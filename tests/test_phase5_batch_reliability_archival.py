"""
Comprehensive Unit & Integration Test Suite for Phase 5:
- 5.1.A: Multi-File Sequential Ingest Queue & Batch Controls (v1.5.1)
- 5.2.A: Automatic Crash Checkpointing & Resumable Tasks (v1.5.2)
- 5.2.B: Storage Quota & Auto-Purge Retention Policies with Zero Data Loss (v1.5.2)
- 5.3.A: Workspace Full Archive Backup (v1.5.3)
- 5.3.B: Hands-Free Fast-Navigation Keyboard Hotkeys (v1.5.3)
"""

import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient

from transcript_suite.config import config
from transcript_suite.batch.manager import BatchIngestManager, BatchItem
from transcript_suite.checkpointing.manager import CheckpointManager
from transcript_suite.storage.retention import RetentionManager
from transcript_suite.workspace.backup import (
    generate_srt,
    generate_vtt,
    get_workspace_summary,
    create_workspace_archive,
    format_timestamp_srt,
    format_timestamp_vtt
)
from transcript_suite.web.app import app, TASKS


def test_batch_ingest_manager_lifecycle():
    """Tests sequential queuing, reordering, pause/resume, and status transitions."""
    mgr = BatchIngestManager()

    item1 = mgr.enqueue("lecture_1.wav", "/tmp/l1.wav", 15.5)
    item2 = mgr.enqueue("lecture_2.wav", "/tmp/l2.wav", 20.0)
    item3 = mgr.enqueue("lecture_3.wav", "/tmp/l3.wav", 12.0)

    status = mgr.get_status()
    assert status["queued_count"] == 3
    assert not status["has_active_task"]
    assert not status["is_paused"]

    # Test reordering: move item3 to the front (position 0)
    reordered = mgr.reorder(item3.item_id, 0)
    assert reordered is True
    status = mgr.get_status()
    assert status["queue"][0]["item_id"] == item3.item_id
    assert status["queue"][0]["queue_position"] == 1

    # Test get_next_to_process picks the first item
    next_item = mgr.get_next_to_process()
    assert next_item is not None
    assert next_item.item_id == item3.item_id

    # Mark started
    mgr.mark_started(item3.item_id, "task-333")
    status = mgr.get_status()
    assert status["has_active_task"] is True
    assert status["current_item"]["item_id"] == item3.item_id
    assert status["current_item"]["status"] == "processing"

    # When an item is processing, get_next_to_process returns None (8 GB sequential guarantee)
    assert mgr.get_next_to_process() is None

    # Update progress
    mgr.update_progress(item3.item_id, 45.0)
    assert mgr.get_status()["current_item"]["progress"] == 45.0

    # Complete item3
    mgr.mark_completed(item3.item_id)
    assert mgr.get_status()["has_active_task"] is False

    # Test pause prevents picking up the next item
    mgr.pause()
    assert mgr.is_paused is True
    assert mgr.get_next_to_process() is None

    mgr.resume()
    assert mgr.is_paused is False
    next_item2 = mgr.get_next_to_process()
    assert next_item2 is not None

    # Test cancel/remove
    cancelled = mgr.remove(item2.item_id)
    assert cancelled is True
    assert item2.item_id not in [q["item_id"] for q in mgr.get_status()["queue"]]

    # Test clear_pending
    mgr.enqueue("extra.wav", "/tmp/extra.wav", 5.0)
    cleared = mgr.clear_pending()
    assert cleared >= 1
    assert mgr.get_status()["queued_count"] == 0


