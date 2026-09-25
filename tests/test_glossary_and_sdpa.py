"""
Tests for Sub-Phase 2.1:
- Feature 2.1.A: Settings-Controlled SDPA / FlashAttention-2 Toggle
- Feature 2.1.B: Custom Phonetic & Domain Glossary Biasing
"""

import pytest
import torch
from unittest.mock import MagicMock, patch
from pathlib import Path
from fastapi.testclient import TestClient

from transcript_suite.config import config, SuiteConfig
from transcript_suite.asr.canary import CanaryQwenTranscriber, sanitize_canary_output
from transcript_suite.asr.council import ModelCouncil
from transcript_suite.pipeline import TranscriptionPipeline
from transcript_suite.web.app import app


def test_config_sdpa_and_glossary_persistence(tmp_path):
    test_cfg = SuiteConfig(base_dir=tmp_path / "ts_data")
    assert test_cfg.attention_backend == "sdpa"
    assert test_cfg.enable_sdpa is True
    assert test_cfg.custom_glossary == []

    # Test glossary update & deduplication
    updated = test_cfg.update_glossary(["Kubernetes", "PyTorch", "kubernetes", "Docker"])
    assert "Kubernetes" in updated
    assert "PyTorch" in updated
    assert "Docker" in updated
    assert len(updated) == 3  # Case-insensitive dedup

    # Test round-trip persistence
    test_cfg.attention_backend = "flash_attention_2"
    test_cfg.enable_sdpa = True
    test_cfg.save_persistent_settings({
        "attention_backend": "flash_attention_2",
        "custom_glossary": updated
    })

    loaded = test_cfg.load_persistent_settings()
    assert loaded.get("attention_backend") == "flash_attention_2"
    assert "Kubernetes" in loaded.get("custom_glossary", [])

    # Test clear glossary
    cleared = test_cfg.clear_glossary()
    assert cleared == []
    assert test_cfg.get_glossary() == []


def test_canary_sdpa_context_and_glossary_conditioning():
    transcriber = CanaryQwenTranscriber(
        device="cpu",
        dtype=torch.float32,
        attention_backend="sdpa",
        glossary=["PostgreSQL", "LoRA"]
    )
    assert transcriber.attention_backend == "sdpa"
    assert transcriber.glossary == ["PostgreSQL", "LoRA"]

    # Test context manager produces valid context without error
    with transcriber.get_sdp_context():
        pass

    # Test output sanitization strips hallucinated glossary prefixes
    raw_echoed = "Vocabulary glossary: PostgreSQL, LoRA. Transcribe the following: We deployed PostgreSQL using LoRA."
    sanitized = sanitize_canary_output(raw_echoed)
    assert sanitized == "We deployed PostgreSQL using LoRA."

    raw_echoed_2 = "Transcribe the following: The query succeeded."
    sanitized_2 = sanitize_canary_output(raw_echoed_2)
    assert sanitized_2 == "The query succeeded."


def test_whisper_sdpa_and_glossary_prompt_ids():
    council = ModelCouncil(
        whisper_model_id="openai/whisper-tiny",
        attention_backend="sdpa",
        glossary=["FastAPI", "Uvicorn"]
    )
    assert council.attention_backend == "sdpa"
    assert council.glossary == ["FastAPI", "Uvicorn"]

    # Mock pipeline and tokenizer
    mock_pipe = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.get_prompt_ids.return_value = [50258, 50259, 1234, 5678]
    mock_pipe.tokenizer = mock_tokenizer
    mock_pipe.return_value = [{"text": "Testing FastAPI with Uvicorn."}]

    council._whisper_pipeline = mock_pipe

    # Transcribe single chunk
    result = council.transcribe_with_whisper("dummy_path.wav")
    assert result == "Testing FastAPI with Uvicorn."
    mock_tokenizer.get_prompt_ids.assert_called_with("FastAPI, Uvicorn")
    call_kwargs = mock_pipe.call_args[1]
    assert call_kwargs["generate_kwargs"]["prompt_ids"] == [50258, 50259, 1234, 5678]

    # Test batch transcription with glossary
    mock_pipe.side_effect = None
    mock_pipe.return_value = iter([{"text": "Hypothesis 1"}, {"text": "Hypothesis 2"}])
    batch_results = council.transcribe_batch_whisper(["file1.wav", "file2.wav"], batch_size=2)
    assert len(batch_results) == 2
    mock_tokenizer.get_prompt_ids.assert_called_with("FastAPI, Uvicorn")


