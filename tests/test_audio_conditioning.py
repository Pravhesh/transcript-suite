"""
Unit and integration tests for:
Feature 1.2: Advanced Pre-Processing Conditioning
 - 1.2.A: EBU R128 LUFS Loudness Normalization & Soft-Knee Dynamic Limiting
 - 1.2.B: SoX VHQ Sinc Resampler Engine (>120dB stopband attenuation)
Feature 1.3: Audio Ingestion & Boundary Refinement
 - 1.3.A: 500ms Sliding Window Chunk Overlap with Boundary Word Deduplication
"""

import tempfile
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
import torch
from fastapi.testclient import TestClient

from transcript_suite.audio.normalizer import (
    apply_k_weighting,
    measure_lufs,
    normalize_lufs,
    apply_soft_knee_limiter,
)
from transcript_suite.audio.resampler import (
    SoxVHQSincResampler,
    resample_sox_vhq,
)
from transcript_suite.audio.chunker import (
    StreamChunk,
    PseudoStreamChunker,
    deduplicate_chunk_boundary,
)
from transcript_suite.audio.loader import (
    AudioLoader,
    diagnose_audio_health,
    load_audio,
)
from transcript_suite.web.app import app


# ---------------------------------------------------------
# Feature 1.2.A: EBU R128 LUFS Normalization & Soft-Knee Limiter
# ---------------------------------------------------------

def test_k_weighting_and_measure_lufs():
    sr = 16000
    t = torch.linspace(0, 3.0, sr * 3)

    # 1. Silence should register below -70 LUFS
    silence = torch.zeros(1, sr * 3)
    lufs_silence = measure_lufs(silence, sr=sr)
    assert lufs_silence <= -70.0, f"Expected silence <= -70 LUFS, got {lufs_silence}"

    # 2. Standard 1 kHz sine tone at 0.5 amplitude (-6 dBFS peak)
    sine = (0.5 * torch.sin(2 * torch.pi * 1000 * t)).unsqueeze(0)
    filtered = apply_k_weighting(sine, sr=sr)
    assert filtered.shape == sine.shape
    # Filtered signal should have valid values
    assert not torch.isnan(filtered).any()

    lufs_sine = measure_lufs(sine, sr=sr)
    # 0.5 peak sine has RMS ~ 0.3535 (-9 dBFS) and K-weighting has slight high shelf gain
    assert -15.0 <= lufs_sine <= -5.0, f"Expected sine LUFS in [-15, -5], got {lufs_sine}"


def test_normalize_lufs_and_peak_limiter():
    sr = 16000
    t = torch.linspace(0, 3.0, sr * 3)
    # Quiet speech-like tone around -30 dBFS
    quiet = (0.03 * torch.sin(2 * torch.pi * 500 * t)).unsqueeze(0)
    initial_lufs = measure_lufs(quiet, sr=sr)
    assert initial_lufs < -25.0

    # Normalize to -16 LUFS
    normed, in_lufs, final_lufs = normalize_lufs(quiet, target_lufs=-16.0, peak_limit_dbfs=-1.0, sr=sr)
    assert in_lufs < -25.0, f"Expected initial LUFS < -25, got {in_lufs}"
    assert abs(final_lufs - (-16.0)) < 1.5, f"Expected final LUFS ~ -16.0, got {final_lufs}"
    # Ensure zero clipping and respect peak limit
    max_peak = torch.max(torch.abs(normed)).item()
    # -1 dBFS is ~0.89125
    assert max_peak <= 0.90, f"Peak exceeded ceiling: {max_peak}"


def test_soft_knee_limiter_extreme_overload():
    # Intentionally create massive overload > +12 dB
    overload = torch.randn(1, 16000) * 5.0
    limited = apply_soft_knee_limiter(overload, peak_limit_dbfs=-1.0)
    max_val = torch.max(torch.abs(limited)).item()
    # Must never exceed -1 dBFS (0.89125)
    assert max_val <= 0.895, f"Limiter allowed signal past ceiling: {max_val}"


# ---------------------------------------------------------
# Feature 1.2.B: SoX VHQ Sinc Resampler Engine
# ---------------------------------------------------------

