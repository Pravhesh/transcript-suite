"""
Model and Checkpoint Manager for Transcript Suite.
Inspects local Hugging Face and NeMo caches, catalogs architecture presets,
manages the dynamic multi-model council roster, and handles asynchronous model downloads.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import os
import shutil
import time
import threading
import gc

from ..config import config


CATALOG_PRESETS: List[Dict[str, Any]] = [
    # Pass 1: Speech-LLM (Lead Justice)
    {
        "id": "nvidia/canary-qwen-2.5b",
        "name": "Canary-Qwen-2.5B",
        "role": "speech_llm",
        "role_display": "Pass 1: Speech-LLM (Lead Justice)",
        "framework": "huggingface",
        "size_gb": 5.2,
        "parameters": "2.5B",
        "description": "NVIDIA Flagship Speech-LLM with conversational context reasoning & high-accuracy English speech-to-text.",
        "recommended": True
    },
    # Pass 2: Autoregressive (Cross-Examiner)
    {
        "id": "openai/whisper-large-v3",
        "name": "Whisper-Large-v3",
        "role": "whisper",
        "role_display": "Pass 2: Autoregressive (Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 3.1,
        "parameters": "1.5B",
        "description": "Gold-standard autoregressive ASR for robust acoustic disambiguation and phrase boundary validation.",
        "recommended": True
    },
    {
        "id": "openai/whisper-large-v3-turbo",
        "name": "Whisper-Large-v3-Turbo",
        "role": "whisper",
        "role_display": "Pass 2: Autoregressive (Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 1.6,
        "parameters": "809M",
        "description": "Fast 4-layer decoder variant of Large-v3 with 3x higher throughput and near-identical accuracy.",
        "recommended": False
    },
    {
        "id": "openai/whisper-medium.en",
        "name": "Whisper-Medium.en",
        "role": "whisper",
        "role_display": "Pass 2: Autoregressive (Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 1.5,
        "parameters": "769M",
        "description": "English-only medium model with strong phonetic accuracy and moderate VRAM footprint.",
        "recommended": False
    },
    {
        "id": "openai/whisper-small.en",
        "name": "Whisper-Small.en",
        "role": "whisper",
        "role_display": "Pass 2: Autoregressive (Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 0.5,
        "parameters": "244M",
        "description": "Lightweight English Whisper model with ultra-fast decoding speed.",
        "recommended": False
    },
    {
        "id": "distil-whisper/distil-large-v3",
        "name": "Distil-Whisper-Large-v3",
        "role": "whisper",
        "role_display": "Pass 2: Autoregressive (Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 1.5,
        "parameters": "756M",
        "description": "Knowledge-distilled Whisper-Large-v3. Up to 6x faster inference speed with minimal error rate increase.",
        "recommended": False
    },
    # Pass 3A: Acoustic Anchor (Dense CTC)
    {
        "id": "nvidia/stt_en_conformer_ctc_xlarge",
        "name": "Conformer-CTC-xLarge",
        "role": "conformer",
        "role_display": "Pass 3A: Acoustic Anchor (Dense CTC)",
        "framework": "huggingface",
        "size_gb": 2.2,
        "parameters": "600M",
        "description": "NVIDIA 600M Conformer CTC model. Acts as a strict non-hallucinatory phonetic anchor.",
        "recommended": True
    },
    {
        "id": "nvidia/stt_en_conformer_ctc_large",
        "name": "Conformer-CTC-Large",
        "role": "conformer",
        "role_display": "Pass 3A: Acoustic Anchor (Dense CTC)",
        "framework": "huggingface",
        "size_gb": 0.8,
        "parameters": "120M",
        "description": "Standard 120M Conformer CTC acoustic model. Fast, lightweight anchor.",
        "recommended": False
    },
    {
        "id": "nvidia/stt_en_fastconformer_ctc_large",
        "name": "FastConformer-CTC-Large",
        "role": "conformer",
        "role_display": "Pass 3A: Acoustic Anchor (Dense CTC)",
        "framework": "huggingface",
        "size_gb": 0.86,
        "parameters": "115M",
        "description": "8x sub-sampled FastConformer CTC architecture with low latency.",
        "recommended": False
    },
    {
        "id": "nvidia/parakeet-ctc-1.1b",
        "name": "Parakeet-CTC-1.1B",
        "role": "conformer",
        "role_display": "Pass 3A: Acoustic Anchor (Dense CTC)",
        "framework": "huggingface",
        "size_gb": 2.3,
        "parameters": "1.1B",
        "description": "1.1B parameter NeMo Parakeet CTC model trained on 65,000 hours of English speech.",
        "recommended": False
    },
    {
        "id": "facebook/mms-1b-all",
        "name": "MMS-1B-All (Meta)",
        "role": "conformer",
        "role_display": "Pass 3A: Acoustic Anchor (Dense CTC)",
        "framework": "huggingface",
        "size_gb": 2.0,
        "parameters": "1.0B",
        "description": "Meta Massively Multilingual Speech (MMS) CTC acoustic model.",
        "recommended": False
    },
    # Pass 3B: Transducer (RNN-T / TDT)
    {
        "id": "nvidia/parakeet-tdt-1.1b",
        "name": "Parakeet-TDT-1.1B",
        "role": "parakeet",
        "role_display": "Pass 3B: Transducer (TDT Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 2.3,
        "parameters": "1.1B",
        "description": "NVIDIA Token-and-Duration Transducer model. Fast joint acoustic and linguistic verification.",
        "recommended": True
    },
    {
        "id": "nvidia/parakeet-tdt-0.6b",
        "name": "Parakeet-TDT-0.6B",
        "role": "parakeet",
        "role_display": "Pass 3B: Transducer (TDT Cross-Examiner)",
        "framework": "huggingface",
        "size_gb": 1.2,
        "parameters": "0.6B",
        "description": "Ultra-lightweight NVIDIA Token-and-Duration Transducer model with minimal VRAM and RAM footprint.",
        "recommended": False
    },
    # Diarization
    {
        "id": "titanet_large",
        "name": "TitaNet-Large",
        "role": "diarizer",
        "role_display": "Speaker Diarizer (NeMo Embeddings)",
        "framework": "nemo",
        "size_gb": 0.1,
        "parameters": "25M",
        "description": "NeMo 1D depth-wise separable convolution speaker embedding model for clustering turns.",
        "recommended": True
    },
    {
        "id": "pyannote/speaker-diarization-3.1",
        "name": "PyAnnote 3.1",
        "role": "diarizer",
        "role_display": "Speaker Diarizer (PyAnnote Audio)",
        "framework": "huggingface",
        "size_gb": 0.6,
        "parameters": "N/A",
        "description": "PyAnnote Audio neural end-to-end diarization pipeline (requires HF Token).",
        "recommended": False
    },
    # Stage 5: Supreme Audio Adjudicator
    {
        "id": "nvidia/Nemotron-Labs-Audex-2B",
        "name": "Nemotron-Labs-Audex-2B",
        "role": "audex",
        "role_display": "Stage 5: Supreme Audio Adjudicator",
        "framework": "huggingface",
        "size_gb": 4.2,
        "parameters": "2.0B",
        "description": "NVIDIA Dense Audio-Language Model with chain-of-thought <think> reasoning for acoustic dispute resolution.",
        "recommended": True
    }
]


class ModelManager:
    """
    Central manager for model discovery, local checkpoint inspection,
    sequential pipeline roster configuration, and checkpoint lifecycle.
    """

    def __init__(self):
        self.hf_cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        self.nemo_cache_dir = Path.home() / ".cache" / "torch" / "NeMo"
        self._lock = threading.Lock()
        
        # Download task tracking state
        self.install_state = {
            "is_downloading": False,
            "model_id": None,
            "framework": None,
            "role": None,
            "status": "idle",  # idle | downloading | completed | failed
            "progress": 0.0,
            "message": "Idle",
            "error": None,
            "started_at": None,
            "completed_at": None
        }

    def _get_path_size(self, p: Path) -> int:
        """Calculates disk usage of a path safely in bytes."""
        if not p.exists():
            return 0
        if p.is_file():
            try:
                return p.stat().st_size
            except OSError:
                return 0
        total = 0
        try:
            for item in p.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def get_active_roster(self) -> Dict[str, Any]:
        """Returns the active sequential council roster and enhancement settings."""
        return {
            "model_name": config.model_name,
            "whisper_model": config.whisper_model,
            "conformer_model": config.conformer_model,
            "parakeet_model": config.parakeet_model,
            "default_diarizer": config.default_diarizer,
            "nemo_diarizer_model": config.nemo_diarizer_model,
            "vocal_boost_level": config.vocal_boost_level,
            "enable_audex_adjudicator": config.enable_audex_adjudicator,
            "audex_model_id": config.audex_model_id
        }

    def update_active_roster(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Saves persistent model settings and updates the in-memory config."""
        saved = config.save_persistent_settings(updates)
        return self.get_active_roster()

    @property
    def hf_cache_dirs(self) -> List[Path]:
        dirs = []
        persistent_hub = config.hf_home / "hub"
        if persistent_hub.exists():
            dirs.append(persistent_hub)
        if config.hf_home.exists() and config.hf_home not in dirs:
            dirs.append(config.hf_home)
        legacy = Path.home() / ".cache" / "huggingface" / "hub"
        if legacy.exists() and legacy not in dirs:
            dirs.append(legacy)
        return dirs

    @property
    def nemo_cache_dirs(self) -> List[Path]:
        dirs = []
        if config.nemo_dir.exists():
            dirs.append(config.nemo_dir)
        legacy = Path.home() / ".cache" / "torch" / "NeMo"
        if legacy.exists() and legacy not in dirs:
            dirs.append(legacy)
        return dirs

    def list_installed_checkpoints(self) -> List[Dict[str, Any]]:
        """
        Inspects local Hugging Face and NeMo disk caches to discover installed models.
        """
        active_roster = self.get_active_roster()
        active_values = set(active_roster.values())
        checkpoints: List[Dict[str, Any]] = []
        seen_paths = set()

        # 1. Inspect Hugging Face hub caches
        for hf_dir in self.hf_cache_dirs:
            if hf_dir.exists():
                for p in hf_dir.glob("models--*"):
                    if p.is_dir() and str(p.resolve()) not in seen_paths:
                        seen_paths.add(str(p.resolve()))
                        sz = self._get_path_size(p)
                        # Filter out empty stub dirs (< 5 MB)
                        if sz > 5 * 1024 * 1024:
                            raw_id = p.name.replace("models--", "").replace("--", "/")
                            mtime = p.stat().st_mtime
                            
                            # Find corresponding role and display info from catalog
                            matched_preset = next((x for x in CATALOG_PRESETS if x["id"].lower() == raw_id.lower()), None)
                            role = matched_preset["role"] if matched_preset else "custom"
                            role_display = matched_preset["role_display"] if matched_preset else "Custom Checkpoint"
                            name = matched_preset["name"] if matched_preset else raw_id

                            checkpoints.append({
                                "id": raw_id,
                                "raw_name": p.name,
                                "name": name,
                                "framework": "huggingface",
                                "path": str(p),
                                "size_bytes": sz,
                                "size_mb": round(sz / (1024 * 1024), 1),
                                "size_gb": round(sz / (1024 ** 3), 2),
                                "last_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                                "is_active": (raw_id in active_values),
                                "role": role,
                                "role_display": role_display,
                                "removable": True
                            })

        # 2. Inspect NeMo caches
        for nemo_dir in self.nemo_cache_dirs:
            if nemo_dir.exists():
                for p in nemo_dir.rglob("*.nemo"):
                    if p.is_file() and str(p.resolve()) not in seen_paths:
                        seen_paths.add(str(p.resolve()))
                        sz = p.stat().st_size
                        if sz > 1 * 1024 * 1024:
                            stem = p.stem
                            mtime = p.stat().st_mtime
                            model_id = "titanet_large" if "titanet" in stem.lower() else stem
                            matched_preset = next((x for x in CATALOG_PRESETS if x["id"].lower() == model_id.lower() or stem in x["id"]), None)
                            role = matched_preset["role"] if matched_preset else "nemo_asr"
                            role_display = matched_preset["role_display"] if matched_preset else "NeMo Checkpoint"
                            name = matched_preset["name"] if matched_preset else stem

                            checkpoints.append({
                                "id": model_id,
                                "raw_name": p.name,
                                "name": name,
                                "framework": "nemo",
                                "path": str(p),
                                "size_bytes": sz,
                                "size_mb": round(sz / (1024 * 1024), 1),
                                "size_gb": round(sz / (1024 ** 3), 2),
                                "last_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                                "is_active": (model_id in active_values or stem in active_values),
                                "role": role,
                                "role_display": role_display,
                                "removable": True
                            })

        checkpoints.sort(key=lambda x: x["size_bytes"], reverse=True)
        return checkpoints

    def get_preset_catalog(self) -> List[Dict[str, Any]]:
        """
        Returns the curated catalog with an is_installed flag cross-referenced against local disk.
        """
        installed = self.list_installed_checkpoints()
        installed_ids = {item["id"].lower() for item in installed}
        
        # Check partial stem matches (e.g. titanet-l matches titanet_large)
        installed_raw = {item.get("raw_name", "").lower() for item in installed}

        catalog: List[Dict[str, Any]] = []
        for preset in CATALOG_PRESETS:
            pid = preset["id"].lower()
            is_inst = (pid in installed_ids)
            if not is_inst:
                clean_stem = pid.split("/")[-1]
                is_inst = any(clean_stem in raw for raw in installed_raw)

            entry = dict(preset)
            entry["is_installed"] = is_inst
            catalog.append(entry)

        return catalog

    def delete_checkpoint(self, checkpoint_id_or_path: str) -> Dict[str, Any]:
        """
        Safely removes a model directory or .nemo file from local disk cache.
        """
        target_path: Optional[Path] = None
        target_p = Path(checkpoint_id_or_path).resolve()

        # Check direct path
        if target_p.exists():
            target_path = target_p
        else:
            # Look up by ID in installed checkpoints
            for cp in self.list_installed_checkpoints():
                if cp["id"].lower() == checkpoint_id_or_path.lower() or cp["raw_name"] == checkpoint_id_or_path:
                    target_path = Path(cp["path"]).resolve()
                    break

        if not target_path or not target_path.exists():
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id_or_path}' not found in local caches.")

        # Security check: Ensure target is strictly inside allowed model cache dirs
        allowed_dirs = [d.resolve() for d in self.hf_cache_dirs + self.nemo_cache_dirs]
        if config.models_dir.exists():
            allowed_dirs.append(config.models_dir.resolve())
        is_safe = any(str(target_path).startswith(str(ad)) for ad in allowed_dirs)

        if not is_safe:
            raise PermissionError(f"Refusing to delete path outside cache directories: {target_path}")

        sz = self._get_path_size(target_path)

        # Execute removal
        if target_path.is_dir():
            shutil.rmtree(target_path, ignore_errors=False)
        else:
            target_path.unlink()

        gc.collect()
        return {
            "status": "deleted",
            "path": str(target_path),
            "reclaimed_bytes": sz,
            "reclaimed_mb": round(sz / (1024 * 1024), 1),
            "reclaimed_gb": round(sz / (1024 ** 3), 2)
        }

    def start_download_task(
        self,
        model_id: str,
        framework: str = "huggingface",
        role: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Starts an asynchronous background download of the specified model.
        """
        with self._lock:
            if self.install_state["is_downloading"]:
                return {
                    "status": "busy",
                    "message": f"Download already in progress for {self.install_state['model_id']}."
                }

            self.install_state.update({
                "is_downloading": True,
                "model_id": model_id,
                "framework": framework,
                "role": role,
                "status": "downloading",
                "progress": 5.0,
                "message": f"Contacting registry for {model_id}...",
                "error": None,
                "started_at": time.time(),
                "completed_at": None
            })

        worker = threading.Thread(
            target=self._run_download_worker,
            args=(model_id, framework, role),
            daemon=True
        )
        worker.start()

        return {
            "status": "started",
            "model_id": model_id,
            "message": f"Download started in background for {model_id}."
        }

    def _run_download_worker(self, model_id: str, framework: str, role: Optional[str]):
        """Executes model download in background thread."""
        try:
            self.install_state["message"] = f"Downloading checkpoint: {model_id}..."
            self.install_state["progress"] = 25.0

            if framework == "huggingface" or "/" in model_id:
                from huggingface_hub import snapshot_download
                snapshot_download(
                    repo_id=model_id,
                    token=config.hf_token,
                    local_files_only=False
                )
            elif framework == "nemo":
                import nemo.collections.asr as nemo_asr
                model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_id, map_location="cpu")
                del model
                gc.collect()

            self.install_state["status"] = "completed"
            self.install_state["progress"] = 100.0
            self.install_state["message"] = f"Successfully downloaded and verified {model_id}."
            self.install_state["completed_at"] = time.time()
        except Exception as exc:
            self.install_state["status"] = "failed"
            self.install_state["error"] = str(exc)
            self.install_state["message"] = f"Download failed for {model_id}: {exc}"
        finally:
            self.install_state["is_downloading"] = False

    def get_download_status(self) -> Dict[str, Any]:
        """Returns the current status of background model download task."""
        return dict(self.install_state)


model_manager = ModelManager()
