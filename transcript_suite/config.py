"""
Global configuration for Transcript Suite.
"""

from dataclasses import dataclass
from pathlib import Path
import os

# Prevent CUDA memory fragmentation on 8GB GPUs
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch

@dataclass
class SuiteConfig:
    # Model configuration
    model_name: str = os.getenv("CANARY_MODEL", "nvidia/canary-qwen-2.5b")
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    dtype: torch.dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    
    # GPU Acceleration Optimizations (RTX 4060 Ada Lovelace)
    use_gpu_vad: bool = torch.cuda.is_available()
    enable_tf32: bool = True
    cudnn_benchmark: bool = True
    
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
        
        # Enable Ada Lovelace Tensor Core acceleration
        if self.enable_tf32 and torch.cuda.is_available():
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        if self.cudnn_benchmark and torch.cuda.is_available():
            torch.backends.cudnn.benchmark = True


config = SuiteConfig()
