"""
Audio loading and normalization utilities using ffmpeg.
Optimized for AAC and multi-format audio conversion to 16kHz mono.
"""

from pathlib import Path
import subprocess
import tempfile
import io
import torch
import soundfile as sf
import numpy as np


def load_audio(file_path: str | Path, target_sr: int = 16000) -> tuple[torch.Tensor, int, float]:
    """Loads an audio file (AAC, WAV, MP3, etc.), converts to 16kHz mono, and returns (waveform, sr, duration)."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    cmd = ["ffmpeg", "-nostdin", "-threads", "0", "-i", str(path), "-f", "wav", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(target_sr), "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg failed to transcode {path}: {e.stderr.decode('utf-8', errors='ignore')}")

    audio_data, sr = sf.read(io.BytesIO(result.stdout), dtype="float32")
    tensor = torch.from_numpy(audio_data).unsqueeze(0) if audio_data.ndim == 1 else torch.from_numpy(audio_data.T)
    return tensor, sr, tensor.shape[1] / sr


def save_segment(waveform: torch.Tensor, start_s: float, end_s: float, output_path: str | Path, sr: int = 16000) -> Path:
    """Extracts an audio slice [start_s, end_s] and writes it to a WAV file."""
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start_sample = max(0, int(start_s * sr))
    end_sample = min(waveform.shape[1], int(end_s * sr))
    sf.write(str(out_path), waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
    return out_path


class AudioLoader:
    def __init__(self, target_sr: int = 16000):
        self.target_sr = target_sr

    def load_audio(self, file_path: str | Path) -> tuple[torch.Tensor, int, float]:
        return load_audio(file_path, self.target_sr)

    def save_segment(self, waveform: torch.Tensor, start_s: float, end_s: float, output_path: str | Path, sr: int = 16000) -> Path:
        return save_segment(waveform, start_s, end_s, output_path, sr)

