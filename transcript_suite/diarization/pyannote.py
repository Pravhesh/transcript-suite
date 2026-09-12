"""
Route B (Optional): PyAnnote Audio 3.x Speaker Diarization.
Requires pyannote.audio package and a valid Hugging Face token (HF_TOKEN).
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
import torch
import soundfile as sf
from .base import BaseDiarizer, SpeakerTurn


def ensure_pyannote_compatibility():
    """
    Applies compatibility shims for torchaudio 2.10+ / 2.11+ where AudioMetaData
    and list_audio_backends were removed from the public module root,
    patches huggingface_hub for use_auth_token deprecation,
    and patches torch.load for PyTorch 2.6+ unpickling of pyannote task specifications.
    """
    import torchaudio
    if not hasattr(torchaudio, "AudioMetaData"):
        from dataclasses import dataclass
        @dataclass
        class AudioMetaData:
            sample_rate: int = 16000
            num_frames: int = 0
            num_channels: int = 1
            bits_per_sample: int = 16
            encoding: str = "PCM_S"
        torchaudio.AudioMetaData = AudioMetaData

    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]

    try:
        import huggingface_hub
        if not getattr(huggingface_hub, "_hf_hub_download_compat_patched", False):
            orig_hf_hub_download = huggingface_hub.hf_hub_download
            def patched_hf_hub_download(*args, **kwargs):
                if "use_auth_token" in kwargs:
                    kwargs["token"] = kwargs.pop("use_auth_token")
                return orig_hf_hub_download(*args, **kwargs)
            huggingface_hub.hf_hub_download = patched_hf_hub_download
            huggingface_hub._hf_hub_download_compat_patched = True
    except Exception:
        pass

    try:
        if not getattr(torch, "_pyannote_torch_load_patched", False):
            orig_torch_load = torch.load
            def patched_torch_load(*args, **kwargs):
                if "weights_only" not in kwargs:
                    kwargs["weights_only"] = False
                return orig_torch_load(*args, **kwargs)
            torch.load = patched_torch_load
            torch._pyannote_torch_load_patched = True
    except Exception:
        pass


import threading
import time

_cached_verification_result: Optional[Dict[str, Any]] = None
_cached_verification_token: Optional[str] = None
_cached_verification_time: float = 0.0
_verification_lock = threading.Lock()


def verify_pyannote_access(token: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Checks if Hugging Face token is valid and grants access to pyannote models.
    Caches verification for 5 minutes unless force_refresh is True.
    """
    global _cached_verification_result, _cached_verification_token, _cached_verification_time
    token = token or os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")

    with _verification_lock:
        now = time.time()
        if not force_refresh and _cached_verification_result is not None:
            if token == _cached_verification_token and (now - _cached_verification_time < 300):
                return dict(_cached_verification_result)

    result: Dict[str, Any] = {
        "installed": True,
        "token_provided": bool(token),
        "token": token or "",
        "masked_token": f"{token[:4]}...{token[-4:]}" if token and len(token) > 8 else ("****" if token else ""),
        "token_valid": False,
        "username": None,
        "diarization_access": False,
        "segmentation_access": False,
        "ready": False,
        "message": ""
    }
    try:
        ensure_pyannote_compatibility()
        import pyannote.audio  # noqa: F401
    except Exception as e:
        result["installed"] = False
        result["message"] = f"pyannote.audio not available: {e}"
        with _verification_lock:
            _cached_verification_result = dict(result)
            _cached_verification_token = token
            _cached_verification_time = time.time()
        return result

    if not token:
        result["message"] = "No Hugging Face token configured. Provide token to enable PyAnnote."
        with _verification_lock:
            _cached_verification_result = dict(result)
            _cached_verification_token = token
            _cached_verification_time = time.time()
        return result

    try:
        from huggingface_hub import HfApi, hf_hub_download
        api = HfApi()
        user_info = api.whoami(token=token)
        result["token_valid"] = True
        result["username"] = user_info.get("name") or user_info.get("preferred_username")

        # Test diarization-3.1
        try:
            hf_hub_download(repo_id="pyannote/speaker-diarization-3.1", filename="config.yaml", token=token)
            result["diarization_access"] = True
        except Exception:
            result["diarization_access"] = False

        # Test segmentation-3.0
        try:
            hf_hub_download(repo_id="pyannote/segmentation-3.0", filename="config.yaml", token=token)
            result["segmentation_access"] = True
        except Exception:
            result["segmentation_access"] = False

        if result["diarization_access"] and result["segmentation_access"]:
            result["ready"] = True
            result["message"] = f"PyAnnote is verified and ready for @{result['username']}."
        elif not result["diarization_access"]:
            result["message"] = "Token is valid, but access to pyannote/speaker-diarization-3.1 is required. Accept agreement on Hugging Face."
        elif not result["segmentation_access"]:
            result["message"] = "Token is valid, but access to pyannote/segmentation-3.0 is required. Accept agreement on Hugging Face."
    except Exception as e:
        result["message"] = f"Token validation failed: {e}"

    with _verification_lock:
        _cached_verification_result = dict(result)
        _cached_verification_token = token
        _cached_verification_time = time.time()

    return result