def test_pipeline_sdpa_and_glossary_forwarding():
    with patch("transcript_suite.pipeline.AudioLoader"), \
         patch("transcript_suite.pipeline.GPUSpeechEnhancer"), \
         patch("transcript_suite.pipeline.AmbiguityResolver"), \
         patch("transcript_suite.pipeline.SileroVADSegmenter"), \
         patch("transcript_suite.pipeline.CanaryQwenTranscriber") as MockCanary, \
         patch("transcript_suite.pipeline.ModelCouncil") as MockCouncil, \
         patch("transcript_suite.pipeline.NeMoTitaNetDiarizer"):

        pipe = TranscriptionPipeline(
            attention_backend="flash_attention_2",
            custom_glossary=["Kubernetes", "Audex"]
        )

        assert pipe.attention_backend == "flash_attention_2"
        assert pipe.custom_glossary == ["Kubernetes", "Audex"]

        MockCanary.assert_called_once()
        canary_kwargs = MockCanary.call_args[1]
        assert canary_kwargs["attention_backend"] == "flash_attention_2"
        assert canary_kwargs["glossary"] == ["Kubernetes", "Audex"]

        MockCouncil.assert_called_once()
        council_kwargs = MockCouncil.call_args[1]
        assert council_kwargs["attention_backend"] == "flash_attention_2"
        assert council_kwargs["glossary"] == ["Kubernetes", "Audex"]


def test_web_api_glossary_and_settings_endpoints():
    client = TestClient(app)

    # 1. Clear glossary initially
    res_del_all = client.delete("/api/glossary?all=true")
    assert res_del_all.status_code == 200
    assert res_del_all.json()["glossary"] == []

    # 2. Add terms to glossary
    res_add = client.post("/api/glossary", json={"terms": ["Docker", "Kubernetes", "FastAPI"]})
    assert res_add.status_code == 200
    data = res_add.json()
    assert data["status"] == "success"
    assert "Docker" in data["glossary"]
    assert data["count"] == 3

    # 3. Get glossary
    res_get = client.get("/api/glossary")
    assert res_get.status_code == 200
    assert res_get.json()["count"] == 3

    # 4. Remove one term via JSON payload
    res_del_one = client.request("DELETE", "/api/glossary", json={"term": "docker"})
    assert res_del_one.status_code == 200
    assert "Docker" not in res_del_one.json()["glossary"]
    assert res_del_one.json()["count"] == 2

    # 4b. Remove another term via query param
    res_del_query = client.delete("/api/glossary?term=fastapi")
    assert res_del_query.status_code == 200
    assert "FastAPI" not in res_del_query.json()["glossary"]
    assert res_del_query.json()["count"] == 1

    # 5. Check GET /api/settings includes attention_backend and custom_glossary
    res_settings = client.get("/api/settings")
    assert res_settings.status_code == 200
    s = res_settings.json()["settings"]
    assert "attention_backend" in s
    assert "enable_sdpa" in s
    assert "custom_glossary" in s
    assert len(s["custom_glossary"]) == 1

    # 6. Update attention_backend via POST /api/settings
    res_update_settings = client.post("/api/settings", json={"attention_backend": "sdpa"})
    assert res_update_settings.status_code == 200
    assert res_update_settings.json()["settings"]["attention_backend"] == "sdpa"
