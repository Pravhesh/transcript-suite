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
        """
        if self._is_loaded:
            return

        print(f"[Canary-Qwen] Loading {self.model_name} onto {self.device} ({self.dtype})...")
        self.vram_manager.clear_cache()

        try:
            from nemo.collections.speechlm2.models import SALM
            self.model = SALM.from_pretrained(self.model_name)
            if self.device.startswith("cuda") and torch.cuda.is_available():
                self.model = self.model.to(device=self.device, dtype=self.dtype)
            self.model.eval()
            self._is_loaded = True
            print("[Canary-Qwen] Model successfully loaded.")
        except Exception as e:
            print(f"[Canary-Qwen Warning] Failed to load NeMo SALM model directly: {e}")
            print("[Canary-Qwen Warning] Attempting alternative NeMo ASR loading or fallback...")
            try:
                import nemo.collections.asr as nemo_asr
                self.model = nemo_asr.models.EncDecCTCModelBPE.from_pretrained(model_name=self.model_name)
                self.model.eval()
                self._is_loaded = True
            except Exception as e2:
                print(f"[Canary-Qwen Error] Model initialization failed: {e2}")
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
            with torch.inference_mode():
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

    def transcribe_waveform_segments(
        self,
        waveform: torch.Tensor,
        segments: List[any],
        sr: int = 16000,
        progress_callback: Optional[callable] = None
    ) -> List[dict]:
        """
        Transcribes audio segments sequentially with VRAM cache flushing.
        """
        if not self._is_loaded:
            self.load_model()

        results = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            for idx, seg in enumerate(segments):
                # Save slice to temporary wav
                start_sample = max(0, int(seg.start * sr))
                end_sample = min(waveform.shape[1], int(seg.end * sr))
                slice_data = waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy()

                chunk_file = tmp_path / f"chunk_{idx:05d}.wav"
                sf.write(str(chunk_file), slice_data, sr, subtype="PCM_16")

                # Transcribe
                self.vram_manager.assert_safe_headroom()
                text = self.transcribe_chunk(chunk_file)
                seg.text = text

                # Clean temporary file
                chunk_file.unlink(missing_ok=True)

                # Memory cleanup interval
                if idx % config.auto_empty_cache_interval == 0:
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
