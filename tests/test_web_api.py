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
        assert data["interval_seconds"] == interval

    print("✓ /api/telemetry/trace verified with 2s, 5s, and 10s intervals.")

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


if __name__ == "__main__":
    test_web_endpoints()
    test_task_control_endpoints()
    test_audio_stream_endpoints()
    test_telemetry_endpoints()
    print("\nAll Web API and telemetry tests passed!")




