"""
Pseudo-streaming audio chunker.
Partitions audio files or memory waveforms into 5-15s progressive speech windows
for low-latency streaming ingestion and incremental transcription.
"""

from dataclasses import dataclass
from typing import Iterator, List, Dict, Any, Optional, Tuple
from pathlib import Path
import torch

import re
import difflib

try:
    from ..asr.phonetics import are_homophones
except Exception:
    def are_homophones(w1: str, w2: str) -> bool:
        return False

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
    context_start: float = 0.0
    context_end: float = 0.0
    overlap_left: float = 0.0
    overlap_right: float = 0.0

    @property
    def left_margin_duration(self) -> float:
        return self.overlap_left

    @property
    def right_margin_duration(self) -> float:
        return self.overlap_right

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_idx": self.chunk_idx,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "sample_rate": self.sample_rate,
            "is_final": self.is_final,
            "speaker": self.speaker,
            "context_start": round(self.context_start, 3),
            "context_end": round(self.context_end, 3),
            "overlap_left": round(self.overlap_left, 3),
            "overlap_right": round(self.overlap_right, 3),
        }


class PseudoStreamChunker:
    """
    Partitions audio into progressive speech chunks (5-15s) with 500ms sliding window overlap (1.3.A).
    Enables low-latency progressive transcription via SSE without audio clipping at chunk boundaries.
    """
    def __init__(
        self,
        sample_rate: int = 16000,
        min_chunk_duration: float = 3.0,
        max_chunk_duration: float = 12.0,
        vad_padding: float = 0.25,
        overlap_duration: float = 0.0,
        use_vad: bool = True,
        device: Optional[str] = None
    ):
        self.sample_rate = sample_rate
        self.min_chunk_duration = min_chunk_duration
        self.max_chunk_duration = max_chunk_duration
        self.vad_padding = vad_padding
        self.overlap_duration = overlap_duration
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
            # 1.3.A: 500ms Sliding Window Context Padding across chunk boundaries
            audio_start = max(0.0, c_start - (self.overlap_duration if idx > 0 else 0.0))
            audio_end = min(duration, c_end + (self.overlap_duration if idx < total_chunks - 1 else 0.0))

            s_idx = max(0, int(audio_start * sr))
            e_idx = min(waveform.shape[1], int(audio_end * sr))
            chunk_slice = waveform[:, s_idx:e_idx]
            is_final = (idx == total_chunks - 1)

            yield StreamChunk(
                chunk_idx=idx,
                start=c_start,
                end=c_end,
                duration=round(c_end - c_start, 3),
                waveform=chunk_slice,
                sample_rate=sr,
                is_final=is_final,
                context_start=round(audio_start, 3),
                context_end=round(audio_end, 3),
                overlap_left=round(c_start - audio_start, 3),
                overlap_right=round(audio_end - c_end, 3)
            )

    # Ergonomic alias
    chunk_generator = stream_chunks


def deduplicate_chunk_boundary(
    prev_text: str,
    curr_text: str,
    max_words: int = 8,
    similarity_threshold: float = 0.80,
    max_overlap_words: Optional[int] = None
) -> str:
    """
    Deduplicates overlapping boundary words between adjacent continuous speech chunks (Feature 1.3.A).
    Compares tail words of prev_text with head words of curr_text.
    Supports exact token matches, phonetic homophone matching, and fuzzy matches.
    Returns curr_text stripped of redundant prefix words.
    """
    limit = max_overlap_words if max_overlap_words is not None else max_words
    prev_words = prev_text.strip().split()
    curr_words = curr_text.strip().split()
    if not prev_words or not curr_words:
        return curr_text.strip()

    def _norm(w: str) -> str:
        return re.sub(r"[^\w]", "", w).lower()

    prev_norm = [_norm(w) for w in prev_words]
    curr_norm = [_norm(w) for w in curr_words]

    if not any(prev_norm) or not any(curr_norm):
        return curr_text.strip()

    max_check = min(len(prev_norm), len(curr_norm), limit)
    best_overlap = 0

    for k in range(max_check, 0, -1):
        prev_slice = prev_norm[-k:]
        curr_slice = curr_norm[:k]

        if prev_slice == curr_slice:
            best_overlap = k
            break

        # Check for phonetic homophones and minor ASR spelling differences
        matches = 0
        for p_w, c_w in zip(prev_slice, curr_slice):
            if p_w == c_w or are_homophones(p_w, c_w):
                matches += 1
            elif difflib.SequenceMatcher(None, p_w, c_w).ratio() >= similarity_threshold:
                matches += 1
        if matches == k:
            best_overlap = k
            break

    if best_overlap > 0:
        remaining_words = curr_words[best_overlap:]
        return " ".join(remaining_words).strip()

    return curr_text.strip()


def stream_chunks(
    audio_or_path: str | Path | torch.Tensor,
    sample_rate: int = 16000,
    min_chunk_duration: float = 3.0,
    max_chunk_duration: float = 12.0,
    overlap_duration: float = 0.5,
    use_vad: bool = True
) -> Iterator[StreamChunk]:
    """Convenience functional interface for pseudo-streaming audio chunking with 500ms overlap (1.3.A)."""
    chunker = PseudoStreamChunker(
        sample_rate=sample_rate,
        min_chunk_duration=min_chunk_duration,
        max_chunk_duration=max_chunk_duration,
        overlap_duration=overlap_duration,
        use_vad=use_vad
    )
    return chunker.stream_chunks(audio_or_path, sample_rate)
