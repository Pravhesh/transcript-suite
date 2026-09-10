"""
Global configuration for Transcript Suite.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import torch

@dataclass
class SuiteConfig:
    # Model configuration
    model_name: str = os.getenv("CANARY_MODEL", "nvidia/canary-qwen-2.5b")
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    dtype: torch.dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    
    # Audio pipeline
    sample_rate: int = 16000
    target_channels: int = 1  # Mono
    max_chunk_duration_s: float = 25.0  # Safe bounds for 8GB VRAM
    min_chunk_duration_s: float = 1.0
    vad_padding_s: float = 0.3
    
    # Memory safety
    vram_alert_threshold_gb: float = 7.0  # Max safe threshold on 8GB GPU
    auto_empty_cache_interval: int = 2     # Clear cache every N chunks
    
    # Diarization
    default_diarizer: str = os.getenv("DEFAULT_DIARIZER", "nemo")  # "nemo" or "pyannote"
    nemo_diarizer_model: str = "titanet_large"
    hf_token: str | None = os.getenv("HF_TOKEN", None)
    
    # Storage
    base_dir: Path = Path.home() / ".cache" / "transcript_suite"
    upload_dir: Path = base_dir / "uploads"
    output_dir: Path = base_dir / "outputs"
    
    def __post_init__(self):
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

config = SuiteConfig()
