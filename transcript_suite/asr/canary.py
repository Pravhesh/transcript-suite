"""
Canary-Qwen-2.5B ASR Engine wrapper.
Implements chunked inference using NeMo SALM with proactive VRAM protection.
"""

from pathlib import Path
import tempfile
from typing import List, Optional
import torch
import soundfile as sf
from .memory import VRAMManager
from ..config import config
import re

PROMPT_LEAK_PATTERNS = [
    r"^\s*transcri(pt|be|ption)(\s+(the|following|text|all|into|and|in|this|what|audio|box).*)?\.?\s*$",
    r"^\s*put it in the box.*$",
    r"^\s*transcribe\s*:?\s*$",
    r"^\s*transcript\s*:?\s*$",
    r"^\s*transcription\s*:?\s*$",
    r"^\s*please transcribe.*$",
    r"^\s*transcribe the following.*$",
    r"^\s*<\|.*?\|>\s*$",
    r"^\s*thank you for watching.*$",
    r"^\s*we('ll| will) be right back.*$"
]
_PROMPT_REGEXES = [re.compile(p, re.IGNORECASE) for p in PROMPT_LEAK_PATTERNS]


def has_consecutive_repetition(words: List[str], n: int = 2, min_repeats: int = 3) -> bool:
    """Check if an n-gram repeats consecutively min_repeats or more times."""
    if len(words) < n * min_repeats:
        return False
    ngram_strs = [" ".join(words[i:i+n]).lower().strip(".,!?") for i in range(len(words) - n + 1)]
    streak = 1
    for i in range(n, len(ngram_strs), n):
        if ngram_strs[i] == ngram_strs[i - n]:
            streak += 1
            if streak >= min_repeats:
                return True
        else:
            streak = 1
    return False


