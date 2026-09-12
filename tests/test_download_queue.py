"""
Unit tests for Model Download Queue, Real-Time Progress / Speed Tracking,
Conformer NGC Routing, and Hugging Face Token Auto-Verification Caching.
"""

import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from transcript_suite.asr.model_manager import (
    ModelManager,
    CATALOG_PRESETS,
    format_bytes,
    format_seconds,
    model_manager
)
from transcript_suite.diarization.pyannote import (
    verify_pyannote_access,
    auto_verify_on_startup
)
from transcript_suite.web.app import app


def test_conformer_ngc_routing_in_presets():
    """Verify Conformer CTC presets are configured for NeMo framework without nvidia/ prefix."""
    conformer_presets = [p for p in CATALOG_PRESETS if "conformer" in p["id"]]
    assert len(conformer_presets) >= 3

    for cp in conformer_presets:
        assert cp["framework"] == "nemo", f"{cp['id']} should have framework 'nemo', got '{cp['framework']}'"
        assert not cp["id"].startswith("nvidia/"), f"{cp['id']} should not contain 'nvidia/' prefix"

    xlarge = next(p for p in conformer_presets if p["id"] == "stt_en_conformer_ctc_xlarge")
    assert xlarge["name"] == "Conformer-CTC-xLarge"
    assert xlarge["framework"] == "nemo"


def test_format_helpers():
    """Verify byte and time duration formatters."""
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024 * 512) == "512.0 KB"
    assert format_bytes(1024 * 1024 * 250) == "250.0 MB"
    assert format_bytes(int(1024 ** 3 * 3.12)) == "3.12 GB"

    assert format_seconds(45) == "45s"
    assert format_seconds(88) == "1m 28s"
    assert format_seconds(3665) == "1h 01m"


def test_download_queue_sequential_advance():
    """Verify that multiple downloads are queued sequentially and auto-advance."""
    mm = ModelManager()

    # Prevent real network downloads during unit test
    with patch.object(mm, "_run_download_worker") as mock_worker:
        # 1. Start first download
        res1 = mm.start_download_task("openai/whisper-large-v3", framework="huggingface")
        assert res1["status"] == "started"
        assert mm.install_state["is_downloading"] is True
        assert mm.install_state["model_id"] == "openai/whisper-large-v3"
        assert len(mm.download_queue) == 0

        # 2. Queue second download
        res2 = mm.start_download_task("stt_en_conformer_ctc_xlarge", framework="nemo")
        assert res2["status"] == "queued"
        assert res2["position"] == 1
        assert len(mm.download_queue) == 1
        assert mm.download_queue[0]["model_id"] == "stt_en_conformer_ctc_xlarge"

        # 3. Queue third download
        res3 = mm.start_download_task("nvidia/canary-qwen-2.5b", framework="huggingface")
        assert res3["status"] == "queued"
        assert res3["position"] == 2
        assert len(mm.download_queue) == 2

        # Verify status payload
        status = mm.get_download_status()
        assert status["is_downloading"] is True
        assert status["model_id"] == "openai/whisper-large-v3"
        assert len(status["queue"]) == 2
        assert status["queue"][0]["model_id"] == "stt_en_conformer_ctc_xlarge"
        assert status["queue"][0]["position"] == 1
        assert status["queue"][1]["model_id"] == "nvidia/canary-qwen-2.5b"
        assert status["queue"][1]["position"] == 2

        # 4. Duplicate request check
        res_dup = mm.start_download_task("stt_en_conformer_ctc_xlarge")
        assert res_dup["status"] == "already_queued"

        res_active_dup = mm.start_download_task("openai/whisper-large-v3")
        assert res_active_dup["status"] == "already_downloading"

        # 5. Cancel a queued item (canary)
        cancel_res = mm.cancel_download("nvidia/canary-qwen-2.5b")
        assert cancel_res["status"] == "removed_from_queue"
        assert len(mm.download_queue) == 1
        assert mm.download_queue[0]["model_id"] == "stt_en_conformer_ctc_xlarge"

        # 6. Cancel active download (whisper) -> auto-advances conformer to active
        cancel_active = mm.cancel_download("openai/whisper-large-v3")
        assert cancel_active["status"] == "cancelled"
        # The next item from queue should now be active
        assert mm.install_state["model_id"] == "stt_en_conformer_ctc_xlarge"
        assert mm.install_state["is_downloading"] is True
        assert len(mm.download_queue) == 0


