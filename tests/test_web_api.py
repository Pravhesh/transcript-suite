"""
FastAPI endpoints and Web UI asset test.
"""

from fastapi.testclient import TestClient
from transcript_suite.web.app import app

def test_web_endpoints():
    client = TestClient(app)

    # 1. Test root UI
    res = client.get("/")
    assert res.status_code == 200
    assert "Transcript Suite" in res.text
    assert "Foggy Woodland" in res.text
    print("✓ Root Web UI endpoint served successfully.")

    # 2. Test VRAM telemetry
    res_vram = client.get("/api/vram")
    assert res_vram.status_code == 200
    data = res_vram.json()
    assert "total_gb" in data
    print(f"✓ /api/vram returned telemetry: {data}")

    # 3. Test static assets
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    assert "foggy-woodland" in res_css.text
    print("✓ Static CSS stylesheet loaded successfully.")

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    print("✓ Static JS script loaded successfully.")

def test_task_control_endpoints():
    from unittest.mock import MagicMock
    import threading
    from transcript_suite.web.app import TASK_CONTROLS, TASKS
    client = TestClient(app)

    task_id = "test-task-123"
    TASK_CONTROLS[task_id] = {
        "pause_event": threading.Event(),
        "stop_event": threading.Event(),
        "pipeline": None
    }
    TASK_CONTROLS[task_id]["pause_event"].set()
    TASKS[task_id] = {"status": "processing", "message": "Running"}

    # Test Pause
    res = client.post(f"/api/tasks/{task_id}/pause")
    assert res.status_code == 200
    assert not TASK_CONTROLS[task_id]["pause_event"].is_set()
    assert TASKS[task_id]["status"] == "paused"
    print("✓ Task pause endpoint verified.")

    # Test Resume
    res = client.post(f"/api/tasks/{task_id}/resume")
    assert res.status_code == 200
    assert TASK_CONTROLS[task_id]["pause_event"].is_set()
    assert TASKS[task_id]["status"] == "processing"
    print("✓ Task resume endpoint verified.")

    # Test Stop
    res = client.post(f"/api/tasks/{task_id}/stop")
    assert res.status_code == 200
    assert TASK_CONTROLS[task_id]["stop_event"].is_set()
    assert TASKS[task_id]["status"] == "stopped"
    print("✓ Task stop endpoint verified.")

def test_audio_stream_endpoints():
    import tempfile
    import numpy as np
    import soundfile as sf
    from pathlib import Path
    from transcript_suite.web.app import TASKS

    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        orig_audio = tmp_path / "orig.wav"
        processed_audio = tmp_path / "processed.wav"
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        slow_chunk = chunks_dir / "chunk_0_slow.wav"

        sr = 16000
        data = np.zeros(sr, dtype=np.float32)
        sf.write(str(orig_audio), data, sr, subtype="PCM_16")
        sf.write(str(processed_audio), data, sr, subtype="PCM_16")
        sf.write(str(slow_chunk), data[:8000], sr, subtype="PCM_16")

        task_id = "test-audio-task"
        TASKS[task_id] = {
            "file_path": str(orig_audio),
            "processed_file_path": str(processed_audio),
            "chunks_dir": str(chunks_dir),
            "segments": [
                {"start": 0.0, "end": 0.5, "speaker": "Speaker 0", "text": "Test chunk"}
            ]
        }

        # 1. Test original audio endpoint
        res_orig = client.get(f"/api/audio/{task_id}")
        assert res_orig.status_code == 200

        # 2. Test processed model audio endpoint
        res_proc = client.get(f"/api/audio/{task_id}/processed")
        assert res_proc.status_code == 200

        # 3. Test chunk audio endpoint
        res_chunk = client.get(f"/api/audio/{task_id}/chunk/0")
        assert res_chunk.status_code == 200

        print("✓ Original, processed, and chunk audio stream endpoints verified.")


