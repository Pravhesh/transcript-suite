"""
Unit and integration tests for Sub-Phase 4.2 & Sub-Phase 4.3:
- 4.2.A: Per-Model Full Transcript Comparator Lab (side-by-side model outputs & scorecards)
- 4.2.B: Juror Voting Inspector (collapsible segment votes with 1-click winner override)
- 4.3.A: Full Undo / Redo Engine (100-step history, Ctrl+Z / Ctrl+Y)
- 4.3.B: Smart Auto-Scroll with Viewport Edit Lock (freezes scrolling while actively typing)
- 4.3.C: Floating Quick-Action Selection Bubble Menu (audition, +glossary, change case, flag)
"""

from fastapi.testclient import TestClient
from transcript_suite.web.app import app, TASKS


def test_comparator_endpoint_with_council_task():
    """Verify GET /api/tasks/{task_id}/comparator compiles per-model scorecards and alignments."""
    client = TestClient(app)
    task_id = "test-comparator-task-001"

    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "model_name": "nvidia/canary-1b",
        "segments": [
            {
                "start": 0.0,
                "end": 2.5,
                "duration": 2.5,
                "speaker": "Speaker 0",
                "text": "Good morning and welcome to the session.",
                "council": {
                    "verdict": "Good morning and welcome to the session.",
                    "agreement_type": "UNANIMOUS",
                    "consensus_score": 1.0,
                    "disputed_tokens": [],
                    "votes": [
                        {"member": "nvidia/canary-1b", "role": "Lead", "hypothesis": "Good morning and welcome to the session.", "confidence": 0.98},
                        {"member": "openai/whisper-large-v3-turbo", "role": "Auditor", "hypothesis": "Good morning and welcome to the session.", "confidence": 0.96},
                        {"member": "nvidia/stt_en_conformer_ctc_xlarge", "role": "Anchor", "hypothesis": "Good morning and welcome to the session.", "confidence": 0.92}
                    ]
                },
                "needs_review": False,
                "loop_circuit_breaker_tripped": False
            },
            {
                "start": 2.5,
                "end": 5.0,
                "duration": 2.5,
                "speaker": "Speaker 1",
                "text": "We will deploy the PyTorch model today.",
                "council": {
                    "verdict": "We will deploy the PyTorch model today.",
                    "agreement_type": "MAJORITY",
                    "consensus_score": 0.75,
                    "disputed_tokens": ["PyTorch"],
                    "votes": [
                        {"member": "nvidia/canary-1b", "role": "Lead", "hypothesis": "We will deploy the PyTorch model today.", "confidence": 0.94},
                        {"member": "openai/whisper-large-v3-turbo", "role": "Auditor", "hypothesis": "We will deploy the PyTorch model today.", "confidence": 0.91},
                        {"member": "nvidia/stt_en_conformer_ctc_xlarge", "role": "Anchor", "hypothesis": "We will deploy the pie torch model today.", "confidence": 0.78}
                    ]
                },
                "needs_review": False,
                "loop_circuit_breaker_tripped": False
            },
            {
                "start": 5.0,
                "end": 8.0,
                "duration": 3.0,
                "speaker": "Speaker 0",
                "text": "The quick brown fox jumps over the lazy dog.",
                "council": {
                    "verdict": "The quick brown fox jumps over the lazy dog.",
                    "agreement_type": "SPLIT_DECISION",
                    "consensus_score": 0.60,
                    "disputed_tokens": ["fox", "box"],
                    "votes": [
                        {"member": "nvidia/canary-1b", "role": "Lead", "hypothesis": "The quick brown box jumps over the lazy dog.", "confidence": 0.85},
                        {"member": "openai/whisper-large-v3-turbo", "role": "Auditor", "hypothesis": "The quick brown fox jumps over the lazy dog.", "confidence": 0.88},
                        {"member": "nvidia/stt_en_conformer_ctc_xlarge", "role": "Anchor", "hypothesis": "The quick brown fox jumped over a lazy dog.", "confidence": 0.82}
                    ]
                },
                "needs_review": True,
                "loop_circuit_breaker_tripped": False
            }
        ]
    }

    try:
        res = client.get(f"/api/tasks/{task_id}/comparator")
        assert res.status_code == 200
        data = res.json()

        assert data["task_id"] == task_id
        assert data["total_chunks"] == 3

        # Consensus stats
        consensus = data["consensus"]
        assert consensus["word_count"] > 0
        assert consensus["unanimous_chunks"] == 1
        assert consensus["majority_chunks"] == 1
        assert consensus["split_chunks"] == 1
        assert consensus["loop_breaker_chunks"] == 0

        # Model scorecards
        models = data["models"]
        assert len(models) == 3
        model_names = [m["display_name"] for m in models]
        assert "Canary-1B" in model_names
        assert "Whisper Large V3" in model_names
        assert "Conformer Ctc" in model_names

        for m in models:
            assert m["word_count"] > 0
            assert 0.0 <= m["avg_confidence"] <= 1.0
            assert 0 <= m["win_count"] <= 3
            assert 0.0 <= m["win_rate"] <= 1.0
            assert 0.0 <= m["agreement_score"] <= 1.0

        # Alignments
        alignments = data["chunk_alignments"]
        assert len(alignments) == 3
        assert alignments[0]["agreement_type"] == "UNANIMOUS"
        assert alignments[1]["agreement_type"] == "MAJORITY"
        assert alignments[2]["needs_review"] is True
        assert len(alignments[0]["votes"]) == 3

    finally:
        if task_id in TASKS:
            del TASKS[task_id]