def test_conformer_id_normalization():
    """Verify passing nvidia/stt_... automatically normalizes to nemo framework without prefix."""
    mm = ModelManager()
    with patch.object(mm, "_run_download_worker"):
        res = mm.start_download_task("nvidia/stt_en_conformer_ctc_xlarge", framework="huggingface")
        assert res["model_id"] == "stt_en_conformer_ctc_xlarge"
        assert mm.install_state["framework"] == "nemo"
        assert mm.install_state["model_id"] == "stt_en_conformer_ctc_xlarge"


def test_progress_telemetry_calculations():
    """Verify byte updates correctly calculate progress percentage, speed, and ETA."""
    mm = ModelManager()
    mm.install_state.update({
        "is_downloading": True,
        "model_id": "test_model",
        "downloaded_bytes": 0,
        "total_bytes": 100 * 1024 * 1024,  # 100 MB
        "progress": 0.0
    })

    # Simulate arrival of 20 MB at t=0
    mm._speed_history.append((time.time() - 2.0, 0))
    mm.on_bytes_downloaded(20 * 1024 * 1024)

    status = mm.get_download_status()
    assert status["downloaded_bytes"] == 20 * 1024 * 1024
    assert status["progress"] == 20.0
    assert "20.0 MB / 100.0 MB" in status["downloaded_str"]
    assert status["speed_mb_s"] > 0
    assert status["eta_seconds"] is not None
    assert status["eta_seconds"] > 0


def test_pyannote_token_verification_caching():
    """Verify Hugging Face token verification results are cached and not re-fetched needlessly."""
    test_token = "hf_mock_test_token_123456789"

    with patch("huggingface_hub.HfApi.whoami") as mock_whoami, \
         patch("huggingface_hub.hf_hub_download") as mock_download:
        mock_whoami.return_value = {"name": "TestUser", "preferred_username": "TestUser"}
        mock_download.return_value = "/tmp/dummy/config.yaml"

        # 1. First call -> executes API check
        res1 = verify_pyannote_access(test_token, force_refresh=True)
        assert res1["token_valid"] is True
        assert res1["username"] == "TestUser"
        assert res1["ready"] is True
        assert mock_whoami.call_count == 1

        # 2. Second call with same token -> uses cache without calling whoami again
        res2 = verify_pyannote_access(test_token, force_refresh=False)
        assert res2["username"] == "TestUser"
        assert res2["ready"] is True
        assert mock_whoami.call_count == 1  # Not called again!

        # 3. Third call with force_refresh -> calls API again
        res3 = verify_pyannote_access(test_token, force_refresh=True)
        assert mock_whoami.call_count == 2


def test_auto_verify_on_startup_background():
    """Verify auto_verify_on_startup initiates without errors."""
    with patch("transcript_suite.diarization.pyannote.verify_pyannote_access") as mock_verify:
        auto_verify_on_startup()
        time.sleep(0.1)
        # Verifier runs daemon thread cleanly


def test_web_api_download_and_queue_endpoints():
    """Verify FastAPI routes for model install, queue status, and cancellation."""
    client = TestClient(app)

    with patch.object(model_manager, "_run_download_worker"):
        # Reset state
        model_manager.cancel_download()
        model_manager.download_queue.clear()
        model_manager.install_state["is_downloading"] = False

        # 1. Install first model
        r1 = client.post("/api/models/install", json={"model_id": "openai/whisper-large-v3-turbo"})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "started"

        # 2. Queue second model
        r2 = client.post("/api/models/install", json={"model_id": "stt_en_conformer_ctc_large"})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "queued"
        assert d2["position"] == 1

        # 3. Check status endpoint
        st_res = client.get("/api/models/install/status")
        assert st_res.status_code == 200
        st = st_res.json()
        assert st["is_downloading"] is True
        assert len(st["queue"]) == 1

        # 4. Cancel queued model
        c_res = client.post("/api/models/install/cancel", json={"model_id": "stt_en_conformer_ctc_large"})
        assert c_res.status_code == 200
        assert c_res.json()["status"] == "removed_from_queue"

        # 5. Cancel active model
        c_active = client.post("/api/models/install/cancel", json={"model_id": "openai/whisper-large-v3-turbo"})
        assert c_active.status_code == 200
        assert c_active.json()["status"] == "cancelled"