def auto_verify_on_startup():
    """
    Asynchronously verifies the configured Hugging Face token in a background thread
    on server boot so the status is cached before user interactions.
    """
    def _worker():
        try:
            from ..config import config
            tok = getattr(config, "hf_token", None) or os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
            if tok:
                verify_pyannote_access(tok, force_refresh=True)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True, name="pyannote-startup-verifier")
    t.start()


class PyAnnoteDiarizer(BaseDiarizer):
    def __init__(self, hf_token: Optional[str] = None, device: str = "cuda" if torch.cuda.is_available() else "cpu"):
        tok = hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
        if not tok:
            try:
                from ..config import config
                tok = getattr(config, "hf_token", None)
            except Exception:
                pass
        self.hf_token = tok
        self.device = device
        self.pipeline = None
        self._is_loaded = False

    def _load_pipeline(self):
        if self._is_loaded:
            return

        if not self.hf_token:
            raise ValueError(
                "PyAnnote requires a Hugging Face token. Provide it via HF_TOKEN environment variable "
                "or configure it in the Models & Checkpoints tab."
            )

        ensure_pyannote_compatibility()

        try:
            from pyannote.audio import Pipeline
            try:
                self.pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    token=self.hf_token
                )
            except TypeError:
                self.pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self.hf_token
                )
            if self.device.startswith("cuda") and torch.cuda.is_available():
                self.pipeline.to(torch.device(self.device))
            self._is_loaded = True
            print("[PyAnnote] Pipeline successfully loaded.")
        except ImportError as e:
            raise ImportError(f"pyannote.audio is not installed: {e}") from e
        except Exception as e:
            err_msg = str(e)
            if any(k in err_msg.lower() for k in ["gated", "401", "403", "restricted"]):
                raise PermissionError(
                    "Access denied to PyAnnote models. Please accept agreements on Hugging Face:\n"
                    "1. https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                    "2. https://huggingface.co/pyannote/segmentation-3.0\n"
                    "and ensure your HF token has Read permissions."
                ) from e
            raise RuntimeError(f"Failed to load PyAnnote pipeline: {e}") from e

    def diarize(self, waveform: torch.Tensor, sample_rate: int = 16000) -> List[SpeakerTurn]:
        """
        Runs pyannote speaker diarization on audio waveform.
        Accepts in-memory waveform tensor directly, falling back to temp WAV only if needed.
        """
        self._load_pipeline()

        if waveform.dim() == 1:
            wf_in = waveform.unsqueeze(0)
        elif waveform.dim() > 2:
            wf_in = waveform.squeeze()
            if wf_in.dim() == 1:
                wf_in = wf_in.unsqueeze(0)
        else:
            wf_in = waveform

        # Ensure tensor is on CPU float32 for pyannote pipeline dictionary protocol
        audio_input = {
            "waveform": wf_in.cpu().float(),
            "sample_rate": sample_rate
        }

        try:
            diarization_result = self.pipeline(audio_input)
        except Exception as e:
            print(f"[PyAnnote] Direct tensor input failed ({e}), falling back to disk file...")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                wav_path = tmp.name
                audio_np = wf_in.squeeze(0).cpu().numpy()
                sf.write(wav_path, audio_np, sample_rate, subtype="PCM_16")
                diarization_result = self.pipeline(wav_path)

        speaker_map = {}
        speaker_turns: List[SpeakerTurn] = []

        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            if speaker not in speaker_map:
                speaker_map[speaker] = f"Speaker {len(speaker_map)}"
            normalized_spk = speaker_map[speaker]
            speaker_turns.append(SpeakerTurn(start=turn.start, end=turn.end, speaker=normalized_spk))

        return speaker_turns

    def unload(self):
        """
        Releases pipeline memory and CUDA tensors.
        """
        if self.pipeline is not None:
            del self.pipeline
            self.pipeline = None
            self._is_loaded = False
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print("[PyAnnote] Pipeline successfully unloaded.")