def test_checkpoint_manager_atomic_lifecycle():
    """Tests atomic checkpoint writing, loading, and filtering."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mgr = CheckpointManager(checkpoints_dir=tmp_path)

        dummy_audio = tmp_path / "sample.wav"
        dummy_audio.write_bytes(b"RIFFdummydata")

        saved = mgr.save_checkpoint(
            task_id="task-recovery-test",
            filename="sample.wav",
            file_path=str(dummy_audio),
            last_chunk_index=2,
            total_chunks=6,
            segments=[
                {"start": 0.0, "end": 5.0, "text": "Hello world", "speaker": "Speaker 0"},
                {"start": 5.0, "end": 10.0, "text": "This is a test", "speaker": "Speaker 1"}
            ],
            params={"diarizer": "nemo", "enable_council": True},
            duration=30.0,
            status="in_progress"
        )
        assert saved is True

        # Checkpoint file exists on disk
        ckpt_file = tmp_path / "checkpoint_task-recovery-test.json"
        assert ckpt_file.exists()

        # Load checkpoint
        loaded = mgr.load_checkpoint("task-recovery-test")
        assert loaded is not None
        assert loaded["last_chunk_index"] == 2
        assert loaded["total_chunks"] == 6
        assert len(loaded["segments"]) == 2
        assert loaded["params"]["diarizer"] == "nemo"

        # List resumable checkpoints
        resumable = mgr.list_resumable_checkpoints()
        assert len(resumable) == 1
        assert resumable[0]["task_id"] == "task-recovery-test"
        assert resumable[0]["last_chunk_index"] == 2

        # Mark completed removes the active checkpoint
        mgr.mark_completed("task-recovery-test")
        assert not ckpt_file.exists()
        assert len(mgr.list_resumable_checkpoints()) == 0


def test_storage_retention_and_zero_loss_invariant():
    """Tests retention policy updates, dry-run candidate calculation, and zero-loss audio purging."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        uploads_dir = base_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # Save config state
        orig_upload_dir = config.upload_dir
        config.upload_dir = uploads_dir

        try:
            # Create old raw audio files
            old_time = time.time() - (30 * 86400)  # 30 days old
            old_wav = uploads_dir / "old_audio_orig.wav"
            old_wav.write_bytes(b"A" * 1024 * 1024)  # 1 MB
            os.utime(old_wav, (old_time, old_time))

            old_chunks_dir = uploads_dir / "old_audio_chunks"
            old_chunks_dir.mkdir()
            chunk_file = old_chunks_dir / "chunk_0.wav"
            chunk_file.write_bytes(b"B" * 512 * 1024)  # 512 KB
            os.utime(chunk_file, (old_time, old_time))
            os.utime(old_chunks_dir, (old_time, old_time))

            # Create fresh audio file (1 hour old)
            fresh_wav = uploads_dir / "fresh_audio_orig.wav"
            fresh_wav.write_bytes(b"C" * 1024 * 1024)

            # Create transcript files (*.json, *.srt, *.txt) that MUST NEVER BE DELETED
            transcript_json = uploads_dir / "old_audio_transcript.json"
            transcript_json.write_text(json.dumps({"task_id": "old_audio", "full_text": "Important permanent record"}))

            transcript_srt = uploads_dir / "old_audio_subtitles.srt"
            transcript_srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nPermanent text\n")

            mgr = RetentionManager()
            # Update policy to 14 days retention
            mgr.update_policy({"audio_retention_days": 14, "storage_quota_gb": 10.0, "auto_purge_enabled": False})

            # Dry-run scan
            scan = mgr.scan_purging_targets()
            candidates = scan["candidates"]
            # Old audio and old chunks dir should be candidates; transcript_json and fresh_wav should NOT
            assert str(old_wav) in candidates
            assert str(old_chunks_dir) in candidates
            assert str(transcript_json) not in candidates
            assert str(fresh_wav) not in candidates

            # Execute purge
            mock_tasks = {
                "old_audio": {
                    "orig_file_path": str(old_wav),
                    "segments": [{"text": "Preserved segment"}]
                }
            }
            purge_res = mgr.execute_purge(tasks=mock_tasks)
            assert purge_res["status"] == "success"
            assert purge_res["removed_count"] >= 2
            assert purge_res["preserved_transcripts"] == 1

            # Invariant checks:
            # 1. Old audio files removed
            assert not old_wav.exists()
            assert not old_chunks_dir.exists()

            # 2. Fresh audio preserved
            assert fresh_wav.exists()

            # 3. TRANSCRIPT DATA GUARANTEE: JSON and SRT untouched
            assert transcript_json.exists(), "CRITICAL: Transcript JSON was deleted during purge!"
            assert transcript_srt.exists(), "CRITICAL: Subtitles were deleted during purge!"
            assert mock_tasks["old_audio"]["audio_purged"] is True
            assert len(mock_tasks["old_audio"]["segments"]) == 1

        finally:
            config.upload_dir = orig_upload_dir


def test_workspace_full_archive_backup():
    """Tests SRT/VTT formatting, summary calculations, and complete ZIP archive generation."""
    sample_segments = [
        {"start": 1.25, "end": 4.5, "speaker": "Speaker 0", "text": "Welcome to the Supreme Council."},
        {"start": 5.0, "end": 9.123, "speaker": "Speaker 1", "text": "Verifying precision acoustic alignment."}
    ]

    # SRT timestamp formatting
    assert format_timestamp_srt(1.25) == "00:00:01,250"
    srt_text = generate_srt(sample_segments)
    assert "00:00:01,250 --> 00:00:04,500" in srt_text
    assert "[Speaker 0] Welcome to the Supreme Council." in srt_text

    # VTT timestamp formatting
    assert format_timestamp_vtt(1.25) == "00:00:01.250"
    vtt_text = generate_vtt(sample_segments)
    assert "WEBVTT" in vtt_text
    assert "00:00:01.250 --> 00:00:04.500" in vtt_text
    assert "<v Speaker 0>Welcome to the Supreme Council." in vtt_text

    # Summary calculations
    mock_tasks = {
        "sess-1": {
            "duration": 3600.0,
            "segments": sample_segments,
            "full_text": "Welcome to the Supreme Council. Verifying precision acoustic alignment."
        }
    }
    summary = get_workspace_summary(mock_tasks)
    assert summary["total_sessions"] == 1
    assert summary["total_segments"] == 2
    assert summary["total_words"] == 9
    assert summary["total_duration_hours"] == 1.0

    # Archive creation
    zip_buffer = create_workspace_archive(mock_tasks)
    assert isinstance(zip_buffer, io.BytesIO)

    with zipfile.ZipFile(zip_buffer, "r") as zf:
        namelist = zf.namelist()
        assert "transcripts/sess-1.json" in namelist
        assert "transcripts/sess-1.txt" in namelist
        assert "transcripts/sess-1.srt" in namelist
        assert "transcripts/sess-1.vtt" in namelist
        assert "glossary.json" in namelist
        assert "settings.json" in namelist
        assert "manifest.json" in namelist
        assert "supervisor_journal.json" in namelist

        # Inspect manifest
        manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest_data["total_sessions"] == 1
        assert manifest_data["archive_generator"] == "Transcript Suite v1.5.3"