def sanitize_canary_output(text: str, chunk_waveform: Optional[torch.Tensor] = None) -> str:
    """
    Sanitizes Canary-Qwen output by detecting and neutralizing:
    1. Conditioning prompt leakage (e.g. 'Transcript', 'Transcript the following text').
    2. Silence/noise hallucinations when RMS energy is near zero.
    3. Severe single-word or short-phrase repetition loops.
    """
    if not text:
        return ""
    cleaned = text.strip()

    # Strip conditioning prefixes if model echoed its instruction (e.g. 'Vocabulary glossary: ...', 'Transcript: ...')
    cleaned = re.sub(r"^(?:Vocabulary glossary|Use this glossary):.*?(?:\n|\.\s+)", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^(?:Transcribe(?: the following| following| audio)?|Transcript|Transcription)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return ""

    # 1. Prompt Leak Match
    for rx in _PROMPT_REGEXES:
        if rx.match(cleaned):
            return ""

    words = cleaned.split()
    # If phrase is just 'transcript' or 'transcribe' or 'transcription' (<= 2 words), it is conditioning leakage
    if len(words) <= 2 and words and words[0].lower().strip(".,:;!?") in {"transcribe", "transcript", "transcription"}:
        return ""

    # 2. Severe repetition loops
    if len(words) >= 4:
        from collections import Counter
        # Single-word domination (e.g. 'the the the the the' or 60%+ identical token)
        most_common_word_count = Counter(w.lower().strip(".,!?") for w in words).most_common(1)[0][1]
        if most_common_word_count / len(words) > 0.60:
            return ""
        # Consecutive n-gram loops (e.g. 1-gram 4x, 2-gram 3x, 3-gram 3x)
        if (
            has_consecutive_repetition(words, n=1, min_repeats=4)
            or has_consecutive_repetition(words, n=2, min_repeats=3)
            or has_consecutive_repetition(words, n=3, min_repeats=3)
        ):
            return ""

    # 3. RMS energy threshold check on silence/background flutter
    if chunk_waveform is not None:
        try:
            rms = torch.sqrt(torch.mean(chunk_waveform.float() ** 2)).item()
            if rms < 0.003 and len(words) <= 2:
                # Digital silence or extremely faint room tone with stray filler token
                if cleaned.lower().strip(".,!?") in {"you", "yeah", "transcript", "transcribe", "we", "the", "a", "it", "so", "oh"}:
                    return ""
        except Exception:
            pass

    return cleaned



class CanaryQwenTranscriber:
    def __init__(
        self,
        model_name: str = "nvidia/canary-qwen-2.5b",
        device: Optional[str] = None,
        dtype: Optional[torch.dtype] = None,
        attention_backend: Optional[str] = None,
        glossary: Optional[List[str]] = None
    ):
        self.model_name = model_name
        self.device = device or config.device
        self.dtype = dtype or config.dtype
        self.attention_backend = (attention_backend or getattr(config, "attention_backend", "sdpa")).lower()
        self.glossary = list(glossary if glossary is not None else getattr(config, "custom_glossary", []))
        self.vram_manager = VRAMManager(warning_threshold_gb=config.vram_alert_threshold_gb)
        self.model = None
        self._is_loaded = False

    def get_sdp_context(self):
        """Context manager for PyTorch scaled dot-product attention kernels (SDPA)."""
        from contextlib import nullcontext
        if "cuda" in str(self.device) and torch.cuda.is_available() and hasattr(torch.backends.cuda, "sdp_kernel"):
            if self.attention_backend in ("sdpa", "flash_attention_2"):
                return torch.backends.cuda.sdp_kernel(enable_flash=True, enable_math=False, enable_mem_efficient=True)
            elif self.attention_backend == "eager":
                return torch.backends.cuda.sdp_kernel(enable_flash=False, enable_math=True, enable_mem_efficient=False)
        return nullcontext()

    def load_model(self):
        """
        Loads the Canary-Qwen-2.5B SALM model into GPU memory.
        Uses accelerate.init_empty_weights and direct safetensors streaming to cuda:0
        to completely eliminate the 10-15 GB CPU-RAM spike.
        """
        if self._is_loaded:
            return

        print(f"[Canary-Qwen] Loading {self.model_name} onto {self.device} ({self.dtype})...")
        self.vram_manager.clear_cache()

        try:
            if self.device.startswith("cuda") and torch.cuda.is_available():
                from accelerate import init_empty_weights
                from accelerate.utils import set_module_tensor_to_device
                from safetensors import safe_open
                from huggingface_hub import hf_hub_download
                from transformers.utils.hub import cached_file
                from omegaconf import OmegaConf
                from nemo.collections.speechlm2.models import SALM
                from nemo.collections.speechlm2.parts.hf_hub import _inject_local_artifact_paths, CONFIG_NAME

                # 1. Fetch config without instantiating weights
                _cached_file_kwargs = dict(
                    cache_dir=None,
                    force_download=False,
                    local_files_only=False,
                    token=None,
                    revision=None,
                    _raise_exceptions_for_gated_repo=False,
                    _raise_exceptions_for_missing_entries=False,
                    _raise_exceptions_for_connection_errors=False,
                )
                cfg_file = cached_file(self.model_name, CONFIG_NAME, **_cached_file_kwargs)
                cfg = OmegaConf.to_container(OmegaConf.load(cfg_file))
                _inject_local_artifact_paths(cfg, self.model_name, _cached_file_kwargs)
                cfg['pretrained_weights'] = False

                # 2. Instantiate on meta device (0 MB RAM)
                with init_empty_weights():
                    self.model = SALM(cfg)

                # 3. Stream safetensors directly into CUDA VRAM in bfloat16
                weights_path = hf_hub_download(repo_id=self.model_name, filename='model.safetensors')
                with safe_open(weights_path, framework='pt', device='cpu') as f:
                    for param_name in f.keys():
                        tensor = f.get_tensor(param_name)
                        set_module_tensor_to_device(
                            self.model,
                            param_name,
                            device=self.device,
                            value=tensor,
                            dtype=self.dtype
                        )

                # 4. Tie tied weights
                if hasattr(self.model.llm, 'base_model') and hasattr(self.model.llm.base_model, 'model'):
                    self.model.llm.base_model.model.lm_head.weight = self.model.embed_tokens.weight
                elif hasattr(self.model.llm, 'model'):
                    self.model.llm.lm_head.weight = self.model.embed_tokens.weight

                # 5. Restore Conformer relative positional encodings
                # Since init_empty_weights creates non-persistent buffers on the meta device,
                # we must recreate the sinusoidal positional encodings table rather than zeroing it out.
                if hasattr(self.model, "perception") and hasattr(self.model.perception, "encoder"):
                    encoder = self.model.perception.encoder
                    if hasattr(encoder, "pos_enc"):
                        length = getattr(encoder.pos_enc, "max_len", 5000)
                        positions = torch.arange(
                            length - 1, -length, -1, dtype=torch.float32, device=self.device
                        ).unsqueeze(1)
                        encoder.pos_enc.create_pe(positions=positions, dtype=self.dtype)

                # 6. Move any remaining meta buffers to target device
                for name, buf in list(self.model.named_buffers()):
                    if buf.device.type == 'meta':
                        set_module_tensor_to_device(
                            self.model,
                            name,
                            device=self.device,
                            value=torch.zeros(buf.shape, dtype=self.dtype, device=self.device)
                        )

                self.model = self.model.to(self.device)
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            else:
                from nemo.collections.speechlm2.models import SALM
                self.model = SALM.from_pretrained(self.model_name)
                self.model = self.model.to(device=self.device)

            self.model.eval()
            self._is_loaded = True
            self.vram_manager.clear_cache()
            print(f"[Canary-Qwen] Model successfully loaded directly on {self.device} ({self.dtype}) without RAM spike.")
        except Exception as e:
            print(f"[Canary-Qwen Warning] Direct streaming failed ({e}), falling back to standard loader...")
            try:
                from nemo.collections.speechlm2.models import SALM
                kwargs = {}
                if self.device.startswith("cuda") and torch.cuda.is_available():
                    kwargs["map_location"] = self.device
                    kwargs["torch_dtype"] = self.dtype
                self.model = SALM.from_pretrained(self.model_name, **kwargs)
                if self.device.startswith("cuda") and torch.cuda.is_available():
                    self.model = self.model.to(device=self.device, dtype=self.dtype)
                self.model.eval()
                self._is_loaded = True
                self.vram_manager.clear_cache()
            except Exception as e2:
                print(f"[Canary-Qwen Warning] Failed to load NeMo SALM model: {e2}")
                try:
                    import nemo.collections.asr as nemo_asr
                    fallback_kwargs = {}
                    if self.device.startswith("cuda") and torch.cuda.is_available():
                        fallback_kwargs["map_location"] = torch.device(self.device)
                    self.model = nemo_asr.models.EncDecCTCModelBPE.from_pretrained(model_name=self.model_name, **fallback_kwargs)
                    if self.device.startswith("cuda") and torch.cuda.is_available():
                        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                        self.model = self.model.to(device=self.device, dtype=dtype)
                    self.model.eval()
                    self._is_loaded = True
                    self.vram_manager.clear_cache()
                except Exception as e3:
                    print(f"[Canary-Qwen Error] Model initialization failed: {e3}")
                    raise e


    def transcribe_chunk(self, wav_path: str | Path, glossary: Optional[List[str]] = None) -> str:
        """
        Transcribes a single audio chunk (WAV 16kHz mono).
        """
        if not self._is_loaded:
            self.load_model()

        wav_path_str = str(Path(wav_path).resolve())
        active_glossary = glossary if glossary is not None else self.glossary

        # If using NeMo SALM
        if hasattr(self.model, "audio_locator_tag"):
            if active_glossary:
                content = f"Vocabulary glossary: {', '.join(active_glossary)}.\nTranscribe the following: {self.model.audio_locator_tag}"
            else:
                content = f"Transcribe the following: {self.model.audio_locator_tag}"
            prompt = [[{
                "role": "user",
                "content": content,
                "audio": [wav_path_str]
            }]]
            autocast_device = "cuda" if self.device.startswith("cuda") and torch.cuda.is_available() else "cpu"
            with torch.inference_mode(), self.get_sdp_context(), torch.autocast(device_type=autocast_device, dtype=self.dtype if autocast_device == "cuda" else torch.float32):
                answer_ids = self.model.generate(prompts=prompt, max_new_tokens=256)
                if hasattr(self.model, "tokenizer"):
                    text = self.model.tokenizer.ids_to_text(answer_ids[0].cpu())
                else:
                    text = str(answer_ids)
            return sanitize_canary_output(text)

        # Fallback for standard NeMo ASR transcribe
        if hasattr(self.model, "transcribe"):
            with torch.inference_mode(), self.get_sdp_context():
                results = self.model.transcribe([wav_path_str])
                if isinstance(results, list) and len(results) > 0:
                    return sanitize_canary_output(str(results[0]))
                return sanitize_canary_output(str(results))

        raise RuntimeError("Loaded model does not support transcription generation.")

    def transcribe_waveform_chunk(
        self,
        chunk_waveform: torch.Tensor,
        sr: int = 16000,
        glossary: Optional[List[str]] = None
    ) -> str:
        """
        Transcribes an audio chunk directly from a PyTorch tensor in memory.
        Bypasses disk I/O, temporary WAV files, and Lhotse audio reloading.
        """
        if not self._is_loaded:
            self.load_model()

        active_glossary = glossary if glossary is not None else self.glossary

        if hasattr(self.model, "audio_locator_tag"):
            if active_glossary:
                content = f"Vocabulary glossary: {', '.join(active_glossary)}.\nTranscribe the following: {self.model.audio_locator_tag}"
            else:
                content = f"Transcribe the following: {self.model.audio_locator_tag}"
            prompt = [[{
                "role": "user",
                "content": content
            }]]
            # Audio input for perception: ensure mono 2D tensor (1, samples) in float32
            wf = chunk_waveform
            if wf.ndim == 2:
                if wf.shape[0] > 1:
                    wf = torch.mean(wf, dim=0, keepdim=True)
            elif wf.ndim == 1:
                wf = wf.unsqueeze(0)
            audio_tensor = wf.to(device=self.device, dtype=torch.float32)
            audio_lens = torch.tensor([audio_tensor.shape[1]], dtype=torch.int64, device=self.device)

            autocast_device = "cuda" if self.device.startswith("cuda") and torch.cuda.is_available() else "cpu"
            with torch.inference_mode(), self.get_sdp_context(), torch.autocast(device_type=autocast_device, dtype=self.dtype if autocast_device == "cuda" else torch.float32):
                answer_ids = self.model.generate(
                    prompts=prompt,
                    audios=audio_tensor,
                    audio_lens=audio_lens,
                    max_new_tokens=256
                )
                if hasattr(self.model, "tokenizer"):
                    text = self.model.tokenizer.ids_to_text(answer_ids[0].cpu())
                else:
                    text = str(answer_ids)
            return sanitize_canary_output(text, chunk_waveform=chunk_waveform)

        # Fallback if model doesn't support audio tensor input directly
        import tempfile
        import soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_data = chunk_waveform.squeeze(0).cpu().numpy()
            sf.write(tf.name, wav_data, sr, subtype="PCM_16")
            res = self.transcribe_chunk(tf.name, glossary=active_glossary)
            Path(tf.name).unlink(missing_ok=True)
            return sanitize_canary_output(res, chunk_waveform=chunk_waveform)

    def unload_model(self):
        """
        Unloads Canary-Qwen model and releases all VRAM and system RAM.
        """
        if self.model is not None:
            del self.model
            self.model = None
        self._is_loaded = False
        self.vram_manager.clear_cache()
        print("[Canary-Qwen] Model unloaded and memory trimmed.")

    def transcribe_waveform_segments(
        self,
        waveform: torch.Tensor,
        segments: List[any],
        sr: int = 16000,
        glossary: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
        pause_event: Optional[any] = None,
        stop_event: Optional[any] = None
    ) -> List[dict]:
        """
        Transcribes audio segments sequentially with in-memory tensor slices,
        VRAM cache flushing, and pause/stop support.
        """
        if not self._is_loaded:
            self.load_model()

        results = []
        import time

        for idx, seg in enumerate(segments):
            # 1. Check for Stop / Cancel
            if stop_event and stop_event.is_set():
                raise InterruptedError("Transcription stopped by user.")

            # 2. Check for Pause
            if pause_event:
                while not pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        raise InterruptedError("Transcription stopped by user.")
                    time.sleep(0.2)

            # In-memory slice — NO DISK WRITE
            start_sample = max(0, int(seg.start * sr))
            end_sample = min(waveform.shape[1], int(seg.end * sr))
            chunk_slice = waveform[:, start_sample:end_sample]

            # Transcribe directly in memory
            try:
                text = self.transcribe_waveform_chunk(chunk_slice, sr=sr, glossary=glossary)
            except Exception as e:
                # Fallback to disk WAV slice only if direct tensor generation encounters an issue
                try:
                    import tempfile
                    import soundfile as sf
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        sf.write(tf.name, chunk_slice.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                        text = self.transcribe_chunk(tf.name, glossary=glossary)
                        Path(tf.name).unlink(missing_ok=True)
                except Exception as e2:
                    print(f"[Canary-Qwen Warning] Chunk {idx} error: {e2}")
                    text = ""

            seg.text = text

            # Memory cleanup every 10 chunks to prevent memory buildup
            if (idx + 1) % 10 == 0:
                self.vram_manager.clear_cache()

            seg_dict = seg.to_dict() if hasattr(seg, "to_dict") else {
                "start": seg.start,
                "end": seg.end,
                "duration": seg.duration,
                "speaker": getattr(seg, "speaker", "Speaker 0"),
                "text": text
            }
            results.append(seg_dict)

            if progress_callback:
                progress_callback(idx + 1, len(segments), seg_dict)

        self.vram_manager.clear_cache()
        return results


