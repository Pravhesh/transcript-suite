"""
Unit tests for GPU Speech Enhancer and Noise Filter.
"""

import torch
import numpy as np
from transcript_suite.audio.enhancer import GPUSpeechEnhancer

def test_speech_enhancer():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    enhancer = GPUSpeechEnhancer(sample_rate=16000, device=device)

    # 1. Create 2 seconds of synthetic audio:
    # 50 Hz AC rumble + 2200 Hz vocal presence tone + noise
    sr = 16000
    t = torch.linspace(0, 2.0, int(sr * 2.0))
    hum_50hz = 0.4 * torch.sin(2 * np.pi * 50 * t)
    vocal_2200hz = 0.15 * torch.sin(2 * np.pi * 2200 * t)
    raw_audio = (hum_50hz + vocal_2200hz).unsqueeze(0)

    # 2. Run enhancement on GPU
    enhanced = enhancer.enhance(raw_audio)

    assert enhanced.shape == raw_audio.shape
    assert not torch.isnan(enhanced).any()
    assert not torch.isinf(enhanced).any()
    assert enhanced.max() <= 1.0
    assert enhanced.min() >= -1.0

    # 3. Verify low frequencies (<80Hz) are significantly attenuated
    # Simple FFT energy check
    fft_raw = torch.abs(torch.fft.rfft(raw_audio.squeeze(0)))
    fft_enh = torch.abs(torch.fft.rfft(enhanced.squeeze(0).cpu()))
    
    # Bin for 50Hz (freq = bin * sr / n)
    bin_50hz = int(50 * len(t) / sr)
    assert fft_enh[bin_50hz] < fft_raw[bin_50hz] * 0.5, "50Hz low-cut attenuation failed"

    print("✓ GPUSpeechEnhancer successfully filtered low-frequency rumble and normalized audio.")


def test_vocal_amplifier_boost():
    """Verifies that quiet speech receives clean amplification without attenuation."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    enhancer = GPUSpeechEnhancer(sample_rate=16000, device=device)

    sr = 16000
    t = torch.linspace(0, 1.0, sr)
    # Quiet vocal signal (RMS ~ 0.02)
    quiet_voice = 0.03 * torch.sin(2 * np.pi * 500 * t).unsqueeze(0)
    orig_rms = torch.sqrt(torch.mean(quiet_voice.pow(2))).item()

    boosted = enhancer.enhance(quiet_voice, boost_level="high")
    boosted_rms = torch.sqrt(torch.mean(boosted.pow(2))).item()

    # Verify amplification is at least 2.5x (> +8 dB)
    gain = boosted_rms / orig_rms
    assert gain >= 2.5, f"Expected at least 2.5x amplification, got {gain:.2f}x"
    assert boosted.abs().max() <= 0.95, "Peak headroom exceeded"


def test_soft_knee_limiter():
    """Verifies that loud transient peaks are softly compressed and never exceed 0.95."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    enhancer = GPUSpeechEnhancer(sample_rate=16000, device=device)

    sr = 16000
    t = torch.linspace(0, 1.0, sr)
    # Voice with a transient spike
    voice = 0.15 * torch.sin(2 * np.pi * 300 * t)
    voice[sr // 2 : sr // 2 + 50] = 0.98  # transient bump
    raw = voice.unsqueeze(0)

    limited = enhancer.enhance(raw, boost_level="standard")
    peak = limited.abs().max().item()

    assert peak <= 0.95, f"Peak {peak} exceeded 0.95 ceiling"
    assert not torch.isnan(limited).any()


if __name__ == "__main__":
    test_speech_enhancer()
    test_vocal_amplifier_boost()
    test_soft_knee_limiter()

