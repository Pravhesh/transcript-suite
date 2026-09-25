"""
EBU R128 / ITU-R BS.1770-4 Loudness Normalization & Soft-Knee Dynamic Peak Limiting (Feature 1.2.A).
Provides native PyTorch tensor K-weighting filtering, momentary and integrated LUFS measurement,
and transparent loudness normalization targeting -16 LUFS with zero digital clipping.
"""

from typing import Tuple, Optional
import math
import torch
import torchaudio.functional as AF


def get_bs1770_k_filter_coefficients(sr: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Computes bilinear transform biquad coefficients for ITU-R BS.1770-4 K-weighting curves:
    - Stage 1: High-shelf pre-filter (simulating acoustic head response, ~+4 dB above 1.5 kHz).
    - Stage 2: High-pass RLB weighting filter (~cut sub-40 Hz floor rumble).
    Returns (b1, a1, b2, a2) as float32 tensors.
    """
    # Stage 1: High shelf filter (f0 = 1681.97 Hz, Gain = 3.9998 dB, Q = 0.7071)
    vh = 10.0 ** (3.99984385397 / 20.0)
    f0_1 = 1681.974450955533
    q1 = 0.7071752369274136
    k1 = math.tan(math.pi * f0_1 / sr)
    den1 = 1.0 + k1 / q1 + k1 * k1
    b0_1 = (vh + math.sqrt(2.0 * vh) * k1 + k1 * k1) / den1
    b1_1 = 2.0 * (k1 * k1 - vh) / den1
    b2_1 = (vh - math.sqrt(2.0 * vh) * k1 + k1 * k1) / den1
    a1_1 = 2.0 * (k1 * k1 - 1.0) / den1
    a2_1 = (1.0 - k1 / q1 + k1 * k1) / den1

    # Stage 2: High pass RLB filter (f0 = 38.14 Hz, Q = 0.5003)
    f0_2 = 38.13547087602444
    q2 = 0.5003270373238773
    k2 = math.tan(math.pi * f0_2 / sr)
    den2 = 1.0 + k2 / q2 + k2 * k2
    b0_2 = 1.0 / den2
    b1_2 = -2.0 / den2
    b2_2 = 1.0 / den2
    a1_2 = 2.0 * (k2 * k2 - 1.0) / den2
    a2_2 = (1.0 - k2 / q2 + k2 * k2) / den2

    return (
        torch.tensor([b0_1, b1_1, b2_1], dtype=torch.float32),
        torch.tensor([1.0, a1_1, a2_1], dtype=torch.float32),
        torch.tensor([b0_2, b1_2, b2_2], dtype=torch.float32),
        torch.tensor([1.0, a1_2, a2_2], dtype=torch.float32),
    )


def apply_k_weighting(waveform: torch.Tensor, sr: int = 16000) -> torch.Tensor:
    """
    Applies ITU-R BS.1770-4 K-weighting pre-filter and RLB filter to waveform [1, T].
    Preserves device and runs directly in PyTorch.
    """
    orig_device = waveform.device
    audio = waveform.to(orig_device).float()
    if audio.ndim == 1:
        audio = audio.unsqueeze(0)

    b1, a1, b2, a2 = get_bs1770_k_filter_coefficients(sr)
    b1, a1, b2, a2 = b1.to(orig_device), a1.to(orig_device), b2.to(orig_device), a2.to(orig_device)

    # Cascaded biquad filtering
    filtered = AF.biquad(audio, b1[0], b1[1], b1[2], a1[0], a1[1], a1[2])
    filtered = AF.biquad(filtered, b2[0], b2[1], b2[2], a2[0], a2[1], a2[2])
    return filtered


def measure_lufs(waveform: torch.Tensor, sr: int = 16000) -> float:
    """
    Measures integrated loudness in LUFS according to ITU-R BS.1770-4 / EBU R128:
    - K-weighting filter applied.
    - 400ms momentary blocks with 75% overlap (100ms hop).
    - Absolute threshold gating at -70.0 LUFS.
    - Relative threshold gating at 10 LU below un-gated loudness.
    Returns integrated LUFS float.
    """
    if waveform.numel() == 0:
        return -70.0

    k_audio = apply_k_weighting(waveform, sr=sr)
    num_samples = k_audio.shape[-1]

    block_size = int(0.400 * sr)  # 400ms momentary window
    hop = int(0.100 * sr)         # 100ms step (75% overlap)

    if num_samples < block_size:
        mean_sq = torch.mean(k_audio ** 2).item()
        if mean_sq <= 1e-12:
            return -70.0
        return float(-0.691 + 10.0 * math.log10(mean_sq))

    blocks = k_audio.squeeze(0).unfold(0, block_size, hop)
    block_energies = torch.mean(blocks ** 2, dim=-1)  # [num_blocks]

    # Absolute threshold: discard silent / low-level blocks below -70.0 LUFS
    block_loudness = -0.691 + 10.0 * torch.log10(block_energies + 1e-12)
    abs_mask = block_loudness >= -70.0
    if not abs_mask.any():
        return -70.0

    z_abs = block_energies[abs_mask]
    mean_z_abs = torch.mean(z_abs).item()
    if mean_z_abs <= 1e-12:
        return -70.0

    # Relative threshold: Gamma_r = Loudness(abs) - 10.0 LU
    gamma_r = -0.691 + 10.0 * math.log10(mean_z_abs) - 10.0
    rel_mask = block_loudness >= gamma_r
    if not rel_mask.any():
        return -70.0

    z_rel = block_energies[rel_mask]
    mean_z_rel = torch.mean(z_rel).item()
    if mean_z_rel <= 1e-12:
        return -70.0

    integrated_lufs = -0.691 + 10.0 * math.log10(mean_z_rel)
    return float(max(-70.0, min(10.0, integrated_lufs)))


def apply_soft_knee_limiter(waveform: torch.Tensor, peak_limit_dbfs: float = -1.0) -> torch.Tensor:
    """
    Applies transparent hyperbolic tangent (tanh) soft-knee peak limiting
    to ensure max peak <= 10^(peak_limit_dbfs/20) with 0% digital clipping.
    """
    ceiling = 10.0 ** (peak_limit_dbfs / 20.0)  # e.g. 10^(-1/20) ~ 0.89125
    knee_threshold = ceiling * 0.85             # start softening at 85% of ceiling

    abs_x = torch.abs(waveform)
    max_peak = abs_x.max().item()

    if max_peak > knee_threshold:
        excess = torch.clamp(abs_x - knee_threshold, min=0.0)
        headroom = ceiling - knee_threshold
        compressed = knee_threshold + headroom * torch.tanh(excess / (headroom + 1e-8))
        scale = torch.where(abs_x > knee_threshold, compressed / (abs_x + 1e-8), torch.ones_like(waveform))
        limited = waveform * scale
        # Strict hard cap ensuring no sample can ever breach ceiling (0% clipping)
        final_peak = limited.abs().max().item()
        if final_peak > ceiling:
            limited = limited * (ceiling / final_peak)
        return limited
    return waveform


def normalize_lufs(
    waveform: torch.Tensor,
    sr: int = 16000,
    target_lufs: float = -16.0,
    peak_limit_dbfs: float = -1.0,
    max_gain_db: float = 30.0
) -> Tuple[torch.Tensor, float, float]:
    """
    Normalizes audio waveform [1, T] to target LUFS with transparent soft-knee peak limiting:
    - Measures pre-normalization integrated LUFS.
    - Computes and applies required linear gain: delta_gain = target_lufs - input_lufs.
    - If peaks approach peak_limit_dbfs (default -1.0 dBFS), applies hyperbolic tangent (tanh)
      soft-knee compression to preserve dynamic clarity without digital clipping.
    Returns (normalized_waveform, input_lufs, output_lufs).
    """
    orig_shape = waveform.shape
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)

    input_lufs = measure_lufs(waveform, sr=sr)
    if input_lufs <= -68.0:
        # Near complete silence: avoid amplifying pure noise floor excessively
        return waveform.view(orig_shape), input_lufs, input_lufs

    delta_gain_db = target_lufs - input_lufs
    delta_gain_db = max(-30.0, min(max_gain_db, delta_gain_db))
    linear_gain = 10.0 ** (delta_gain_db / 20.0)

    amplified = waveform * linear_gain

    # Soft-knee peak limiting
    limited = apply_soft_knee_limiter(amplified, peak_limit_dbfs=peak_limit_dbfs)

    output_lufs = measure_lufs(limited, sr=sr)
    return limited.view(orig_shape), round(input_lufs, 1), round(output_lufs, 1)
