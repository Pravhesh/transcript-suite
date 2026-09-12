"""
Tests for PyAnnote Audio compatibility shims, Hugging Face verification, and lifecycle.
"""

import pytest
from unittest.mock import patch, MagicMock
from transcript_suite.diarization.pyannote import (
    ensure_pyannote_compatibility,
    verify_pyannote_access,
    PyAnnoteDiarizer
)


def test_ensure_pyannote_compatibility():
    """Verify torchaudio compatibility shims are properly registered."""
    import torchaudio
    ensure_pyannote_compatibility()

    assert hasattr(torchaudio, "AudioMetaData")
    meta = torchaudio.AudioMetaData(sample_rate=16000, num_channels=1)
    assert meta.sample_rate == 16000
    assert meta.num_channels == 1

    assert hasattr(torchaudio, "list_audio_backends")
    backends = torchaudio.list_audio_backends()
    assert isinstance(backends, list)
    assert len(backends) > 0


def test_verify_pyannote_access_no_token():
    """Verify status report when no Hugging Face token is provided."""
    res = verify_pyannote_access(token=None)
    assert res["installed"] is True
    assert res["token_provided"] is False
    assert res["ready"] is False
    assert "No Hugging Face token configured" in res["message"]


def test_verify_pyannote_access_mocked():
    """Verify status report when Hugging Face API validates successfully."""
    with patch("huggingface_hub.HfApi") as mock_hf_api, \
         patch("huggingface_hub.hf_hub_download") as mock_download:
        mock_instance = MagicMock()
        mock_instance.whoami.return_value = {"name": "testuser", "preferred_username": "testuser"}
        mock_hf_api.return_value = mock_instance
        mock_download.return_value = "/tmp/fake_config.yaml"

        res = verify_pyannote_access(token="hf_mock_token_123")
        assert res["installed"] is True
        assert res["token_provided"] is True
        assert res["token_valid"] is True
        assert res["username"] == "testuser"
        assert res["diarization_access"] is True
        assert res["segmentation_access"] is True
        assert res["ready"] is True


def test_pyannote_diarizer_lifecycle():
    """Verify PyAnnoteDiarizer instance initialization and unload."""
    diarizer = PyAnnoteDiarizer(hf_token="hf_dummy_token", device="cpu")
    assert diarizer.hf_token == "hf_dummy_token"
    assert diarizer.pipeline is None
    assert diarizer._is_loaded is False

    # Calling unload when not loaded should safely no-op
    diarizer.unload()
    assert diarizer.pipeline is None
