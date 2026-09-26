"""
Unit and integration tests for Sub-Phase 4.1:
- 4.1.A: Side-by-Side Dual-Pane Layout (Stacked vs. Split layout toggle for 16:9/ultrawide displays)
- 4.1.B: Ambient Reading Ruler Focus Dimming (Softly dim inactive segments, glowing active accent)
- 4.1.C: Vertical Document Mini-Map Scrub Bar (Bird's-eye scrub bar with color-coded speaker and dispute indicators)
"""

from pathlib import Path
from fastapi.testclient import TestClient

from transcript_suite.web.app import app


def test_subphase_4_1_html_elements():
    """Verify HTML elements for layout toggling, reading ruler, and vertical minimap."""
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # 4.1.A Layout toggles & workspace container
    assert 'class="workspace-container layout-stacked"' in html
    assert 'id="workspaceContainer"' in html
    assert 'id="btnLayoutStacked"' in html
    assert 'id="btnLayoutSplit"' in html
    assert 'class="layout-toggle-group"' in html

    # 4.1.B Reading Ruler toggle
    assert 'id="btnToggleReadingRuler"' in html
    assert 'Reading Ruler' in html
    assert 'Alt + F' in html

    # 4.1.C Document Mini-Map components
    assert 'class="transcript-feed-wrapper"' in html
    assert 'id="transcriptMinimap"' in html
    assert 'id="minimapCanvas"' in html
    assert 'id="minimapSlider"' in html
    assert 'id="minimapPlayhead"' in html


def test_subphase_4_1_css_rules():
    """Verify CSS styling rules for layout modes, reading ruler dimming, and minimap."""
    client = TestClient(app)
    res = client.get("/static/style.css")
    assert res.status_code == 200
    css = res.text

    # 4.1.A Dual-Pane Layout CSS
    assert ".workspace-container" in css
    assert ".workspace-container.layout-stacked" in css
    assert ".workspace-container.layout-split" in css
    assert "grid-template-columns: minmax(360px, 460px) minmax(0, 1fr);" in css
    assert ".layout-toggle-group" in css
    assert ".btn-layout" in css

    # 4.1.B Reading Ruler Focus Dimming CSS
    assert ".reading-ruler-active" in css
    assert ".reading-ruler-active .segment-block:not(.active)" in css
    assert "opacity: 0.38" in css
    assert ".reading-ruler-active .segment-block.active" in css
    assert "border-left: 3px solid var(--accent-light)" in css

    # 4.1.C Vertical Minimap CSS
    assert ".transcript-feed-wrapper" in css
    assert ".transcript-minimap" in css
    assert ".minimap-canvas" in css
    assert ".minimap-slider" in css
    assert ".minimap-playhead" in css


def test_subphase_4_1_javascript_logic():
    """Verify JavaScript implementations and hooks for 4.1."""
    client = TestClient(app)
    res = client.get("/static/app.js")
    assert res.status_code == 200
    js = res.text

    # 4.1.A Layout functions & persistence
    assert "function setWorkspaceLayoutMode(mode)" in js
    assert "function initWorkspaceLayoutToggle()" in js
    assert 'localStorage.setItem("transcript_layout_mode", currentLayoutMode)' in js
    assert "wavesurferOrig?.drawBuffer()" in js
    assert "wavesurferModel?.drawBuffer()" in js

    # 4.1.B Reading Ruler functions & persistence
    assert "function toggleReadingRuler(forceVal)" in js
    assert "function initReadingRuler()" in js
    assert 'localStorage.setItem("transcript_reading_ruler", isReadingRulerActive ? "true" : "false")' in js
    assert 'toggleReadingRuler()' in js
    assert 'altKey && (e.key === "f" || e.key === "F")' in js

    # 4.1.C Minimap rendering, playhead tracking, and scrubbing
    assert "function renderDocumentMinimap()" in js
    assert "function updateMinimapSlider()" in js
    assert "function updateMinimapPlayhead(currentTime, totalDuration)" in js
    assert "function initMinimapScrubbing()" in js
    assert "getSpeakerClass(spk)" in js
    assert "isBreaker" in js
    assert "isDisputed" in js
    assert "updateTimelinePlayheads" in js
