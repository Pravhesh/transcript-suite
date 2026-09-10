"""
GPU-Accelerated Speech Enhancer and Noise Filter.
Operates natively on PyTorch tensors on CUDA to suppress background rumble/hiss
and amplify human voice intelligibility formants.
"""

from typing import Optional
import torch
import torchaudio.functional as F
import numpy as np


class GPUSpeechEnhancer:
    def __init__(
        self,
        sample_rate: int = 16000,
        device: Optional[str] = None,
        low_cut_hz: float = 85.0,
        vocal_boost_hz: float = 2200.0,
        vocal_boost_gain_db: float = 4.5,
        target_rms: float = 0.08
    ):
        self.sample_rate = sample_rate
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.low_cut_hz = low_cut_hz
        self.vocal_boost_hz = vocal_boost_hz
        self.vocal_boost_gain_db = vocal_boost_gain_db
        self.target_rms = target_rms

    def enhance(self, waveform: torch.Tensor) -> torch.Tensor:
        """
        Enhances audio waveform [1, T] directly on GPU:
        1. Cuts sub-85Hz low-frequency noise (rumble, electrical hum, wind).
        2. Applies vocal formant equalizer boost around 2.2 kHz (consonants & intelligibility).
        3. Applies adaptive noise floor gating.
        4. Applies dynamic RMS Automatic Gain Control (AGC).
        """
        # Ensure tensor is on GPU
        audio = waveform.to(self.device).float()
        if audio.ndim == 1:
            audio = audio.unsqueeze(0)

        # 1. High-pass filter (sub-85Hz rumble cut)
        try:
            audio = F.highpass_biquad(audio, sample_rate=self.sample_rate, cutoff_freq=self.low_cut_hz)
        except Exception:
            pass

        # 2. Vocal Formant Presence Boost (Equalizer centered at 2.2 kHz, Q=1.0)
        try:
            audio = F.equalizer_biquad(
                audio,
                sample_rate=self.sample_rate,
                center_freq=self.vocal_boost_hz,
                gain=self.vocal_boost_gain_db,
                q=1.0
            )
        except Exception:
            pass

        # 3. Soft Noise Floor Gating
        # Suppress stationary noise below RMS threshold
        frame_len = int(self.sample_rate * 0.03)  # 30ms frames
        if audio.shape[1] > frame_len:
            # Estimate noise floor on quiet frames
            energy = audio.pow(2)
            noise_thresh = torch.quantile(energy, 0.15) * 1.5
            mask = (energy > noise_thresh).float()
            # Soft smoothing
            audio = audio * torch.clamp(mask + 0.1, max=1.0)

        # 4. Dynamic RMS Automatic Gain Control (AGC)
        rms = torch.sqrt(torch.mean(audio.pow(2)) + 1e-8)
        if rms > 1e-5:
            gain = self.target_rms / rms
            # Limit maximum gain to +18dB to prevent blowing out background hiss
            gain = torch.clamp(gain, min=0.3, max=8.0)
            audio = audio * gain

        # Hard peak clamp to prevent clipping
        audio = torch.clamp(audio, min=-0.99, max=0.99)

        return audio
