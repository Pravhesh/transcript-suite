"""
Route B (Optional): PyAnnote Audio 3.x Speaker Diarization.
Requires pyannote.audio package and a valid Hugging Face token (HF_TOKEN).
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional
import torch
import soundfile as sf
from .base import BaseDiarizer, SpeakerTurn


class PyAnnoteDiarizer(BaseDiarizer):
    def __init__(self, hf_token: Optional[str] = None, device: str = "cuda" if torch.cuda.is_available() else "cpu"):
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.device = device
        self.pipeline = None
        self._is_loaded = False

    def _load_pipeline(self):
        if self._is_loaded:
            return

        if not self.hf_token:
            raise ValueError(
                "PyAnnote requires a Hugging Face token. Provide it via HF_TOKEN environment variable "
                "or pass it via --hf-token argument."
            )

        try:
            from pyannote.audio import Pipeline
            self.pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token
            )
            if self.device.startswith("cuda") and torch.cuda.is_available():
                self.pipeline.to(torch.device(self.device))
            self._is_loaded = True
            print("[PyAnnote] Pipeline successfully loaded.")
        except ImportError:
            raise ImportError("pyannote.audio is not installed. Install via `uv pip install pyannote.audio`")
        except Exception as e:
            raise RuntimeError(f"Failed to load PyAnnote pipeline: {e}")

    def diarize(self, waveform: torch.Tensor, sample_rate: int = 16000) -> List[SpeakerTurn]:
        """
        Runs pyannote speaker diarization on audio waveform.
        """
        self._load_pipeline()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            wav_path = tmp.name
            audio_np = waveform.squeeze(0).cpu().numpy()
            sf.write(wav_path, audio_np, sample_rate, subtype="PCM_16")

            diarization_result = self.pipeline(wav_path)

        speaker_map = {}
        speaker_turns: List[SpeakerTurn] = []

        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            if speaker not in speaker_map:
                speaker_map[speaker] = f"Speaker {len(speaker_map)}"
            normalized_spk = speaker_map[speaker]
            speaker_turns.append(SpeakerTurn(start=turn.start, end=turn.end, speaker=normalized_spk))

        return speaker_turns
