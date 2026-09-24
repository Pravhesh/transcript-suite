"""
Unit tests for Feature 1.1.A (Pre-Flight Audio Health Badge)
and Feature 1.1.B (Stereo Phase Inversion & Auto-Remix).
"""

import tempfile
from pathlib import Path
import torch
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from transcript_suite.audio.loader import (
    AudioLoader,
    AudioHealthReport,
    remix_and_align_channels,
    diagnose_audio_health,
    estimate_snr_db
)
from transcript_suite.web.app import app


def test_stereo_phase_inversion_remedies_cancellation():
    """1.1.B: Tests that out-of-phase stereo is detected and flipped to prevent cancellation."""
    sr = 16000
    t = torch.linspace(0, 1.0, sr)
    sine = 0.5 * torch.sin(2 * torch.pi * 440 * t)
    # Inverted stereo channels: L and R are 180 degrees out of phase
    inverted_stereo = torch.stack([sine, -sine])

    # Uncorrected naive downmix produces absolute silence
    naive_mono = (inverted_stereo[0] + inverted_stereo[1]) / 2.0
    assert torch.max(torch.abs(naive_mono)).item() < 1e-6, "Expected naive downmix to cancel to 0"

    # Intelligent Auto-Remixing
    mono, channels, corr, inverted, dead, recs = remix_and_align_channels(inverted_stereo)
    assert channels == 2
    assert corr < -0.9, f"Expected correlation < -0.9, got {corr}"
    assert inverted is True, "Expected phase_inverted flag to be True"
    # Remedied mono signal has full amplitude
    rem_peak = torch.max(torch.abs(mono)).item()
    assert rem_peak > 0.45, f"Expected recovered signal peak ~0.5, got {rem_peak}"
    assert any("Inverted Right channel phase" in r for r in recs)
    print("✓ Stereo phase inversion & anti-cancellation verified.")


def test_dead_channel_bypass():
    """1.1.B: Tests that a single dead/silent mic channel on a stereo track is cleanly bypassed."""
    sr = 16000
    t = torch.linspace(0, 1.0, sr)
    sine = 0.6 * torch.sin(2 * torch.pi * 440 * t)
    silent_channel = torch.zeros(sr)
    stereo_dead_right = torch.stack([sine, silent_channel])

    mono, channels, corr, inverted, dead, recs = remix_and_align_channels(stereo_dead_right)
    assert dead is True
    assert torch.max(torch.abs(mono)).item() > 0.55
    assert any("Bypassed silent Right channel" in r for r in recs)
    print("✓ Dead channel detection & bypass verified.")


def test_audio_health_diagnostics_snr_clipping_dc():
    """1.1.A: Tests pre-flight diagnostics estimating SNR, clipping %, DC offset and grading."""
    sr = 16000
    t = torch.linspace(0, 2.0, sr * 2)
    sine = 0.5 * torch.sin(2 * torch.pi * 440 * t)

    # 1. Clean audio with pauses
    speech_sim = torch.cat([sine[:8000], torch.randn(8000) * 0.005, sine[8000:16000], torch.randn(16000) * 0.005]).unsqueeze(0)
    clean_mono, rep_clean = diagnose_audio_health(speech_sim, sr=sr)
    assert rep_clean.snr_db >= 20.0
    assert rep_clean.clipping_pct == 0.0
    assert rep_clean.dc_offset < 0.005
    assert rep_clean.health_grade == "EXCELLENT"

    # 2. Clipped audio with severe DC bias
    dirty = (3.0 * sine + 0.08).clamp(-1.0, 1.0).unsqueeze(0)
    clean_dirty, rep_dirty = diagnose_audio_health(dirty, sr=sr)
    assert rep_dirty.clipping_pct > 5.0
    assert rep_dirty.dc_offset > 0.02
    assert rep_dirty.health_grade in ("FAIR", "POOR")
    assert any("clipped samples" in r for r in rep_dirty.recommendations)
    assert any("Removed mains DC offset" in r for r in rep_dirty.recommendations)

    # Verify DC offset was subtracted from clean_dirty tensor
    assert abs(torch.mean(clean_dirty).item()) < 0.005
    print("✓ Pre-flight health diagnostics (SNR, Clipping, DC offset) verified.")


def test_api_audio_diagnostics_endpoint():
    """Tests the POST /api/audio/diagnostics endpoint with TestClient."""
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
        h = data["health"]
        assert "snr_db" in h
        assert "clipping_pct" in h
        assert "dc_offset" in h
        assert "phase_correlation" in h
        assert "health_grade" in h
        assert h["health_grade"] in ("EXCELLENT", "GOOD", "FAIR", "POOR")
        print("✓ POST /api/audio/diagnostics endpoint verified.")


if __name__ == "__main__":
    test_stereo_phase_inversion_remedies_cancellation()
    test_dead_channel_bypass()
    test_audio_health_diagnostics_snr_clipping_dc()
    test_api_audio_diagnostics_endpoint()
    print("\nAll Pre-Flight Health (1.1.A) & Phase Inversion (1.1.B) unit tests passed!")