def test_telemetry_endpoints():
    from transcript_suite.web.app import add_log, add_trace_sample
    client = TestClient(app)

    # Add sample log and trace
    add_log("test-telemetry-task", "INFO", "Sample task initialized.")
    add_log("test-telemetry-task", "STAGE", "Stage 1: Speech Enhancer active.")
    add_log("test-telemetry-task", "CHUNK", "Transcribed chunk 1/10: 'Hello world'")
    add_trace_sample("test-telemetry-task")

    # 1. Trace endpoint with cadence intervals (2s, 5s, 10s)
    for interval in [2, 5, 10]:
        res_trace = client.get(f"/api/telemetry/trace?interval={interval}&task_id=test-telemetry-task")
        assert res_trace.status_code == 200
        data = res_trace.json()
        assert "samples" in data
        assert "current" in data
        assert "peak" in data
        assert "peak_vram_gb" in data["peak"]
        assert "peak_ram_gb" in data["peak"]
        assert data["interval_seconds"] == interval

        if len(data["samples"]) > 0:
            sample = data["samples"][-1]
            assert "app_ram_gb" in sample
            assert "proc_ram_used_gb" in sample
            assert "elapsed_str" in sample

    # Check /api/vram aliases
    res_vram = client.get("/api/vram")
    assert res_vram.status_code == 200
    vram_data = res_vram.json()
    assert "app_ram_rss_gb" in vram_data
    assert "proc_ram_used_gb" in vram_data

    print("✓ /api/telemetry/trace verified with 2s, 5s, and 10s intervals and peak stats.")

    # 2. Logs endpoint with level filter
    res_logs = client.get("/api/telemetry/logs?level=ALL")
    assert res_logs.status_code == 200
    logs_data = res_logs.json()
    assert len(logs_data["logs"]) > 0

    res_logs_stage = client.get("/api/telemetry/logs?level=STAGE")
    assert res_logs_stage.status_code == 200
    for l in res_logs_stage.json()["logs"]:
        assert l["level"] == "STAGE"

    print("✓ /api/telemetry/logs verified with level filtering.")

    # 3. Export Trace as CSV
    res_csv = client.get("/api/telemetry/export/trace.csv")
    assert res_csv.status_code == 200
    assert res_csv.headers["content-type"].startswith("text/csv")
    assert "Timestamp,Time,Elapsed_Sec" in res_csv.text
    print("✓ /api/telemetry/export/trace.csv export verified.")

    # 4. Export Logs as CSV
    res_log_csv = client.get("/api/telemetry/export/logs.csv")
    assert res_log_csv.status_code == 200
    assert res_log_csv.headers["content-type"].startswith("text/csv")
    assert "Timestamp,Time,Level" in res_log_csv.text
    print("✓ /api/telemetry/export/logs.csv export verified.")

    # 5. Export Logs as TXT
    res_log_txt = client.get("/api/telemetry/export/logs.txt")
    assert res_log_txt.status_code == 200
    assert res_log_txt.headers["content-type"].startswith("text/plain")
    assert "Transcript Suite Execution Log Report" in res_log_txt.text
    print("✓ /api/telemetry/export/logs.txt report export verified.")

    # 6. Clear Telemetry
    res_clear = client.post("/api/telemetry/clear")
    assert res_clear.status_code == 200
    assert res_clear.json()["status"] == "cleared"
    print("✓ /api/telemetry/clear verified.")

    # 7. Proactive RAM & GPU Clear Cache Endpoint
    res_mem = client.post("/api/memory/clear")
    assert res_mem.status_code == 200
    mem_data = res_mem.json()
    assert mem_data["status"] == "cleared"
    assert "stats" in mem_data
    assert "free_gb" in mem_data["stats"]
    print("✓ /api/memory/clear proactive cache flush verified.")


