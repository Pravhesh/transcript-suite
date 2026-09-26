"""
FFT Mel-Spectrogram Waterfall Generator (Feature 3.1.B).
Generates high-contrast time-frequency spectrogram waterfall PNGs
highlighting speech formants (300 Hz - 3.5 kHz) versus background noise / sibilants.
"""

from typing import Optional, Union
from pathlib import Path
import io
import torch
import torchaudio
import numpy as np
from PIL import Image
import matplotlib.cm as cm
import logging
from .loader import load_audio

logger = logging.getLogger(__name__)


def _generate_blank_spectrogram(width: int = 1200, height: int = 96) -> bytes:
    """Renders a blank dark canvas PNG when audio loading fails."""
    img = Image.new("RGB", (width, height), (11, 15, 23))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_spectrogram_image(
    audio_path_or_tensor: Union[str, Path, torch.Tensor],
    output_path: Optional[Union[str, Path]] = None,
    sr: int = 16000,
    width: int = 1200,
    height: int = 96
) -> bytes:
    """
    Computes a Mel-scale FFT spectrogram and renders an inferno-colormapped PNG.
    - Resolves speech formants with percentile dynamic range stretching.
    - Caches the resulting PNG on disk next to the audio file if a path is provided.
    - Returns raw PNG image bytes.
    """
    cache_file: Optional[Path] = None
    if isinstance(audio_path_or_tensor, (str, Path)):
        p = Path(audio_path_or_tensor).resolve()
        cache_file = p.parent / f"{p.name}.spec_{width}x{height}.png"
        if output_path is None and cache_file.exists():
            try:
                return cache_file.read_bytes()
            except Exception:
                pass
        elif output_path and Path(output_path).exists():
            try:
                return Path(output_path).read_bytes()
            except Exception:
                pass

    # 1. Load or standardize waveform
    try:
        if isinstance(audio_path_or_tensor, (str, Path)):
            audio_res = load_audio(audio_path_or_tensor, target_sr=sr)
            waveform = audio_res[0]
            sr = audio_res[1]
        else:
            waveform = audio_path_or_tensor
    except Exception as exc:
        logger.warning(f"Failed to load audio for spectrogram generation: {exc}")
        return _generate_blank_spectrogram(width, height)

    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    elif waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)

    num_samples = waveform.shape[-1]
    if num_samples == 0:
        # Generate 1s silence if waveform is empty
        waveform = torch.zeros(1, sr)
        num_samples = sr

    # 2. Compute Mel-scale Spectrogram
    n_fft = 512
    win_length = 400
    # Adapt hop length based on duration so resolution matches requested width
    hop_length = max(80, min(512, int(num_samples / max(1, width))))

    mel_transform = torchaudio.transforms.MelSpectrogram(
        sample_rate=sr,
        n_fft=n_fft,
        win_length=win_length,
        hop_length=hop_length,
        n_mels=height,
        power=2.0
    )

    with torch.inference_mode():
        spec = mel_transform(waveform)
        # Logarithmic dB compression: 10 * log10(max(spec, 1e-5))
        spec_db = 10.0 * torch.log10(torch.clamp(spec, min=1e-5))

        # Dynamic range percentile clipping for speech formant contrast
        p5 = torch.quantile(spec_db, 0.05).item()
        p99 = torch.quantile(spec_db, 0.995).item()
        denom = max(1e-4, p99 - p5)
        spec_norm = torch.clamp((spec_db - p5) / denom, 0.0, 1.0)

        # Invert vertical frequency axis so low frequencies (formants) are at the bottom
        spec_norm = torch.flip(spec_norm.squeeze(0), dims=[0])

        # Resize to exact (height, width)
        spec_resized = torch.nn.functional.interpolate(
            spec_norm.unsqueeze(0).unsqueeze(0),
            size=(height, width),
            mode="bilinear",
            align_corners=False
        ).squeeze(0).squeeze(0).cpu().numpy()

    # 3. Colormap via matplotlib inferno (dark purple -> amber -> bright gold)
    rgb = (cm.inferno(spec_resized)[:, :, :3] * 255.0).astype(np.uint8)

    # 4. Save to PNG
    img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()

    # Cache on disk
    if cache_file:
        try:
            cache_file.write_bytes(png_bytes)
        except Exception:
            pass

    if output_path:
        try:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png_bytes)
        except Exception:
            pass

    return png_bytes
