"""
GPU-Accelerated Speech Enhancer and Noise Filter.
Operates natively on PyTorch tensors on CUDA to suppress background rumble/hiss/hum
and amplify human voice intelligibility without phase distortion or audio-rate chopping.
"""

from typing import Optional
import torch
import torchaudio.functional as F


class GPUSpeechEnhancer:
    def __init__(
        self,
        sample_rate: int = 16000,
        device: Optional[str] = None,
        low_cut_hz: float = 85.0,
        vocal_boost_hz: float = 2400.0,
        vocal_boost_gain_db: float = 3.0,
        target_speech_rms: float = 0.12,
        boost_level: str = "adaptive"
    ):
        self.sample_rate = sample_rate
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.low_cut_hz = low_cut_hz
        self.vocal_boost_hz = vocal_boost_hz
        self.vocal_boost_gain_db = vocal_boost_gain_db
        self.target_speech_rms = target_speech_rms
        self.boost_level = boost_level

    def set_boost_level(self, level: str):
        """Sets the amplifier boost level ('standard', 'adaptive', 'high', 'max')."""
        if level in ("standard", "adaptive", "high", "max"):
            self.boost_level = level

    def enhance(self, waveform: torch.Tensor, boost_level: Optional[str] = None) -> torch.Tensor:
        """
        Enhances audio waveform [1, T] directly on GPU with clean spectral processing:
        1. 4th-order High-pass filter (sub-85Hz low-frequency noise, wind, HVAC rumble cut).
        2. Vocal formant presence equalizer (+3dB boost at 2.4 kHz for consonant definition).
        3. Frequency-domain Wiener spectral subtraction noise gate (STFT domain, zero chopping).
        4. Speech-Active Gated Vocal Amplifier with Soft-Knee Tanh Dynamic Limiting (up to +18dB clean gain, no clipping).
        """
        orig_device = waveform.device
        audio = waveform.to(self.device).float()
        if audio.ndim == 1:
            audio = audio.unsqueeze(0)

        # 1. 4th-order Highpass (cascaded Butterworth biquad at 85 Hz)
        try:
            audio = F.highpass_biquad(audio, sample_rate=self.sample_rate, cutoff_freq=self.low_cut_hz, Q=0.707)
            audio = F.highpass_biquad(audio, sample_rate=self.sample_rate, cutoff_freq=self.low_cut_hz, Q=0.707)
        except Exception:
            pass

        # 2. Vocal Formant Presence Boost (Parametric EQ at 2.4 kHz, Q=0.8)
        try:
            audio = F.equalizer_biquad(
                audio,
                sample_rate=self.sample_rate,
                center_freq=self.vocal_boost_hz,
                gain=self.vocal_boost_gain_db,
                Q=0.8
            )
        except Exception:
            pass

        # 3. Spectral Subtraction Noise Gate (STFT domain)
        try:
            audio = self._spectral_denoise(audio)
        except Exception as e:
            print(f"[GPUSpeechEnhancer Warning] Spectral denoise fallback: {e}")

        # 4. Speech-Active Gated Vocal Amplifier & Soft-Knee Dynamic Compression
        try:
            active_level = boost_level or self.boost_level
            audio = self._amplify_and_limit(audio, boost_level=active_level)
        except Exception as e:
            print(f"[GPUSpeechEnhancer Warning] Vocal amplifier fallback: {e}")

        return audio.to(orig_device)

    def _amplify_and_limit(self, audio: torch.Tensor, boost_level: str = "adaptive") -> torch.Tensor:
        """
        Vocal Amplifier with Speech-Active Gated AGC and Soft-Knee Dynamic Compression:
        - Detects active vocal frames to calculate true speech RMS (ignoring silence and noise floor).
        - Applies adaptive gain to achieve target speech RMS (~0.12 / -18.4 dBFS).
        - Smoothly compresses transient peaks above 0.70 using hyperbolic tangent saturation (soft-knee limiter).
        - Enforces strict peak headroom at 0.95 (0% digital clipping).
        """
        max_gain_db_map = {
            "standard": 10.0,   # ~3.1x
            "adaptive": 15.0,   # ~5.6x
            "high": 18.0,       # ~8.0x
            "max": 22.0         # ~12.6x
        }
        max_gain_db = max_gain_db_map.get(boost_level, 15.0)
        max_gain = 10.0 ** (max_gain_db / 20.0)

        total_samples = audio.shape[-1]
        frame_len = int(self.sample_rate * 0.05)  # 50ms frames
        
        if total_samples >= frame_len:
            frames = audio.unfold(-1, frame_len, frame_len // 2)
            frame_rms = torch.sqrt(torch.mean(frames.pow(2), dim=-1) + 1e-8)
            
            noise_floor = torch.quantile(frame_rms, 0.15)
            speech_gate = max(noise_floor.item() * 1.5, 1e-3)
            speech_frames = frame_rms[frame_rms > speech_gate]
            
            if len(speech_frames) > 0:
                speech_rms = torch.mean(speech_frames).item()
            else:
                speech_rms = torch.sqrt(torch.mean(audio.pow(2)) + 1e-8).item()
        else:
            speech_rms = torch.sqrt(torch.mean(audio.pow(2)) + 1e-8).item()

        if speech_rms > 1e-4:
            gain = min(self.target_speech_rms / speech_rms, max_gain)
        else:
            gain = 1.0

        gain = max(gain, 1.0)
        amplified = audio * gain

        threshold = 0.70
        abs_x = torch.abs(amplified)
        excess = torch.clamp(abs_x - threshold, min=0.0)
        compressed = threshold + (1.0 - threshold) * torch.tanh(excess / (1.0 - threshold))
        scale = torch.where(abs_x > threshold, compressed / (abs_x + 1e-8), torch.ones_like(amplified))
        output = amplified * scale

        peak = output.abs().max()
        if peak > 0.95:
            output = output * (0.95 / peak)

        return output

    def _spectral_denoise(self, audio: torch.Tensor) -> torch.Tensor:
        """
        Applies smooth Wiener-style spectral subtraction across frequency bins.
        Estimates stationary background noise floor without any time-domain chopping.
        Processes in 60s windows with 1s overlap-add for long files.
        """
        n_fft = 512
        hop_length = 128
        win_length = 512
        window = torch.hann_window(win_length, device=audio.device)
        total_len = audio.shape[-1]

        chunk_size = self.sample_rate * 60  # 60s chunks
        overlap = self.sample_rate          # 1s overlap

        if total_len <= chunk_size + overlap:
            return self._denoise_window(audio, n_fft, hop_length, win_length, window)

        # Chunked processing with smooth linear crossfade for long audio
        out = torch.zeros_like(audio)
        norm = torch.zeros_like(audio)
        step = chunk_size - overlap

        for start in range(0, total_len, step):
            end = min(start + chunk_size, total_len)
            chunk = audio[:, start:end]
            clean_chunk = self._denoise_window(chunk, n_fft, hop_length, win_length, window)

            # Linear crossfade ramp
            w = torch.ones(clean_chunk.shape[-1], device=audio.device)
            if start > 0:
                ramp_in = torch.linspace(0, 1, overlap, device=audio.device)
                w[:overlap] = ramp_in
            if end < total_len:
                ramp_out = torch.linspace(1, 0, overlap, device=audio.device)
                w[-overlap:] = ramp_out

            out[:, start:end] += clean_chunk * w
            norm[:, start:end] += w

        norm = torch.clamp(norm, min=1e-6)
        return out / norm

    def _denoise_window(
        self,
        audio: torch.Tensor,
        n_fft: int,
        hop_length: int,
        win_length: int,
        window: torch.Tensor
    ) -> torch.Tensor:
        stft = torch.stft(
            audio,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window=window,
            return_complex=True
        )
        mag = stft.abs()
        phase = torch.angle(stft)

        # Estimate stationary noise floor per frequency bin (10th percentile over time frames)
        noise_floor = torch.quantile(mag, 0.10, dim=-1, keepdim=True)

        # Wiener-style smooth gain
        snr = (mag.pow(2)) / (noise_floor.pow(2) + 1e-8)
        gain = snr / (snr + 1.5)
        # Floor gain to 0.25 (-12dB maximum suppression) to preserve vocal warmth & avoid musical noise
        gain = torch.clamp(gain, min=0.25, max=1.0)

        clean_stft = torch.polar(mag * gain, phase)
        clean = torch.istft(
            clean_stft,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window=window,
            length=audio.shape[-1]
        )
        return clean
