"""
Unit and integration tests for Sub-Phase 4.4: Formatting Automation & Accessibility Customization:
- 4.4.A: Deterministic Number & Disfluency Normalizer ($100, 25%, filler-word cleanup)
- 4.4.B: Instant Segment Merge & Split (Ctrl+Shift+J / Ctrl+Shift+S)
- 4.4.C: Global Search & Replace with Regex & Case Matching (Ctrl+H)
- 4.4.D: Live Word-Count, Pacing & Speech Rate Metrics (WPM / CPS badges)
- 4.4.E: Typography & Density Customizer (font scale, OpenDyslexic, line height)
- 4.4.F: Speaker Palette & Role Avatar Customizer (custom accents & role icons)
- 4.4.G: Interactive Keyboard Shortcuts Cheatsheet Modal (? / Ctrl+/)
"""

import os
from fastapi.testclient import TestClient
from transcript_suite.web.app import app, TASKS
from transcript_suite.nlp.normalizer import (
    normalize_transcript_text,
    words_to_number,
    normalize_currencies,
    normalize_percentages,
    normalize_numbers_and_ordinals,
    remove_disfluencies
)


# --- 1. NLP Normalizer Module Tests ---

def test_words_to_number_conversion():
    assert words_to_number("forty-two") == 42
    assert words_to_number("one hundred and twenty-three") == 123
    assert words_to_number("two thousand and twenty-four") == 2024
    assert words_to_number("five million") == 5000000


def test_currency_normalization():
    text = "The bill was one hundred dollars and fifty euros, plus twenty pounds."
    normalized = normalize_currencies(text)
    assert "$100" in normalized
    assert "€50" in normalized
    assert "£20" in normalized

    # Number word with currency
    text2 = "It costs twenty-five dollars."
    assert "$25" in normalize_currencies(text2)


def test_percentage_normalization():
    text = "We achieved twenty-five percent growth and 10 percentage point increase."
    normalized = normalize_percentages(text)
    assert "25%" in normalized
    assert "10%" in normalized


def test_number_and_ordinal_normalization():
    text = "The first step was taken by three participants on the second day."
    normalized = normalize_numbers_and_ordinals(text)
    assert "1st" in normalized
    assert "3" in normalized
    assert "2nd" in normalized


def test_disfluency_removal():
    text = "Um, I think, uh, we should proceed, you know, with the plan."
    cleaned = remove_disfluencies(text)
    assert "um" not in cleaned.lower()
    assert "uh" not in cleaned.lower()
    assert "you know" not in cleaned.lower()
    assert "we should proceed" in cleaned


def test_full_nlp_normalize_pipeline():
    text = "Um, we paid one hundred dollars for twenty-five percent of the first phase, uh, you know."
    res = normalize_transcript_text(
        text,
        convert_numbers=True,
        normalize_currencies=True,
        normalize_percentages=True,
        remove_disfluencies=True
    )
    assert "$100" in res
    assert "25%" in res
    assert "1st" in res
    assert "um" not in res.lower()
    assert "you know" not in res.lower()


# --- 2. Backend Normalizer API Endpoint Tests ---

