"""
Audio loading, pre-flight health diagnostics (1.1.A), and stereo phase inversion remixing (1.1.B).
Optimized for multi-format audio conversion (AAC, WAV, MP3, FLAC) to 16kHz mono with
automatic polarity correction to prevent out-of-phase acoustic cancellation.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import subprocess
import io
import torch
import soundfile as sf
import numpy as np

from .normalizer import measure_lufs, normalize_lufs
from .resampler import SoxVHQSincResampler, resample_sox_vhq


@dataclass
class AudioHealthReport:
    """Pre-flight acoustic health diagnostics badge and telemetry (1.1.A / 1.1.B / 1.2.A)."""
    snr_db: float
    clipping_pct: float
    dc_offset: float
    peak_dbfs: float
    rms_dbfs: float
    channels_original: int
    phase_correlation: float
    phase_inverted: bool
    dead_channel_detected: bool
    health_grade: str  # EXCELLENT | GOOD | FAIR | POOR
    recommendations: List[str]
    duration_s: float
    lufs: float = -70.0
    target_lufs: float = -16.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["snr_db"] = round(self.snr_db, 1)
        d["clipping_pct"] = round(self.clipping_pct, 2)
        d["dc_offset"] = round(self.dc_offset, 5)
        d["peak_dbfs"] = round(self.peak_dbfs, 1)
        d["rms_dbfs"] = round(self.rms_dbfs, 1)
        d["phase_correlation"] = round(self.phase_correlation, 2)
        d["duration_s"] = round(self.duration_s, 2)
        d["lufs"] = round(self.lufs, 1)
        d["target_lufs"] = round(self.target_lufs, 1)
        return d


def estimate_snr_db(mono: torch.Tensor, sr: int = 16000) -> float:
    """
    Estimates Signal-to-Noise Ratio (SNR) in dB from short-time frame energy distribution.
    """
    num_samples = mono.shape[-1]
    frame_len = int(0.050 * sr)  # 50ms frames (800 samples at 16kHz)
    hop = int(0.025 * sr)        # 25ms step (400 samples at 16kHz)

    if num_samples < frame_len:
        return 30.0

    frames = mono.squeeze(0).unfold(0, frame_len, hop)
    frame_rms = torch.sqrt(torch.mean(frames ** 2, dim=-1)) + 1e-9
    frame_db = 20.0 * torch.log10(frame_rms)

    p10 = torch.quantile(frame_db, 0.10).item()
    p90 = torch.quantile(frame_db, 0.90).item()
    dyn_range = p90 - p10

    if dyn_range >= 5.0:
        snr = dyn_range
    else:
        # Steady continuous tone or low-variation signal: evaluate overall RMS
        rms_val = torch.sqrt(torch.mean(mono ** 2)).item()
        rms_overall = 20.0 * np.log10(max(1e-9, rms_val))
        if rms_overall > -35.0:
            snr = min(45.0, max(25.0, rms_overall + 65.0))
        elif rms_overall < -60.0:
            snr = 0.0
        else:
            snr = max(5.0, rms_overall + 45.0)

    return max(0.0, min(60.0, float(snr)))


def remix_and_align_channels(waveform: torch.Tensor) -> Tuple[torch.Tensor, int, float, bool, bool, List[str]]:
    """
    Analyzes multi-channel audio, detects phase cancellation (1.1.B) and dead channels,
    and produces an optimal, phase-coherent 1-channel mono tensor [1, T].
    """
    channels = waveform.shape[0]
    phase_corr = 1.0
    phase_inverted = False
    dead_channel = False
    recs: List[str] = []

    if channels == 1:
        return waveform, 1, 1.0, False, False, recs

    if channels == 2:
        L = waveform[0]
        R = waveform[1]

        rms_L = torch.sqrt(torch.mean(L ** 2)).item()
        rms_R = torch.sqrt(torch.mean(R ** 2)).item()

        # Dead channel check (e.g. mono microphone plugged into stereo recorder)
        if rms_L > 1e-4 and rms_R < 1e-4:
            dead_channel = True
            mono = L.unsqueeze(0)
            recs.append("Bypassed silent Right channel (mono mic on stereo track)")
            return mono, channels, 1.0, False, True, recs
        elif rms_R > 1e-4 and rms_L < 1e-4:
            dead_channel = True
            mono = R.unsqueeze(0)
            recs.append("Bypassed silent Left channel (mono mic on stereo track)")
            return mono, channels, 1.0, False, True, recs

        # Cross-channel Pearson phase correlation
        L_centered = L - torch.mean(L)
        R_centered = R - torch.mean(R)
        var_L = torch.sum(L_centered ** 2)
        var_R = torch.sum(R_centered ** 2)
        denom = torch.sqrt(var_L * var_R) + 1e-9
        phase_corr = float((torch.sum(L_centered * R_centered) / denom).item())

        # Out-of-phase condition (ρ < -0.3)
        if phase_corr < -0.3:
            phase_inverted = True
            R_corrected = -R
            mono = ((L + R_corrected) / 2.0).unsqueeze(0)
            recs.append(f"Inverted Right channel phase (ρ = {round(phase_corr, 2)}) to prevent acoustic cancellation")
        else:
            mono = ((L + R) / 2.0).unsqueeze(0)

        return mono, channels, phase_corr, phase_inverted, dead_channel, recs

    # Multi-channel / surround (>2 channels)
    # If 6-channel 5.1 surround: channel 2 is dialogue Center; channels 0,1 are L, R
    if channels == 6:
        center = waveform[2]
        lr_mix = (waveform[0] + waveform[1]) * 0.5
        mono = (0.7 * center + 0.3 * lr_mix).unsqueeze(0)
        recs.append("Extracted 5.1 Center dialogue channel with front-stage mixdown")
    else:
        mono = torch.mean(waveform, dim=0, keepdim=True)

    return mono, channels, 1.0, False, False, recs


def diagnose_audio_health(
    waveform: torch.Tensor,
    sr: int = 16000,
    channels_original: int = 1,
    phase_correlation: float = 1.0,
    phase_inverted: bool = False,
    dead_channel: bool = False,
    initial_recs: Optional[List[str]] = None
) -> Tuple[torch.Tensor, AudioHealthReport]:
    """
    Performs rapid (<0.05s) pre-flight health diagnostics (1.1.A):
    Checks clipping %, mains DC offset, peak/RMS dBFS, and estimates SNR in dB.
    Removes DC offset if bias exceeds 0.005.
    """
    recs = list(initial_recs) if initial_recs else []
    m = waveform.squeeze(0)
    num_samples = m.shape[0]

    # 1. 0 dBFS Clipping Percentage (evaluated before DC offset removal)
    clipped_count = (torch.abs(m) >= 0.995).sum().item()
    clipping_pct = (clipped_count / max(1, num_samples)) * 100.0
    if clipping_pct > 0.5:
        recs.append(f"Warning: {round(clipping_pct, 2)}% clipped samples detected (digital distortion)")

    # 2. Mains DC Offset
    dc_offset = float(torch.mean(m).item())
    if abs(dc_offset) > 0.005:
        m = m - dc_offset
        recs.append(f"Removed mains DC offset of {round(abs(dc_offset), 4)}")
        waveform = m.unsqueeze(0)

    # 3. Peak and RMS amplitude (dBFS)
    peak = float(torch.max(torch.abs(m)).item())
    peak_dbfs = float(20.0 * np.log10(max(1e-9, peak)))
    rms = float(torch.sqrt(torch.mean(m ** 2)).item())
    rms_dbfs = float(20.0 * np.log10(max(1e-9, rms)))

    # 4. SNR Estimation
    snr_db = estimate_snr_db(waveform, sr)
    if snr_db < 12.0:
        recs.append(f"Low SNR audio ({round(snr_db, 1)} dB): GPU speech enhancer recommended")

    # 5. Integrated Loudness (1.2.A)
    measured_lufs = measure_lufs(waveform, sr)
    if -65.0 < measured_lufs < -26.0:
        recs.append(f"Low dialogue loudness ({round(measured_lufs, 1)} LUFS): EBU R128 dynamic normalization active")
    elif measured_lufs > -12.0:
        recs.append(f"Loud / hyper-compressed audio ({round(measured_lufs, 1)} LUFS): soft-knee peak limiting active")

    # 6. Composite Health Grade
    if snr_db >= 20.0 and clipping_pct < 0.05 and abs(dc_offset) < 0.005:
        grade = "EXCELLENT"
    elif snr_db >= 14.0 and clipping_pct < 0.3:
        grade = "GOOD"
    elif snr_db >= 8.0 and clipping_pct < 1.0:
        grade = "FAIR"
    else:
        grade = "POOR"

    report = AudioHealthReport(
        snr_db=round(snr_db, 1),
        clipping_pct=round(clipping_pct, 2),
        dc_offset=round(abs(dc_offset), 5),
        peak_dbfs=round(peak_dbfs, 1),
        rms_dbfs=round(rms_dbfs, 1),
        channels_original=channels_original,
        phase_correlation=round(phase_correlation, 2),
        phase_inverted=phase_inverted,
        dead_channel_detected=dead_channel,
        health_grade=grade,
        recommendations=recs,
        duration_s=round(num_samples / sr, 2),
        lufs=round(measured_lufs, 1),
        target_lufs=-16.0
    )

    return waveform, report


def load_audio(
    file_path: str | Path,
    target_sr: int = 16000,
    return_health: bool = False,
    auto_remix_phase: bool = True,
    enable_lufs_norm: bool = True,
    target_lufs: float = -16.0
) -> Tuple[torch.Tensor, int, float] | Tuple[torch.Tensor, int, float, AudioHealthReport]:
    """
    Loads an audio file (AAC, WAV, MP3, FLAC), converts to target sample rate using SoX VHQ
    resampling (1.2.B), performs out-of-phase polarity inversion & auto-remixing (1.1.B), computes
    pre-flight audio health diagnostics (1.1.A), and applies EBU R128 LUFS normalization (1.2.A).
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # FFmpeg reads source channels using high-precision libsoxr resampler (1.2.B)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads", "0",
        "-i", str(path),
        "-af", "aresample=resampler=soxr:precision=28:cheby=1",
        "-f", "wav",
        "-acodec", "pcm_s16le",
        "-ar", str(target_sr),
        "-"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data, sr = sf.read(io.BytesIO(result.stdout), dtype="float32")
    except subprocess.CalledProcessError:
        # Fallback to standard decode and in-memory SoX VHQ Kaiser sinc resampler
        fallback_cmd = [
            "ffmpeg",
            "-nostdin",
            "-threads", "0",
            "-i", str(path),
            "-f", "wav",
            "-acodec", "pcm_s16le",
            "-"
        ]
        fb_result = subprocess.run(fallback_cmd, capture_output=True, check=True)
        raw_data, orig_sr = sf.read(io.BytesIO(fb_result.stdout), dtype="float32")
        t_data = torch.from_numpy(raw_data if raw_data.ndim == 1 else raw_data.T)
        t_resampled = resample_sox_vhq(t_data, orig_sr=orig_sr, target_sr=target_sr)
        result_buf = io.BytesIO()
        sf.write(result_buf, t_resampled.cpu().numpy().T if t_resampled.ndim > 1 else t_resampled.cpu().numpy(), target_sr, format="WAV", subtype="PCM_16")
        result_buf.seek(0)
        audio_data, sr = sf.read(result_buf, dtype="float32")

    if audio_data.ndim == 1:
        raw_tensor = torch.from_numpy(audio_data).unsqueeze(0)
    else:
        raw_tensor = torch.from_numpy(audio_data.T)

    # 1.1.B: Channel Remix & Stereo Phase Inversion
    if auto_remix_phase:
        mono_tensor, orig_ch, phase_corr, inverted, dead_ch, recs = remix_and_align_channels(raw_tensor)
    else:
        mono_tensor = torch.mean(raw_tensor, dim=0, keepdim=True)
        orig_ch = raw_tensor.shape[0]
        phase_corr = 1.0
        inverted = False
        dead_ch = False
        recs = []

    # 1.1.A: Pre-Flight Health Diagnostics
    clean_mono, health_report = diagnose_audio_health(
        mono_tensor,
        sr=sr,
        channels_original=orig_ch,
        phase_correlation=phase_corr,
        phase_inverted=inverted,
        dead_channel=dead_ch,
        initial_recs=recs
    )

    # 1.2.A: EBU R128 LUFS Loudness Normalization
    if enable_lufs_norm:
        clean_mono, in_lufs, out_lufs = normalize_lufs(clean_mono, sr=sr, target_lufs=target_lufs)
        health_report.lufs = in_lufs
        health_report.target_lufs = target_lufs

    duration = clean_mono.shape[1] / sr

    if return_health:
        return clean_mono, sr, duration, health_report
    return clean_mono, sr, duration