def test_comparator_single_model_fallback():
    """Verify GET /api/tasks/{task_id}/comparator works gracefully on tasks without council data."""
    client = TestClient(app)
    task_id = "test-comparator-single-002"

    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "model_name": "nvidia/canary-1b",
        "segments": [
            {"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "Testing single model comparator fallback."}
        ]
    }

    try:
        res = client.get(f"/api/tasks/{task_id}/comparator")
        assert res.status_code == 200
        data = res.json()
        assert data["total_chunks"] == 1
        assert len(data["models"]) >= 1
        assert len(data["chunk_alignments"]) == 1
        assert data["chunk_alignments"][0]["votes"][0]["is_winner"] is True
    finally:
        if task_id in TASKS:
            del TASKS[task_id]


def test_segment_patch_and_restore_endpoints():
    """Verify segment PATCH with review/override fields and POST /restore endpoint for undo/redo."""
    client = TestClient(app)
    task_id = "test-patch-restore-003"

    TASKS[task_id] = {
        "id": task_id,
        "status": "completed",
        "segments": [
            {"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "First chunk."},
            {"start": 2.0, "end": 4.0, "speaker": "Speaker 1", "text": "Second chunk."}
        ]
    }

    try:
        # 1. Test enhanced PATCH with needs_review, edited, winning_juror
        patch_res = client.patch(
            f"/api/tasks/{task_id}/segments/0",
            json={
                "text": "First chunk modified.",
                "needs_review": True,
                "edited": True,
                "winning_juror": "openai/whisper-large-v3-turbo"
            }
        )
        assert patch_res.status_code == 200
        patched_seg = patch_res.json()["segment"]
        assert patched_seg["text"] == "First chunk modified."
        assert patched_seg["needs_review"] is True
        assert patched_seg["edited"] is True
        assert patched_seg["winning_juror"] == "openai/whisper-large-v3-turbo"

        # 2. Test DELETE segment
        del_res = client.delete(f"/api/tasks/{task_id}/segments/0")
        assert del_res.status_code == 200
        assert len(TASKS[task_id]["segments"]) == 1

        # 3. Test POST /restore to re-insert the deleted segment (Undo)
        restore_res = client.post(
            f"/api/tasks/{task_id}/segments/restore",
            json={
                "segment_idx": 0,
                "segment": patched_seg
            }
        )
        assert restore_res.status_code == 200
        assert restore_res.json()["status"] == "restored"
        assert len(TASKS[task_id]["segments"]) == 2
        assert TASKS[task_id]["segments"][0]["text"] == "First chunk modified."

    finally:
        if task_id in TASKS:
            del TASKS[task_id]


def test_web_ui_html_elements_for_4_2_and_4_3():
    """Verify HTML UI elements for 4.2 (Comparator, Juror Drawers) and 4.3 (Undo/Redo, Edit Lock, Selection Bubble)."""
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # 4.2.A & 4.2.B Toolbar buttons
    assert 'id="btnModelComparator"' in html
    assert 'id="btnToggleAllJurors"' in html

    # 4.3.A Undo / Redo buttons
    assert 'id="btnUndo"' in html
    assert 'id="btnRedo"' in html

    # 4.3.B Auto-Scroll Lock button
    assert 'id="btnAutoScrollLock"' in html
    assert 'id="autoScrollLockIcon"' in html
    assert 'id="autoScrollLockLabel"' in html

    # 4.3.C Selection Bubble Menu
    assert 'id="selectionBubble"' in html
    assert 'id="bubbleBtnAudition"' in html
    assert 'id="bubbleBtnGlossary"' in html
    assert 'id="bubbleBtnCase"' in html
    assert 'id="bubbleCaseDropdown"' in html
    assert 'id="bubbleBtnFlag"' in html
    assert 'id="bubbleBtnClose"' in html

    # 4.2.A Model Comparator Lab Modal
    assert 'id="comparatorModal"' in html
    assert 'id="btnCloseComparator"' in html
    assert 'id="comparatorScorecards"' in html
    assert 'id="chkDisagreementsOnly"' in html
    assert 'id="comparatorSearchInput"' in html
    assert 'id="btnExportComparatorTxt"' in html
    assert 'id="comparatorGridWrapper"' in html


def test_web_ui_css_rules_for_4_2_and_4_3():
    """Verify CSS styling rules for 4.2 and 4.3 components."""
    client = TestClient(app)
    res = client.get("/static/style.css")
    assert res.status_code == 200
    css = res.text

    # 4.3.A Undo / Redo & 4.3.B Auto-Scroll Lock styles
    assert ".history-button-group" in css
    assert ".btn-history:disabled" in css
    assert ".btn-tool-pill.locked" in css

    # 4.2.B Enhanced Juror Voting Inspector styles
    assert ".council-vote-row.is-winner" in css
    assert ".vote-winner-badge" in css
    assert ".vote-winner-badge.consensus-win" in css
    assert ".vote-winner-badge.user-override" in css
    assert ".btn-override-winner" in css

    # 4.3.C Floating Selection Bubble styles
    assert ".selection-bubble" in css
    assert ".btn-bubble-action" in css
    assert ".bubble-dropdown" in css
    assert ".bubble-dropdown-menu" in css
    assert ".bubble-dropdown-item" in css

    # 4.2.A Model Comparator Modal styles
    assert ".modal-content-wide" in css
    assert ".comparator-scorecards" in css
    assert ".comparator-card" in css
    assert ".comparator-table" in css
    assert ".comparator-diff-token" in css
    assert ".comparator-win-token" in css


def test_web_ui_javascript_logic_for_4_2_and_4_3():
    """Verify JavaScript logic and functions for 4.2 and 4.3."""
    client = TestClient(app)
    res = client.get("/static/app.js")
    assert res.status_code == 200
    js = res.text

    # 4.3.A Undo / Redo Manager
    assert "UndoRedoManager" in js
    assert "initEditorStateAndHistory()" in js

    # 4.3.B Smart Auto-Scroll Lock
    assert "setAutoScrollLock" in js
    assert "updateAutoScrollLockUI" in js
    assert "initAutoScrollLockControls" in js
    assert "isAutoScrollLocked" in js

    # 4.3.C Selection Bubble Menu
    assert "initSelectionBubble" in js
    assert "hideSelectionBubble" in js
    assert "applyCaseTransformation" in js

    # 4.2.B Juror Winner Override & Expand All
    assert "overrideJurorWinner" in js
    assert "toggleAllJurorDrawers" in js

    # 4.2.A Model Comparator Lab
    assert "initComparatorLabControls" in js
    assert "openComparatorLab" in js
    assert "closeComparatorLab" in js
    assert "buildClientSideComparatorData" in js
    assert "renderComparatorLab" in js
    assert "renderComparatorTable" in js
    assert "copyComparatorView" in js