def test_cache_and_segment_management():
    from transcript_suite.web.app import TASKS
    client = TestClient(app)

    # 1. Test /api/cache/stats
    res_stats = client.get("/api/cache/stats")
    assert res_stats.status_code == 200
    data = res_stats.json()
    assert "memory" in data
    assert "storage" in data
    assert "total_disk_gb" in data["storage"]
    assert "hf_models" in data["storage"]
    print("✓ /api/cache/stats returned storage and memory breakdown.")

    # 2. Test granular /api/cache/clear (vram, ram, temp_audio, all)
    res_clear_vram = client.post("/api/cache/clear", json={"target": "vram"})
    assert res_clear_vram.status_code == 200
    assert "GPU VRAM Cache" in res_clear_vram.json()["targets"]

    res_clear_ram = client.post("/api/cache/clear", json={"target": "ram"})
    assert res_clear_ram.status_code == 200
    assert "Process Heap (RAM)" in res_clear_ram.json()["targets"]
    print("✓ Granular /api/cache/clear targets verified.")

    # 3. Test Segment Delete & Patch
    task_id = "test-edit-task"
    TASKS[task_id] = {
        "id": task_id,
        "segments": [
            {"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "First chunk"},
            {"start": 2.0, "end": 4.0, "speaker": "Speaker 1", "text": "Noise cough chunk"},
            {"start": 4.0, "end": 6.0, "speaker": "Speaker 0", "text": "Third chunk"}
        ],
        "full_text": "First chunk Noise cough chunk Third chunk"
    }

    # Patch segment 0
    res_patch = client.patch(f"/api/tasks/{task_id}/segments/0", json={"text": "Updated first chunk"})
    assert res_patch.status_code == 200
    assert TASKS[task_id]["segments"][0]["text"] == "Updated first chunk"

    # Delete segment 1 (noise cough chunk)
    res_del = client.delete(f"/api/tasks/{task_id}/segments/1")
    assert res_del.status_code == 200
    assert res_del.json()["remaining_count"] == 2
    assert len(TASKS[task_id]["segments"]) == 2
    assert TASKS[task_id]["segments"][1]["text"] == "Third chunk"
    print("✓ Segment patch and delete endpoints verified.")


def test_models_and_deep_memory_api():
    client = TestClient(app)

    # 1. Test /api/models overview
    res_models = client.get("/api/models")
    assert res_models.status_code == 200
    m_data = res_models.json()
    assert "roster" in m_data
    assert "checkpoints" in m_data
    assert "presets" in m_data
    assert "total_checkpoint_gb" in m_data

    # 2. Test /api/models/roster update
    res_roster = client.post("/api/models/roster", json={"vocal_boost_level": "max"})
    assert res_roster.status_code == 200
    assert res_roster.json()["roster"]["vocal_boost_level"] == "max"

    # Reset back to adaptive
    client.post("/api/models/roster", json={"vocal_boost_level": "adaptive"})

    # 3. Test /api/telemetry/deep-memory and export
    res_deep = client.get("/api/telemetry/deep-memory")
    assert res_deep.status_code == 200
    deep_data = res_deep.json()
    assert "process" in deep_data
    assert "system_ram" in deep_data
    assert "top_processes" in deep_data
    assert "all_processes" in deep_data
    assert "gpu" in deep_data
    assert deep_data["process"]["rss_mb"] > 0
    assert len(deep_data["top_processes"]) <= 10
    assert len(deep_data["all_processes"]) >= len(deep_data["top_processes"])

    res_export_txt = client.get("/api/telemetry/deep-memory/export?format=txt")
    assert res_export_txt.status_code == 200
    assert "text/plain" in res_export_txt.headers["content-type"]
    assert "TRANSCRIPT SUITE - SYSTEM MEMORY & PROCESS AUDIT" in res_export_txt.text
    assert "COMPLETE SYSTEM PROCESS LIST" in res_export_txt.text

    # 4. Test /api/models/checkpoints delete security rejection
    res_del_bad = client.request("DELETE", "/api/models/checkpoints", json={"id": "/etc/passwd"})
    assert res_del_bad.status_code == 403

    # 5. Test /api/memory/drop-cache
    res_drop_cache = client.post("/api/memory/drop-cache")
    assert res_drop_cache.status_code == 200
    cache_data = res_drop_cache.json()
    assert cache_data.get("status") == "success"
    assert "freed_cached_mb" in cache_data
    assert "files_purged" in cache_data



def test_pyannote_and_supervisor_api():
    client = TestClient(app)

    # 1. PyAnnote status and token configuration
    res_py_status = client.get("/api/pyannote/status")
    assert res_py_status.status_code == 200
    assert "installed" in res_py_status.json()

    # Test saving a persistent token
    res_py_set = client.post("/api/pyannote/token", json={"token": "hf_persistent_test_token"})
    assert res_py_set.status_code == 200
    assert res_py_set.json()["token_provided"] is True
    assert res_py_set.json()["token"] == "hf_persistent_test_token"

    res_py_check = client.get("/api/pyannote/status")
    assert res_py_check.status_code == 200
    assert res_py_check.json()["token"] == "hf_persistent_test_token"

    # Reset token back to None
    res_py_token = client.post("/api/pyannote/token", json={"token": None})
    assert res_py_token.status_code == 200
    assert res_py_token.json()["token_provided"] is False

    # 2. Supervisor Subsystems
    res_subsystems = client.get("/api/supervisor/subsystems")
    assert res_subsystems.status_code == 200
    sub_data = res_subsystems.json()
    assert "subsystems" in sub_data
    assert "governor" in sub_data
    assert len(sub_data["subsystems"]) >= 7

    # 3. Supervisor Governor Update
    res_gov = client.post("/api/supervisor/governor", json={"ceiling_gb": 6.0, "enabled": True})
    assert res_gov.status_code == 200
    assert res_gov.json()["status"] == "updated"

    # Reset back to 5.5
    client.post("/api/supervisor/governor", json={"ceiling_gb": 5.5, "enabled": True})

    # 4. Supervisor Force Unload
    res_unload = client.post("/api/supervisor/unload", json={"stage": "whisper"})
    assert res_unload.status_code == 200
    assert res_unload.json()["success"] is True

    # 5. Supervisor Journal Endpoints
    res_journal = client.get("/api/supervisor/journal?limit=20")
    assert res_journal.status_code == 200
    assert "events" in res_journal.json()

    res_export_json = client.get("/api/supervisor/journal/export?format=json")
    assert res_export_json.status_code == 200
    assert res_export_json.headers["content-type"].startswith("application/json")

    res_export_csv = client.get("/api/supervisor/journal/export?format=csv")
    assert res_export_csv.status_code == 200
    assert "text/csv" in res_export_csv.headers["content-type"]

    res_clear_journal = client.post("/api/supervisor/journal/clear")
    assert res_clear_journal.status_code == 200
    assert res_clear_journal.json()["status"] == "cleared"

    # 6. Detailed Storage Endpoints
    res_storage = client.get("/api/storage/detailed")
    assert res_storage.status_code == 200
    storage_data = res_storage.json()
    assert "targets" in storage_data
    assert "total_gb" in storage_data

    # 7. Storage Purge (guarded check)
    res_purge_guarded = client.post("/api/storage/purge", json={"target": "hf_cache"})
    assert res_purge_guarded.status_code == 400


def test_settings_and_storage_endpoints():
    """Tests /api/settings GET & POST and /api/storage/purge-all clean slate endpoint."""
    client = TestClient(app)
    # 1. GET /api/settings
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert "storage" in data
    assert "settings" in data
    storage = data["storage"]
    assert "base_dir" in storage
    assert "models_dir" in storage
    assert "tmp_dir" in storage
    assert "free_gb" in storage

    # 2. POST /api/settings
    res_update = client.post("/api/settings", json={
        "vocal_boost_level": "adaptive",
        "default_diarizer": "pyannote"
    })
    assert res_update.status_code == 200
    up_data = res_update.json()
    assert up_data["status"] == "updated"
    assert up_data["settings"]["vocal_boost_level"] == "adaptive"

    # 3. POST /api/storage/purge-all
    res_purge = client.post("/api/storage/purge-all")
    assert res_purge.status_code == 200
    purge_data = res_purge.json()
    assert purge_data["status"] == "purged"


if __name__ == "__main__":
    test_web_endpoints()
    test_task_control_endpoints()
    test_audio_stream_endpoints()
    test_telemetry_endpoints()
    test_cache_and_segment_management()
    test_models_and_deep_memory_api()
    test_pyannote_and_supervisor_api()
    test_settings_and_storage_endpoints()
    print("\nAll Web API, models, deep memory, PyAnnote, and supervisor tests passed!")







