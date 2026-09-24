"""
Unit and integration tests for Pseudo-Streaming Ingestion and Unified Chunker.
Tests StreamChunk, PseudoStreamChunker, TranscriptionPipeline.stream_transcribe, and SSE endpoint.
"""

from pathlib import Path
import tempfile
import json
import numpy as np
import soundfile as sf
import torch
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from transcript_suite.audio.chunker import StreamChunk, PseudoStreamChunker, stream_chunks
from transcript_suite.audio.vad import SpeechSegment
from transcript_suite.pipeline import TranscriptionPipeline
from transcript_suite.web.app import app, TASKS, TASK_CONTROLS


def test_stream_chunk_dataclass():
    wf = torch.zeros((1, 16000), dtype=torch.float32)
    chunk = StreamChunk(
        chunk_idx=0,
        start=0.0,
        end=1.0,
        duration=1.0,
        waveform=wf,
        sample_rate=16000,
        is_final=True,
        speaker="Speaker 1"
    )
    d = chunk.to_dict()
    assert d["chunk_idx"] == 0
    assert d["start"] == 0.0
    assert d["end"] == 1.0
    assert d["duration"] == 1.0
    assert d["is_final"] is True
    assert d["speaker"] == "Speaker 1"


def test_chunker_bounds_fallback():
    chunker = PseudoStreamChunker(min_chunk_duration=3.0, max_chunk_duration=10.0, use_vad=False)
    # Total duration 25s should split into [0, 10], [10, 20], [20, 25]
    bounds = chunker.calculate_bounds(total_duration=25.0, raw_segments=None)
    assert len(bounds) == 3
    assert bounds[0] == (0.0, 10.0)
    assert bounds[1] == (10.0, 20.0)
    assert bounds[2] == (20.0, 25.0)


def test_chunker_bounds_vad_coalesce_and_split():
    chunker = PseudoStreamChunker(min_chunk_duration=3.0, max_chunk_duration=10.0)
    # 3 short segments that fit together in <= 10s: [0, 2], [2.5, 5], [6, 8] -> merged into [0, 8]
    # 1 long segment [15, 38] (> 10s) -> split into [15, 25], [25, 35], [35, 38]
    raw_segs = [
        SpeechSegment(start=0.0, end=2.0, duration=2.0),
        SpeechSegment(start=2.5, end=5.0, duration=2.5),
        SpeechSegment(start=6.0, end=8.0, duration=2.0),
        SpeechSegment(start=15.0, end=38.0, duration=23.0),
    ]
    bounds = chunker.calculate_bounds(total_duration=40.0, raw_segments=raw_segs)
    assert len(bounds) >= 3
    assert bounds[0] == (0.0, 8.0)
    # All chunk durations must be <= max_chunk_duration
    for s, e in bounds:
        assert (e - s) <= 10.05


def test_stream_chunks_generator():
    sr = 16000
    duration_s = 6.0
    data = 0.1 * np.sin(2 * np.pi * 440 * np.linspace(0, duration_s, int(sr * duration_s), endpoint=False))
    tensor = torch.from_numpy(data.astype(np.float32)).unsqueeze(0)

    chunker = PseudoStreamChunker(sample_rate=sr, max_chunk_duration=2.5, use_vad=False)
    chunks = list(chunker.stream_chunks(tensor, sample_rate=sr))

    assert len(chunks) == 3  # [0, 2.5], [2.5, 5.0], [5.0, 6.0]
    assert chunks[0].chunk_idx == 0
    assert not chunks[0].is_final
    assert chunks[-1].is_final is True
    assert chunks[-1].chunk_idx == 2
    assert chunks[0].waveform.shape[1] == int(2.5 * sr)


def test_pipeline_stream_transcribe():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wav_file = tmp_path / "test_stream.wav"
        sr = 16000
        data = 0.1 * np.sin(2 * np.pi * 440 * np.linspace(0, 4.0, int(sr * 4.0), endpoint=False))
        sf.write(str(wav_file), data, sr, subtype="PCM_16")

        with patch("transcript_suite.pipeline.SileroVADSegmenter"), \
             patch("transcript_suite.pipeline.CanaryQwenTranscriber") as mock_canary_cls, \
             patch("transcript_suite.pipeline.ModelCouncil"):

            mock_canary = MagicMock()
            mock_canary.transcribe_waveform_chunk.return_value = "Streamed recognition text."
            mock_canary_cls.return_value = mock_canary

            pipeline = TranscriptionPipeline()
            events = list(pipeline.stream_transcribe(
                file_path=wav_file,
                model_choice="canary",
                max_chunk_duration=2.0
            ))

            event_types = [e["event"] for e in events]
            assert "init" in event_types
            assert "chunk_start" in event_types
            assert "chunk_done" in event_types
            assert "complete" in event_types

            complete_ev = next(e for e in events if e["event"] == "complete")
            assert "Streamed recognition text." in complete_ev["data"]["full_text"]
            assert complete_ev["data"]["total_chunks"] >= 1


def test_api_transcribe_stream_sse():
    import transcript_suite.pipeline as pipe_mod
    pipe_mod._active_pipeline = None
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wav_file = tmp_path / "test_api_stream.wav"
        sr = 16000
        data = 0.1 * np.sin(2 * np.pi * 440 * np.linspace(0, 3.0, int(sr * 3.0), endpoint=False))
        sf.write(str(wav_file), data, sr, subtype="PCM_16")

        with patch("transcript_suite.pipeline.SileroVADSegmenter"), \
             patch("transcript_suite.pipeline.CanaryQwenTranscriber") as mock_canary_cls, \
             patch("transcript_suite.pipeline.ModelCouncil"):

            mock_canary = MagicMock()
            mock_canary.transcribe_waveform_chunk.return_value = "Live SSE segment text."
            mock_canary_cls.return_value = mock_canary

            with open(wav_file, "rb") as f:
                res = client.post(
                    "/api/transcribe/stream",
                    files={"audio": ("sample.wav", f, "audio/wav")},
                    data={"model": "canary", "max_chunk_duration": "2.0"}
                )

            assert res.status_code == 200
            assert "text/event-stream" in res.headers["content-type"]
            body = res.text

            assert "event: task_registered" in body
            assert "event: init" in body
            assert "event: chunk_done" in body
            assert "event: complete" in body
            assert "Live SSE segment text." in body
