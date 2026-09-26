"""
Voice Activity Detection (VAD) and speech chunking via Silero-VAD.
Safely bounds speech segments to 15-25s for 8GB VRAM safety.
"""

from dataclasses import dataclass
from typing import List, Dict, Any
from pathlib import Path
import torch
import numpy as np

from ..config import config


@dataclass
class SpeechSegment:
    start: float
    end: float
    duration: float
    speaker: str = "Unknown"
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "speaker": self.speaker,
            "text": self.text.strip()
        }


class SileroVADSegmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        max_chunk_duration: float = 25.0,
        min_chunk_duration: float = 1.0,
        padding_duration: float = 0.25,
        device: str = "cuda" if torch.cuda.is_available() else "cpu"
    ):
        self.sample_rate = sample_rate
        self.max_chunk_duration = max_chunk_duration
        self.min_chunk_duration = min_chunk_duration
        self.padding_duration = padding_duration
        self.device = device
        self._model = None
        self._utils = None

    def _load_model(self):
        if self._model is None:
            local_candidates = [
                config.torch_home / "hub" / "snakers4_silero-vad_master",
                Path.home() / ".cache" / "torch" / "hub" / "snakers4_silero-vad_master"
            ]
            loaded = False
            for p in local_candidates:
                if p.exists():
                    try:
                        model, utils = torch.hub.load(
                            repo_or_dir=str(p),
                            model="silero_vad",
                            source="local",
                            force_reload=False,
                            onnx=False
                        )
                        self._model = model.to(self.device)
                        self._model.eval()
                        self._utils = utils
                        loaded = True
                        break
                    except Exception as e:
                        print(f"[VAD Warning] Failed loading local hub repo from {p}: {e}")
            if not loaded:
                model, utils = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    force_reload=False,
                    onnx=False
                )
                self._model = model.to(self.device)
                self._model.eval()
                self._utils = utils

    def segment(self, waveform: torch.Tensor, total_duration: float) -> List[SpeechSegment]:
        """
        Segments audio into speech intervals bounded by max_chunk_duration on GPU.
        """
        try:
            self._load_model()
            (get_speech_timestamps, _, _, _, _) = self._utils

            # Silero expects 1D float tensor on the same device as the model
            audio_1d = waveform.squeeze(0).to(self.device).float()
            speech_timestamps = get_speech_timestamps(
                audio_1d,
                self._model,
                sampling_rate=self.sample_rate,
                min_speech_duration_ms=250,
                min_silence_duration_ms=400,
                return_seconds=True
            )
        except Exception as e:
            # Fallback to simple sliding window if Silero fails to load (e.g. offline)
            print(f"[VAD Warning] Silero-VAD offline or loading error: {e}. Using window chunking.")
            speech_timestamps = []
            curr = 0.0
            step = self.max_chunk_duration
            while curr < total_duration:
                end = min(total_duration, curr + step)
                speech_timestamps.append({"start": curr, "end": end})
                curr = end

        # Refine and enforce chunk size limits
        refined_segments: List[SpeechSegment] = []
        for ts in speech_timestamps:
            start = max(0.0, ts["start"] - self.padding_duration)
            end = min(total_duration, ts["end"] + self.padding_duration)
            duration = end - start

            if duration < self.min_chunk_duration:
                continue

            if duration <= self.max_chunk_duration:
                refined_segments.append(SpeechSegment(start=start, end=end, duration=duration))
            else:
                # Sub-split long speech intervals into chunks <= max_chunk_duration
                sub_start = start
                while sub_start < end:
                    sub_end = min(end, sub_start + self.max_chunk_duration)
                    refined_segments.append(
                        SpeechSegment(start=sub_start, end=sub_end, duration=sub_end - sub_start)
                    )
                    sub_start = sub_end

        return refined_segments

    def unload_model(self):
        """Unloads Silero VAD model and frees associated memory."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._utils is not None:
            del self._utils
            self._utils = None
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

