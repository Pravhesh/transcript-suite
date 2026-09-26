"""
Base Diarization interface and data structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any
import torch


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "speaker": self.speaker
        }


class BaseDiarizer(ABC):
    @abstractmethod
    def diarize(self, waveform: torch.Tensor, sample_rate: int = 16000) -> List[SpeakerTurn]:
        """
        Extracts speaker turns from an audio waveform tensor [1, T].
        Returns a list of SpeakerTurn objects.
        """
        pass
