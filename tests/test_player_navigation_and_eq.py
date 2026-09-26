"""
Unit and integration tests for Sub-Phases 3.2 and 3.3:
- 3.2.A: Waveform Smooth Mouse-Wheel Zoom & Drag-Region Audition Loop
- 3.2.B: Word-Level A-B Micro-Looping (0.75x Syllable Looping)
- 3.3.A: NLE J-K-L Shuttle Scrubbing
- 3.3.B: Human Listener 3-Band Vocal Clarity EQ
"""

import io
import tempfile
from pathlib import Path
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from transcript_suite.web.app import app, TASKS


def _create_dummy_wav(duration_sec: float = 2.0, sr: int = 16000) -> str:
    """Helper to generate a clean synthetic audio file."""
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float32)
    signal = 0.5 * np.sin(2 * np.pi * 440 * t)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, signal, sr)
    tmp.close()
    return tmp.name


def test_audio_slice_endpoint():
    """Verify precision audio slicing API (start, end, speed modification, and caching)."""
    client = TestClient(app)
    wav_path = _create_dummy_wav(duration_sec=3.0, sr=16000)
    task_id = "test-slice-task-123"

    TASKS[task_id] = {
        "id": task_id,
        "file_path": wav_path,
        "processed_audio": None,
        "status": "completed"
    }

    try:
        # 1. Test standard 1.0x slice from 0.5s to 1.5s (1.0 second duration)
        res = client.get(f"/api/audio/{task_id}/slice?start=0.5&end=1.5&speed=1.0")
        assert res.status_code == 200
        assert res.headers["content-type"] == "audio/wav"

        with sf.SoundFile(io.BytesIO(res.content)) as f:
            dur = len(f) / f.samplerate
            assert abs(dur - 1.0) < 0.05, f"Expected 1.0s slice, got {dur}s"
            assert f.samplerate == 16000

        # 2. Test 0.75x time-stretched slice for word syllable micro-looping (3.2.B)
        # Slicing 0.6s of audio at 0.75x speed yields 0.6 / 0.75 = 0.8s
        res_slow = client.get(f"/api/audio/{task_id}/slice?start=1.0&end=1.6&speed=0.75")
        assert res_slow.status_code == 200
        with sf.SoundFile(io.BytesIO(res_slow.content)) as f:
            dur_slow = len(f) / f.samplerate
            assert abs(dur_slow - 0.8) < 0.08, f"Expected ~0.8s slowed slice, got {dur_slow}s"

        # 3. Test caching: repeated request returns identical bytes immediately
        res_cached = client.get(f"/api/audio/{task_id}/slice?start=1.0&end=1.6&speed=0.75")
        assert res_cached.status_code == 200
        assert res_cached.content == res_slow.content

        # 4. Error validation: end <= start returns 400
        res_bad = client.get(f"/api/audio/{task_id}/slice?start=2.0&end=1.0")
        assert res_bad.status_code == 400

        # 5. Non-existent task returns 404
        res_404 = client.get("/api/audio/unknown-task-9999/slice?start=0&end=1")
        assert res_404.status_code == 404

    finally:
        if task_id in TASKS:
            del TASKS[task_id]
        if Path(wav_path).exists():
            Path(wav_path).unlink()


def test_web_ui_navigation_and_eq_assets():
    """Verify HTML UI elements, JS event handlers, and CSS rules for 3.2 and 3.3."""
    client = TestClient(app)

    # 1. HTML index
    res_index = client.get("/")
    assert res_index.status_code == 200
    html = res_index.text
    # 3.2.A Zoom & Loop
    assert 'id="zoomControls"' in html
    assert 'id="zoomPill"' in html
    assert 'id="loopRegionBadge"' in html
    assert 'id="selectionOverlayOrig"' in html
    # 3.2.B Syllable Banner
    assert 'id="syllableLoopBanner"' in html
    # 3.3.A Shuttle HUD
    assert 'id="shuttleBadge"' in html
    # 3.3.B Vocal Clarity EQ
    assert 'id="eqControlsGroup"' in html
    assert 'id="btnEqClarifier"' in html
    assert 'id="btnEqDehiss"' in html

    # 2. JavaScript logic
    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    js = res_js.text
    assert "setWaveformZoom" in js
    assert "setActiveLoop" in js
    assert "clearActiveLoop" in js
    assert "transcript-word" in js
    assert "handleShuttleKey" in js
    assert "initNLEShuttleShortcuts" in js
    assert "initWebAudioEQ" in js
    assert "setEQPreset" in js
    assert "initNavigationAndTransport" in js

    # 3. CSS stylesheet
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    css = res_css.text
    assert ".zoom-controls" in css
    assert ".shuttle-badge" in css
    assert ".loop-badge" in css
    assert ".waveform-selection-overlay" in css
    assert ".eq-controls-group" in css
    assert ".transcript-word" in css
    assert ".syllable-loop-banner" in css