def test_phase5_api_endpoints():
    """Integration test for all Phase 5 REST endpoints."""
    client = TestClient(app)

    # 1. Test Batch Ingestion Endpoint
    file_content = b"WAVE_TEST_AUDIO_BYTES_BATCH"
    files = [
        ("files", ("audio1.wav", file_content, "audio/wav")),
        ("files", ("audio2.wav", file_content, "audio/wav"))
    ]
    res = client.post("/api/ingest/batch", files=files, data={"diarizer": "nemo", "enable_council": "true"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "enqueued"
    assert data["count"] == 2
    items = data["items"]
    assert len(items) == 2

    # 2. Test Get Batch Status
    res = client.get("/api/ingest/batch")
    assert res.status_code == 200
    batch_status = res.json()
    assert "queue" in batch_status

    # 3. Test Batch Pause and Resume
    res_pause = client.post("/api/ingest/batch/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["is_paused"] is True

    res_resume = client.post("/api/ingest/batch/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["is_paused"] is False

    # 4. Test Checkpoints Endpoints
    res = client.get("/api/tasks/checkpoints")
    assert res.status_code == 200
    assert "checkpoints" in res.json()

    # 5. Test Storage Retention Endpoints
    res = client.get("/api/storage/retention")
    assert res.status_code == 200
    retention_info = res.json()
    assert "policy" in retention_info
    assert "total_audio_mb" in retention_info

    # Update retention policy
    res = client.post("/api/storage/retention/policy", json={"audio_retention_days": 7, "storage_quota_gb": 12.0})
    assert res.status_code == 200
    assert res.json()["policy"]["audio_retention_days"] == 7
    assert res.json()["policy"]["storage_quota_gb"] == 12.0

    # Run retention purge
    res = client.post("/api/storage/retention/run")
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # 6. Test Workspace Summary & Full Archive Export
    res = client.get("/api/workspace/summary")
    assert res.status_code == 200
    summary = res.json()
    assert "total_sessions" in summary

    # Export backup (.zip stream)
    res = client.get("/api/workspace/backup")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment; filename=transcript_workspace_backup_" in res.headers["content-disposition"]
    # Check standard zip magic bytes (PK\x03\x04)
    assert res.content[:4] == b"PK\x03\x04"


def test_phase5_ui_html_and_css():
    """Verifies that all Phase 5 elements and CSS classes exist in static assets."""
    client = TestClient(app)

    # HTML Verification
    res_html = client.get("/")
    assert res_html.status_code == 200
    html = res_html.text

    # 5.1.A Batch queue section & controls
    assert 'id="batchQueueSection"' in html
    assert 'id="batchCountBadge"' in html
    assert 'id="batchStatusChip"' in html
    assert 'id="btnPauseBatch"' in html
    assert 'id="btnResumeBatch"' in html
    assert 'id="btnClearBatch"' in html
    assert 'id="batchQueueList"' in html
    assert 'multiple' in html

    # 5.2.A Checkpoints & Resumable Tasks section
    assert 'id="checkpointsSection"' in html
    assert 'id="checkpointsBadge"' in html
    assert 'id="btnRefreshCheckpoints"' in html

    # 5.2.B Storage Retention Card
    assert 'id="storageRetentionCard"' in html
    assert 'id="inputRetentionDays"' in html
    assert 'id="inputStorageQuota"' in html
    assert 'id="toggleAutoPurge"' in html
    assert 'id="btnSaveRetentionPolicy"' in html
    assert 'id="btnRunRetentionPurge"' in html

    # 5.3.A Workspace Backup Card
    assert 'id="workspaceBackupCard"' in html
    assert 'id="btnExportWorkspaceBackup"' in html

    # 5.3.B Workspace Fast Navigation Shortcuts
    assert 'Workspace &amp; Fast Navigation' in html
    assert 'Alt' in html
    assert 'Alt + 1' in html or '<kbd>Alt</kbd> + <kbd>1</kbd>' in html
    assert 'Alt + Q' in html or '<kbd>Alt</kbd> + <kbd>Q</kbd>' in html
    assert 'Alt + H' in html or '<kbd>Alt</kbd> + <kbd>H</kbd>' in html

    # CSS Verification
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    css = res_css.text

    assert ".batch-queue-card" in css
    assert ".batch-queue-header" in css
    assert ".batch-item-row" in css
    assert ".batch-status-chip" in css
    assert ".checkpoints-card" in css
    assert ".btn-checkpoint-resume" in css
