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
    conformer_model: str = os.getenv("COUNCIL_CONFORMER_MODEL", "stt_en_conformer_ctc_xlarge")
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
    
    # Memory safety & Predictive Governor
    vram_alert_threshold_gb: float = 7.0  # Max safe threshold on 8GB GPU
    vram_governor_threshold_gb: float = 5.5  # Predictive Emergency Ceiling
    predictive_emergency_enabled: bool = True  # Dynamic auto-throttling & pre-emptive eviction
    auto_empty_cache_interval: int = 2     # Clear cache every N chunks
    
    # Diarization
    default_diarizer: str = os.getenv("DEFAULT_DIARIZER", "nemo")  # "nemo" or "pyannote"
    nemo_diarizer_model: str = "titanet_large"
    hf_token: str | None = os.getenv("HF_TOKEN", None)

    # Supreme Adjudicator (Stage 5)
    enable_audex_adjudicator: bool = os.getenv("ENABLE_AUDEX_ADJUDICATOR", "false").lower() in ("true", "1")
    audex_model_id: str = os.getenv("AUDEX_MODEL_ID", "nvidia/Nemotron-Labs-Audex-2B")
    
    # Storage Hierarchy
    base_dir: Path = Path("/mnt/d/transcript_suite_data") if (Path("/mnt/d").exists() and os.access("/mnt/d", os.W_OK)) else (Path.home() / ".cache" / "transcript_suite")

    def __post_init__(self):
        self.base_dir = Path(self.base_dir)
        self._init_subdirectories()
        
        # Load user persisted settings if available (may override base_dir)
        self.load_persistent_settings()
        
        # Apply environment routing for cache and temp directories
        self._apply_env_routing()

        # Enable Ada Lovelace Tensor Core acceleration
        if self.enable_tf32 and torch.cuda.is_available():
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        if self.cudnn_benchmark and torch.cuda.is_available():
            torch.backends.cudnn.benchmark = True

    def _init_subdirectories(self):
        """Initializes all subdirectories under self.base_dir."""
        self.models_dir = self.base_dir / "models"
        self.hf_home = self.models_dir / "huggingface"
        self.nemo_dir = self.models_dir / "nemo"
        self.torch_home = self.models_dir / "torch"
        self.tmp_dir = self.base_dir / "tmp"
        self.upload_dir = self.base_dir / "uploads"
        self.output_dir = self.base_dir / "outputs"
        self.settings_file = self.base_dir / "settings.json"

        for p in [self.base_dir, self.models_dir, self.hf_home, self.nemo_dir, self.torch_home, self.tmp_dir, self.upload_dir, self.output_dir]:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"[Config Warning] Could not create {p}: {e}")

    def _apply_env_routing(self):
        """Routes OS temp dirs, HF cache, Torch cache, and NeMo cache to persistent storage."""
        tmp_str = str(self.tmp_dir)
        os.environ["TMPDIR"] = tmp_str
        os.environ["TEMP"] = tmp_str
        os.environ["TMP"] = tmp_str

        import tempfile
        tempfile.tempdir = tmp_str

        os.environ["HF_HOME"] = str(self.hf_home)
        os.environ["TORCH_HOME"] = str(self.torch_home)
        os.environ["NEMO_CACHE_DIR"] = str(self.nemo_dir)

    def update_storage_root(self, new_path: str | Path) -> dict:
        """Updates the persistent storage root directory, creates structure, and updates settings."""
        p = Path(new_path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.base_dir = p
        self._init_subdirectories()
        self._apply_env_routing()
        return self.save_persistent_settings({"base_dir": str(p)})

    def get_storage_info(self) -> dict:
        """Returns disk space metrics and paths for the active persistent storage root."""
        import shutil
        base_str = str(self.base_dir)
        try:
            total, used, free = shutil.disk_usage(base_str)
            total_gb = round(total / (1024 ** 3), 2)
            used_gb = round(used / (1024 ** 3), 2)
            free_gb = round(free / (1024 ** 3), 2)
            pct = round((used / total) * 100, 1)
        except Exception:
            total_gb, used_gb, free_gb, pct = 0.0, 0.0, 0.0, 0.0

        return {
            "base_dir": base_str,
            "models_dir": str(self.models_dir),
            "hf_home": str(self.hf_home),
            "nemo_dir": str(self.nemo_dir),
            "torch_home": str(self.torch_home),
            "tmp_dir": str(self.tmp_dir),
            "upload_dir": str(self.upload_dir),
            "output_dir": str(self.output_dir),
            "settings_file": str(self.settings_file),
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": pct
        }

    def load_persistent_settings(self) -> dict:
        """Loads persistent model settings from settings.json and updates config attributes."""
        import json
        # Check both active base_dir settings and fallback ~/.cache settings
        s_file = self.settings_file
        if not s_file.exists():
            alt = Path.home() / ".cache" / "transcript_suite" / "settings.json"
            if alt.exists():
                s_file = alt

        if not s_file.exists():
            return {}
        try:
            with open(s_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                allowed_keys = {
                    "base_dir",
                    "model_name",
                    "whisper_model",
                    "conformer_model",
                    "parakeet_model",
                    "default_diarizer",
                    "nemo_diarizer_model",
                    "vocal_boost_level",
                    "hf_token",
                    "vram_governor_threshold_gb",
                    "predictive_emergency_enabled",
                    "enable_audex_adjudicator",
                    "audex_model_id"
                }
                for k, v in data.items():
                    if k in allowed_keys and v is not None:
                        if k == "base_dir":
                            self.base_dir = Path(v)
                            self._init_subdirectories()
                        else:
                            setattr(self, k, v)

                # Export persistent Hugging Face token to environment if present
                if getattr(self, "hf_token", None):
                    os.environ["HF_TOKEN"] = self.hf_token
                    os.environ["HUGGING_FACE_HUB_TOKEN"] = self.hf_token
                else:
                    hf_cache_file = Path.home() / ".cache" / "huggingface" / "token"
                    if hf_cache_file.exists():
                        try:
                            cached_tok = hf_cache_file.read_text(encoding="utf-8").strip()
                            if cached_tok:
                                self.hf_token = cached_tok
                                os.environ["HF_TOKEN"] = cached_tok
                                os.environ["HUGGING_FACE_HUB_TOKEN"] = cached_tok
                        except Exception:
                            pass
                return data
        except Exception as e:
            print(f"[Config Warning] Failed to load {s_file}: {e}")
        return {}

    def save_persistent_settings(self, updates: dict) -> dict:
        """Saves persistent model settings to settings.json and updates active config."""
        import json
        current = self.load_persistent_settings()
        allowed_keys = {
            "base_dir",
            "model_name",
            "whisper_model",
            "conformer_model",
            "parakeet_model",
            "default_diarizer",
            "nemo_diarizer_model",
            "vocal_boost_level",
            "hf_token",
            "vram_governor_threshold_gb",
            "predictive_emergency_enabled",
            "enable_audex_adjudicator",
            "audex_model_id"
        }
        for k, v in updates.items():
            if k in allowed_keys:
                if v is None or (isinstance(v, str) and not v.strip()):
                    current.pop(k, None)
                    setattr(self, k, None)
                    if k == "hf_token":
                        os.environ.pop("HF_TOKEN", None)
                        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
                else:
                    current[k] = v
                    if k == "base_dir":
                        self.base_dir = Path(v)
                        self._init_subdirectories()
                        self._apply_env_routing()
                    else:
                        setattr(self, k, v)
                    if k == "hf_token":
                        os.environ["HF_TOKEN"] = str(v)
                        os.environ["HUGGING_FACE_HUB_TOKEN"] = str(v)
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            print(f"[Config Warning] Failed to write {self.settings_file}: {e}")
        return current


config = SuiteConfig()
