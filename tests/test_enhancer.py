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

if __name__ == "__main__":
    test_speech_enhancer()
