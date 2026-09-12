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
    whisper_model: str = os.getenv("COUNCIL_WHISPER_MODEL", "openai/whisper-large-v3")
    conformer_model: str = os.getenv("COUNCIL_CONFORMER_MODEL", "nvidia/stt_en_conformer_ctc_xlarge")
    parakeet_model: str = os.getenv("COUNCIL_PARAKEET_MODEL", "nvidia/parakeet-tdt-1.1b")
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
    vocal_boost_level: str = "adaptive"  # standard, adaptive, high, max
    
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
    settings_file: Path = base_dir / "settings.json"
    
    def __post_init__(self):
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Load user persisted settings if available
        self.load_persistent_settings()
        
        # Enable Ada Lovelace Tensor Core acceleration
        if self.enable_tf32 and torch.cuda.is_available():
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        if self.cudnn_benchmark and torch.cuda.is_available():
            torch.backends.cudnn.benchmark = True

    def load_persistent_settings(self) -> dict:
        """Loads persistent model settings from settings.json and updates config attributes."""
        import json
        if not self.settings_file.exists():
            return {}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                allowed_keys = {
                    "model_name",
                    "whisper_model",
                    "conformer_model",
                    "parakeet_model",
                    "default_diarizer",
                    "nemo_diarizer_model",
                    "vocal_boost_level"
                }
                for k, v in data.items():
                    if k in allowed_keys and v is not None:
                        setattr(self, k, v)
                return data
        except Exception as e:
            print(f"[Config Warning] Failed to load {self.settings_file}: {e}")
        return {}

    def save_persistent_settings(self, updates: dict) -> dict:
        """Saves persistent model settings to settings.json and updates active config."""
        import json
        current = self.load_persistent_settings()
        allowed_keys = {
            "model_name",
            "whisper_model",
            "conformer_model",
            "parakeet_model",
            "default_diarizer",
            "nemo_diarizer_model",
            "vocal_boost_level"
        }
        for k, v in updates.items():
            if k in allowed_keys and v is not None:
                current[k] = v
                setattr(self, k, v)
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            print(f"[Config Warning] Failed to write {self.settings_file}: {e}")
        return current


config = SuiteConfig()