def test_sox_vhq_resampler_44k_to_16k():
    sr_orig = 44100
    sr_target = 16000
    duration = 1.0
    t = torch.linspace(0, duration, int(sr_orig * duration))
    # 440 Hz tone
    audio_44k = (0.7 * torch.sin(2 * torch.pi * 440 * t)).unsqueeze(0)

    resampler = SoxVHQSincResampler(orig_freq=sr_orig, new_freq=sr_target)
    audio_16k = resampler(audio_44k)

    assert audio_16k.shape[0] == 1
    # Expected length ~ 16000
    assert abs(audio_16k.shape[1] - 16000) <= 10
    # Energy preservation
    orig_rms = torch.sqrt(torch.mean(audio_44k ** 2)).item()
    new_rms = torch.sqrt(torch.mean(audio_16k ** 2)).item()
    assert abs(orig_rms - new_rms) < 0.05, f"RMS energy mismatch: orig={orig_rms}, new={new_rms}"


def test_resample_sox_vhq_identity_and_multi_channel():
    # Identity test: 16k to 16k
    audio = torch.randn(2, 16000)
    resampled = resample_sox_vhq(audio, orig_freq=16000, new_freq=16000)
    assert torch.equal(audio, resampled)


# ---------------------------------------------------------
# Feature 1.3.A: 500ms Sliding Window Chunk Overlap & Dedup
# ---------------------------------------------------------

def test_sliding_window_overlap_chunker():
    chunker = PseudoStreamChunker(
        min_chunk_duration=3.0,
        max_chunk_duration=5.0,
        overlap_duration=0.5,
        use_vad=False
    )
    # Total duration 12s -> should split into ~3 chunks with context margins
    total_duration = 12.0
    sr = 16000
    full_wf = torch.zeros(1, int(sr * total_duration))

    chunks = list(chunker.chunk_generator(full_wf, sample_rate=sr))
    assert len(chunks) >= 2

    # Chunk 0 has right margin of 0.5s into next chunk
    chunk0 = chunks[0]
    assert chunk0.start == 0.0
    assert chunk0.end == 5.0
    assert chunk0.context_start == 0.0
    assert chunk0.context_end == 5.5
    assert chunk0.right_margin_duration == 0.5
    assert chunk0.left_margin_duration == 0.0

    # Chunk 1 has left margin of 0.5s and right margin of 0.5s
    chunk1 = chunks[1]
    assert chunk1.start == 5.0
    assert chunk1.end == 10.0
    assert chunk1.context_start == 4.5
    assert chunk1.context_end == 10.5
    assert chunk1.left_margin_duration == 0.5
    assert chunk1.right_margin_duration == 0.5


def test_boundary_word_deduplication():
    # 1. Exact match duplicate across boundary
    prev_text = "Today we discuss the audio quality"
    curr_text = "audio quality of the new neural models"
    deduped = deduplicate_chunk_boundary(prev_text, curr_text, max_overlap_words=6)
    assert deduped == "of the new neural models", f"Expected 'of the new neural models', got '{deduped}'"

    # 2. Phonetic homophone duplicate across boundary: "their" vs "there"
    prev_text_homo = "They left their luggage at"
    curr_text_homo = "there luggage at the front station"
    deduped_homo = deduplicate_chunk_boundary(prev_text_homo, curr_text_homo, max_overlap_words=6)
    assert deduped_homo == "the front station", f"Expected 'the front station', got '{deduped_homo}'"

    # 3. Disjoint text (no boundary duplication)
    prev_disjoint = "First sentence completely ends here."
    curr_disjoint = "Second sentence begins with fresh words."
    deduped_disjoint = deduplicate_chunk_boundary(prev_disjoint, curr_disjoint, max_overlap_words=6)
    assert deduped_disjoint == curr_disjoint


# ---------------------------------------------------------
# Integration: AudioLoader & Diagnostics Endpoint
# ---------------------------------------------------------

def test_diagnose_audio_health_measures_lufs():
    sr = 16000
    t = torch.linspace(0, 2.0, sr * 2)
    sine = (0.4 * torch.sin(2 * torch.pi * 440 * t)).unsqueeze(0)

    clean_mono, report = diagnose_audio_health(sine, sr=sr)
    assert report.lufs is not None
    assert -25.0 <= report.lufs <= -5.0
    assert report.target_lufs == -16.0


def test_api_diagnostics_endpoint_returns_lufs():
    client = TestClient(app)
    with tempfile.NamedTemporaryFile(suffix=".wav") as tf:
        sr = 16000
        t = np.linspace(0, 1.0, sr)
        audio = 0.5 * np.sin(2 * np.pi * 440 * t)
        sf.write(tf.name, audio, sr, subtype="PCM_16")

        with open(tf.name, "rb") as af:
            resp = client.post("/api/audio/diagnostics", files={"audio": ("test.wav", af, "audio/wav")})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "health" in data
        assert "lufs" in data["health"]
        assert isinstance(data["health"]["lufs"], (int, float))