def save_segment(waveform: torch.Tensor, start_s: float, end_s: float, output_path: str | Path, sr: int = 16000) -> Path:
    """Extracts an audio slice [start_s, end_s] and writes it to a WAV file."""
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start_sample = max(0, int(start_s * sr))
    end_sample = min(waveform.shape[1], int(end_s * sr))
    sf.write(str(out_path), waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
    return out_path


class AudioLoader:
    """
    Managed audio ingest engine with pre-flight health diagnostics (1.1.A),
    stereo phase inversion remediation (1.1.B), and EBU R128 LUFS normalization (1.2.A).
    """

    def __init__(self, target_sr: int = 16000, auto_remix_phase: bool = True, enable_lufs_norm: bool = True, target_lufs: float = -16.0):
        self.target_sr = target_sr
        self.auto_remix_phase = auto_remix_phase
        self.enable_lufs_norm = enable_lufs_norm
        self.target_lufs = target_lufs
        self.last_health_report: Optional[AudioHealthReport] = None

    def load_audio(
        self,
        file_path: str | Path,
        return_health: bool = False,
        enable_lufs_norm: Optional[bool] = None,
        target_lufs: Optional[float] = None
    ) -> Tuple[torch.Tensor, int, float] | Tuple[torch.Tensor, int, float, AudioHealthReport]:
        use_lufs = self.enable_lufs_norm if enable_lufs_norm is None else enable_lufs_norm
        use_target = self.target_lufs if target_lufs is None else target_lufs
        res = load_audio(
            file_path,
            target_sr=self.target_sr,
            return_health=True,
            auto_remix_phase=self.auto_remix_phase,
            enable_lufs_norm=use_lufs,
            target_lufs=use_target
        )
        waveform, sr, duration, report = res
        self.last_health_report = report
        if return_health:
            return waveform, sr, duration, report
        return waveform, sr, duration

    def diagnose_audio(self, file_path: str | Path) -> AudioHealthReport:
        """Runs a standalone pre-flight health check on an audio file."""
        _, _, _, report = load_audio(
            file_path,
            target_sr=self.target_sr,
            return_health=True,
            auto_remix_phase=self.auto_remix_phase,
            enable_lufs_norm=self.enable_lufs_norm,
            target_lufs=self.target_lufs
        )
        self.last_health_report = report
        return report

    def save_segment(self, waveform: torch.Tensor, start_s: float, end_s: float, output_path: str | Path, sr: int = 16000) -> Path:
        return save_segment(waveform, start_s, end_s, output_path, sr)
