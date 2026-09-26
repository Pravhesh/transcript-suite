"""
Unit tests for ModelManager and Checkpoint Management.
"""

import pytest
from pathlib import Path
from transcript_suite.asr.model_manager import ModelManager, CATALOG_PRESETS
from transcript_suite.config import config


def test_model_manager_roster():
    mm = ModelManager()
    roster = mm.get_active_roster()
    assert "model_name" in roster
    assert "whisper_model" in roster
    assert "conformer_model" in roster
    assert "parakeet_model" in roster
    assert "vocal_boost_level" in roster


def test_model_manager_presets_catalog():
    mm = ModelManager()
    presets = mm.get_preset_catalog()
    assert len(presets) >= 10
    
    roles = {p["role"] for p in presets}
    assert "speech_llm" in roles
    assert "whisper" in roles
    assert "conformer" in roles
    assert "parakeet" in roles
    assert "diarizer" in roles

    for p in presets:
        assert "id" in p
        assert "name" in p
        assert "framework" in p
        assert "is_installed" in p
        assert isinstance(p["is_installed"], bool)


def test_model_manager_list_checkpoints():
    mm = ModelManager()
    checkpoints = mm.list_installed_checkpoints()
    assert isinstance(checkpoints, list)
    
    if checkpoints:
        cp = checkpoints[0]
        assert "id" in cp
        assert "name" in cp
        assert "framework" in cp
        assert "size_bytes" in cp
        assert "size_gb" in cp
        assert "is_active" in cp


def test_model_manager_security_delete_guard():
    mm = ModelManager()
    # Attempting to delete a file outside cache dirs must raise PermissionError
    with pytest.raises(PermissionError):
        mm.delete_checkpoint("/etc/hosts")

    with pytest.raises(FileNotFoundError):
        mm.delete_checkpoint("non_existent_fake_checkpoint_12345")


def test_model_manager_update_roster(tmp_path):
    mm = ModelManager()
    updated = mm.update_active_roster({
        "vocal_boost_level": "max"
    })
    assert updated["vocal_boost_level"] == "max"
    assert config.vocal_boost_level == "max"

    # Reset back to adaptive
    mm.update_active_roster({"vocal_boost_level": "adaptive"})
    assert config.vocal_boost_level == "adaptive"
