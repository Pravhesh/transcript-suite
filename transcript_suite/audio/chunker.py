"""
Pseudo-streaming audio chunker.
Partitions audio files or memory waveforms into 5-15s progressive speech windows
for low-latency streaming ingestion and incremental transcription.
"""

from dataclasses import dataclass
from typing import Iterator, List, Dict, Any, Optional, Tuple
from pathlib import Path
import torch

from .loader import load_audio
from .vad import SileroVADSegmenter, SpeechSegment


@dataclass
class StreamChunk:
    chunk_idx: int
    start: float
    end: float
    duration: float
    waveform: torch.Tensor
    sample_rate: int = 16000
    is_final: bool = False
    speaker: str = "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_idx": self.chunk_idx,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "sample_rate": self.sample_rate,
            "is_final": self.is_final,
            "speaker": self.speaker,
        }


class PseudoStreamChunker:
    """
    Partitions audio into progressive speech chunks (5-15s) using Silero-VAD or sliding windows.
    Enables low-latency progressive transcription via SSE.
    """
    def __init__(
        self,
        sample_rate: int = 16000,
        min_chunk_duration: float = 3.0,
        max_chunk_duration: float = 12.0,
        vad_padding: float = 0.25,
        use_vad: bool = True,
        device: Optional[str] = None
    ):
        self.sample_rate = sample_rate
        self.min_chunk_duration = min_chunk_duration
        self.max_chunk_duration = max_chunk_duration
        self.vad_padding = vad_padding
        self.use_vad = use_vad
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._vad: Optional[SileroVADSegmenter] = None

    def _get_vad(self) -> SileroVADSegmenter:
        if self._vad is None:
            self._vad = SileroVADSegmenter(
                sample_rate=self.sample_rate,
                max_chunk_duration=self.max_chunk_duration,
                min_chunk_duration=self.min_chunk_duration,
                padding_duration=self.vad_padding,
                device=self.device
            )
        return self._vad

    def calculate_bounds(
        self,
        total_duration: float,
        raw_segments: Optional[List[SpeechSegment]] = None
    ) -> List[Tuple[float, float]]:
        """
        Calculates optimal [start, end] chunk intervals.
        Groups short VAD segments together up to max_chunk_duration.
        """
        if not raw_segments:
            # Fallback to sliding window if no speech segments detected
            bounds = []
            curr = 0.0
            step = self.max_chunk_duration
            while curr < total_duration:
                end = min(total_duration, curr + step)
                if end - curr >= 0.1:
                    bounds.append((round(curr, 3), round(end, 3)))
                curr = end
            return bounds or [(0.0, round(total_duration, 3))]

        # Split any individual segment exceeding max_chunk_duration
        expanded_segments: List[SpeechSegment] = []
        for seg in raw_segments:
            if seg.duration > self.max_chunk_duration:
                sub_s = seg.start
                while sub_s < seg.end:
                    sub_e = min(seg.end, sub_s + self.max_chunk_duration)
                    if sub_e - sub_s >= 0.1:
                        expanded_segments.append(
                            SpeechSegment(start=sub_s, end=sub_e, duration=sub_e - sub_s)
                        )
                    sub_s = sub_e
            else:
                expanded_segments.append(seg)

        if not expanded_segments:
            return [(0.0, round(total_duration, 3))]

        bounds: List[Tuple[float, float]] = []
        c_start = expanded_segments[0].start
        c_end = expanded_segments[0].end

        for seg in expanded_segments[1:]:
            # If adding this segment fits within max_chunk_duration, coalesce
            if (seg.end - c_start) <= self.max_chunk_duration:
                c_end = seg.end
            else:
                bounds.append((round(c_start, 3), round(c_end, 3)))
                c_start = seg.start
                c_end = seg.end

        bounds.append((round(c_start, 3), round(c_end, 3)))
        return bounds

    def stream_chunks(
        self,
        audio_or_path: str | Path | torch.Tensor,
        sample_rate: Optional[int] = None
    ) -> Iterator[StreamChunk]:
        """
        Yields StreamChunk items sequentially.
        """
        if isinstance(audio_or_path, (str, Path)):
            waveform, sr, duration = load_audio(audio_or_path, target_sr=self.sample_rate)
        else:
            waveform = audio_or_path
            sr = sample_rate or self.sample_rate
            if waveform.ndim == 1:
                waveform = waveform.unsqueeze(0)
            duration = waveform.shape[1] / sr

        if duration <= 0:
            return

        raw_segments = None
        if self.use_vad:
            try:
                vad = self._get_vad()
                raw_segments = vad.segment(waveform, duration)
            except Exception as e:
                print(f"[PseudoStreamChunker Warning] VAD segmentation fallback: {e}")
                raw_segments = None

        bounds = self.calculate_bounds(duration, raw_segments)
        total_chunks = len(bounds)

        for idx, (c_start, c_end) in enumerate(bounds):
            s_idx = max(0, int(c_start * sr))
            e_idx = min(waveform.shape[1], int(c_end * sr))
            chunk_slice = waveform[:, s_idx:e_idx]
            is_final = (idx == total_chunks - 1)

            yield StreamChunk(
                chunk_idx=idx,
                start=c_start,
                end=c_end,
                duration=round(c_end - c_start, 3),
                waveform=chunk_slice,
                sample_rate=sr,
                is_final=is_final
            )


def stream_chunks(
    audio_or_path: str | Path | torch.Tensor,
    sample_rate: int = 16000,
    min_chunk_duration: float = 3.0,
    max_chunk_duration: float = 12.0,
    use_vad: bool = True
) -> Iterator[StreamChunk]:
    """Convenience functional interface for pseudo-streaming audio chunking."""
    chunker = PseudoStreamChunker(
        sample_rate=sample_rate,
        min_chunk_duration=min_chunk_duration,
        max_chunk_duration=max_chunk_duration,
        use_vad=use_vad
    )
    return chunker.stream_chunks(audio_or_path, sample_rate)
