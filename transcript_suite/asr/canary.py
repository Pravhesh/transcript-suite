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


class CanaryQwenTranscriber:
    def __init__(
        self,
        model_name: str = "nvidia/canary-qwen-2.5b",
        device: Optional[str] = None,
        dtype: Optional[torch.dtype] = None
    ):
        self.model_name = model_name
        self.device = device or config.device
        self.dtype = dtype or config.dtype
        self.vram_manager = VRAMManager(warning_threshold_gb=config.vram_alert_threshold_gb)
        self.model = None
        self._is_loaded = False

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

                # 5. Move any remaining meta buffers to target device
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
                        self.model = self.model.to(device=self.device)
                    self.model.eval()
                    self._is_loaded = True
                    self.vram_manager.clear_cache()
                except Exception as e3:
                    print(f"[Canary-Qwen Error] Model initialization failed: {e3}")
                    raise e


    def transcribe_chunk(self, wav_path: str | Path) -> str:
        """
        Transcribes a single audio chunk (WAV 16kHz mono).
        """
        if not self._is_loaded:
            self.load_model()

        wav_path_str = str(Path(wav_path).resolve())

        # If using NeMo SALM
        if hasattr(self.model, "audio_locator_tag"):
            prompt = [[{
                "role": "user",
                "content": f"Transcribe the following: {self.model.audio_locator_tag}",
                "audio": [wav_path_str]
            }]]
            autocast_device = "cuda" if self.device.startswith("cuda") and torch.cuda.is_available() else "cpu"
            with torch.inference_mode(), torch.autocast(device_type=autocast_device, dtype=self.dtype if autocast_device == "cuda" else torch.float32):
                answer_ids = self.model.generate(prompts=prompt, max_new_tokens=256)
                if hasattr(self.model, "tokenizer"):
                    text = self.model.tokenizer.ids_to_text(answer_ids[0].cpu())
                else:
                    text = str(answer_ids)
            return text.strip()

        # Fallback for standard NeMo ASR transcribe
        if hasattr(self.model, "transcribe"):
            with torch.inference_mode():
                results = self.model.transcribe([wav_path_str])
                if isinstance(results, list) and len(results) > 0:
                    return str(results[0]).strip()
                return str(results).strip()

        raise RuntimeError("Loaded model does not support transcription generation.")

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
        progress_callback: Optional[callable] = None,
        pause_event: Optional[any] = None,
        stop_event: Optional[any] = None
    ) -> List[dict]:
        """
        Transcribes audio segments sequentially with VRAM cache flushing and pause/stop support.
        """
        if not self._is_loaded:
            self.load_model()

        results = []
        import time

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

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

                # Save slice to temporary wav
                start_sample = max(0, int(seg.start * sr))
                end_sample = min(waveform.shape[1], int(seg.end * sr))
                slice_data = waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy()

                chunk_file = tmp_path / f"chunk_{idx:05d}.wav"
                sf.write(str(chunk_file), slice_data, sr, subtype="PCM_16")

                # Transcribe
                self.vram_manager.assert_safe_headroom()
                try:
                    text = self.transcribe_chunk(chunk_file)
                except Exception as e:
                    print(f"[Canary-Qwen Warning] Chunk {idx} transcription error: {e}")
                    text = ""

                seg.text = text

                # Clean temporary file
                chunk_file.unlink(missing_ok=True)

                # Memory cleanup interval (aggressive)
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

