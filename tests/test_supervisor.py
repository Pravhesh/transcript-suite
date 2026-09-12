"""
Tests for SubsystemSupervisor, predictive emergency governor, event journaling, and storage management.
"""

import json
import pytest
from transcript_suite.asr.memory import get_subsystem_supervisor, SubsystemSupervisor


def test_supervisor_singleton_and_subsystems():
    """Verify singleton instance and standard subsystem registration."""
    sup1 = get_subsystem_supervisor()
    sup2 = SubsystemSupervisor()
    assert sup1 is sup2

    assert "audio_preprocessor" in sup1.subsystems
    assert "stage_1_canary" in sup1.subsystems
    assert "stage_2_whisper" in sup1.subsystems
    assert "stage_3a_conformer" in sup1.subsystems
    assert "stage_3b_parakeet" in sup1.subsystems
    assert "stage_4_diarizer" in sup1.subsystems
    assert "web_server" in sup1.subsystems


def test_subsystem_stage_lifecycle():
    """Verify stage transitions: start -> end -> unload."""
    sup = get_subsystem_supervisor()

    # 1. Start stage
    sup.record_stage_start("stage_2_whisper", active_model="openai/whisper-large-v3")
    sub = sup.subsystems["stage_2_whisper"]
    assert sub["state"] == "running"
    assert sub["active_model"] == "openai/whisper-large-v3"

    # 2. End stage
    sup.record_stage_end("stage_2_whisper", duration_sec=4.5, audio_duration_sec=30.0)
    assert sub["state"] == "loaded"
    assert sub["last_runtime_sec"] == 4.5
    assert sub["rtfx"] == round(30.0 / 4.5, 2)

    # 3. Unload stage
    sup.record_stage_unload("stage_2_whisper", reclaimed_vram_mb=3500.0, reclaimed_ram_mb=120.0)
    assert sub["state"] == "evicted"
    assert sub["vram_allocated_mb"] == 0.0


def test_supervisor_running_live_metrics_and_finalize():
    """Verify live metric updating during stage execution and finalize_pipeline cleanup."""
    sup = get_subsystem_supervisor()

    # 1. Start stage
    sup.record_stage_start("stage_2_whisper", active_model="openai/whisper-large-v3")
    assert sup.subsystems["stage_2_whisper"]["state"] == "running"

    # 2. Sample telemetry while running
    report = sup.sample_telemetry()
    running_sub = next(s for s in report["subsystems"] if s["id"] == "stage_2_whisper")
    assert running_sub["state"] == "running"
    assert "last_runtime_sec" in running_sub

    # 3. Finalize pipeline
    sup.finalize_pipeline()
    assert sup.subsystems["stage_2_whisper"]["state"] == "evicted"
    assert sup.subsystems["stage_2_whisper"]["vram_allocated_mb"] == 0.0



def test_predictive_governor_telemetry():
    """Verify telemetry sampling, governor ceiling, and velocity metrics."""
    sup = get_subsystem_supervisor()
    report = sup.sample_telemetry()

    assert "subsystems" in report
    assert "governor" in report
    gov = report["governor"]
    assert "ceiling_gb" in gov
    assert "velocity_mb_s" in gov
    assert "free_headroom_mb" in gov
    assert gov["status"] in ("NORMAL", "WARNING", "EMERGENCY")


def test_dynamic_batch_throttling():
    """Verify batch throttler respects normal vs throttled recommendations."""
    sup = get_subsystem_supervisor()
    sup.suggested_batch_throttles.clear()

    # Normal state: returns default
    assert sup.get_suggested_batch_size("whisper", default_size=8) == 8

    # Throttled state: returns reduced size
    sup.suggested_batch_throttles["whisper"] = 2
    assert sup.get_suggested_batch_size("whisper", default_size=8) == 2


def test_event_journal_operations():
    """Verify logging, querying, filtering, exporting, and clearing journal."""
    sup = get_subsystem_supervisor()

    # Log specific events
    sup.log_journal("INFO", "test_sub", "TEST_INFO", "Informational test message.")
    sup.log_journal("EMERGENCY", "test_sub", "TEST_EMERGENCY", "Emergency test alert.")

    # Query all
    events = sup.get_journal(limit=10, severity="ALL")
    assert len(events) >= 2

    # Query emergency only
    em_events = sup.get_journal(limit=10, severity="EMERGENCY")
    assert all(e["severity"] == "EMERGENCY" for e in em_events)

    # Export JSON
    json_str = sup.export_journal(export_format="json")
    parsed = json.loads(json_str)
    assert isinstance(parsed, list)

    # Export CSV
    csv_str = sup.export_journal(export_format="csv")
    assert "Timestamp,Severity,Subsystem,Event Type,Message" in csv_str


def test_storage_breakdown_and_security_guard():
    """Verify storage inspection and guarded deletion."""
    sup = get_subsystem_supervisor()
    breakdown = sup.get_detailed_storage_breakdown()

    assert "targets" in breakdown
    assert "total_bytes" in breakdown
    assert "total_gb" in breakdown
    target_ids = [t["id"] for t in breakdown["targets"]]
    assert "hf_cache" in target_ids
    assert "nemo_cache" in target_ids
    assert "temp_audio" in target_ids

    # Protected target deletion attempt should be safely rejected
    res = sup.purge_storage("hf_cache")
    assert res["success"] is False
    assert "guarded" in res["message"]