def test_api_normalize_task_endpoint():
    client = TestClient(app)
    task_id = "test-task-normalize-001"
    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "speaker": "Speaker 0",
                "text": "Um, the price is one hundred dollars.",
                "edited": False
            },
            {
                "start": 3.0,
                "end": 6.0,
                "speaker": "Speaker 1",
                "text": "Uh, we observed twenty-five percent improvement.",
                "edited": False
            }
        ],
        "full_text": "Um, the price is one hundred dollars. Uh, we observed twenty-five percent improvement."
    }

    # Normalize entire task
    resp = client.post(f"/api/tasks/{task_id}/normalize", json={
        "convert_numbers": True,
        "normalize_currencies": True,
        "normalize_percentages": True,
        "remove_disfluencies": True,
        "scope": "all"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["modified_segments_count"] == 2
    assert "$100" in data["segments"][0]["text"]
    assert "25%" in data["segments"][1]["text"]
    assert "um" not in data["segments"][0]["text"].lower()

    # Scope = active segment only
    resp2 = client.post(f"/api/tasks/{task_id}/normalize", json={
        "convert_numbers": True,
        "normalize_currencies": True,
        "normalize_percentages": True,
        "remove_disfluencies": False,
        "scope": "active",
        "segment_idx": 0
    })
    assert resp2.status_code == 200


# --- 3. Backend Segment Merge & Split API Tests ---

def test_api_segment_merge_endpoint():
    client = TestClient(app)
    task_id = "test-task-merge-001"
    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "segments": [
            {"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "Hello world.", "edited": False},
            {"start": 2.0, "end": 4.5, "speaker": "Speaker 0", "text": "This is segment two.", "edited": False},
            {"start": 4.5, "end": 7.0, "speaker": "Speaker 1", "text": "And segment three.", "edited": False}
        ]
    }

    # Merge segment 0 and segment 1
    resp = client.post(f"/api/tasks/{task_id}/segments/merge", json={"first_idx": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_segments"] == 2
    merged = data["merged_segment"]
    assert merged["start"] == 0.0
    assert merged["end"] == 4.5
    assert merged["text"] == "Hello world. This is segment two."
    assert merged["edited"] is True

    # Bad index (out of range)
    resp_bad = client.post(f"/api/tasks/{task_id}/segments/merge", json={"first_idx": 1})
    assert resp_bad.status_code == 400


def test_api_segment_split_endpoint():
    client = TestClient(app)
    task_id = "test-task-split-001"
    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "segments": [
            {"start": 0.0, "end": 6.0, "speaker": "Speaker 0", "text": "First sentence here. Second sentence starts now.", "edited": False}
        ]
    }

    # Split segment at character offset 20
    resp = client.post(f"/api/tasks/{task_id}/segments/split", json={
        "seg_idx": 0,
        "char_offset": 20
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_segments"] == 2
    assert "First sentence here." in data["first_segment"]["text"]
    assert "Second sentence starts now." in data["second_segment"]["text"]
    assert data["first_segment"]["start"] == 0.0
    assert data["second_segment"]["end"] == 6.0
    assert data["first_segment"]["end"] == data["second_segment"]["start"]


# --- 4. Backend Speaker Palette API Tests ---

def test_api_speaker_palette_endpoint():
    client = TestClient(app)
    task_id = "test-task-palette-001"
    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "segments": [
            {"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "Testing palette.", "edited": False}
        ]
    }

    palette_payload = {
        "palette": {
            "Speaker 0": {"color": "#10b981", "avatar": "🎙️", "alias": "Host Doctor"}
        }
    }
    resp = client.post(f"/api/tasks/{task_id}/speakers/palette", json=palette_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "Speaker 0" in data["palette"]
    assert data["palette"]["Speaker 0"]["color"] == "#10b981"
    assert data["palette"]["Speaker 0"]["avatar"] == "🎙️"


# --- 5. Frontend HTML / CSS / JS Integration Checks ---

def test_frontend_elements_and_modals_present():
    static_dir = os.path.join(os.path.dirname(__file__), "..", "transcript_suite", "web", "static")
    html_path = os.path.join(static_dir, "index.html")
    css_path = os.path.join(static_dir, "style.css")
    js_path = os.path.join(static_dir, "app.js")

    assert os.path.exists(html_path)
    assert os.path.exists(css_path)
    assert os.path.exists(js_path)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Toolbar buttons
    assert 'id="btnOpenNormalizer"' in html_content
    assert 'id="btnToggleSearchReplace"' in html_content
    assert 'id="btnTypography"' in html_content
    assert 'id="btnSpeakerPalette"' in html_content
    assert 'id="btnShortcutsCheatsheet"' in html_content

    # Metrics ribbon & Search/Replace bar
    assert 'id="documentMetricsRibbon"' in html_content
    assert 'id="searchReplaceBar"' in html_content
    assert 'id="srFindInput"' in html_content
    assert 'id="srReplaceInput"' in html_content

    # Modals
    assert 'id="normalizerModal"' in html_content
    assert 'id="typographyModal"' in html_content
    assert 'id="speakerPaletteModal"' in html_content
    assert 'id="shortcutsModal"' in html_content

    with open(css_path, "r", encoding="utf-8") as f:
        css_content = f.read()

    # CSS styles
    assert ".document-metrics-ribbon" in css_content
    assert ".badge-pacing" in css_content
    assert ".pace-slow" in css_content
    assert ".pace-normal" in css_content
    assert ".pace-fast" in css_content
    assert ".pace-rush" in css_content
    assert ".search-replace-bar" in css_content
    assert ".btn-segment-action" in css_content
    assert ".font-dyslexic" in css_content
    assert ".speaker-palette-item" in css_content
    assert "kbd" in css_content

    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # JS functions and state
    assert "updateDocumentMetrics" in js_content
    assert "mergeSegmentWithNext" in js_content
    assert "splitSegmentAtCursor" in js_content
    assert "initSearchAndReplace" in js_content
    assert "toggleSearchReplaceBar" in js_content
    assert "initNormalizerModal" in js_content
    assert "normalizeDocument" in js_content
    assert "initTypographyCustomizer" in js_content
    assert "applyTypography" in js_content
    assert "initSpeakerPaletteModal" in js_content
    assert "initShortcutsModal" in js_content
    assert "MERGE_SEGMENTS" in js_content
    assert "SPLIT_SEGMENT" in js_content
    assert "BULK_REPLACE" in js_content
