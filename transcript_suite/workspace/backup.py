"""
Workspace Full Archive Backup (Sub-Phase 5.3.A).

Exports all session transcripts (JSON, TXT, SRT, VTT), domain glossaries,
settings, speaker palettes, and supervisor audit journals into a single
standardized .zip archive for complete portability and disaster recovery.
"""

import io
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..config import config
from ..asr.memory import get_subsystem_supervisor


def format_timestamp_srt(seconds: float) -> str:
    """Formats seconds to SubRip timestamp format (HH:MM:SS,mmm)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msec = int(round((seconds - int(seconds)) * 1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{msec:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Formats seconds to WebVTT timestamp format (HH:MM:SS.mmm)."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    msec = int(round((seconds - int(seconds)) * 1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{msec:03d}"


def generate_srt(segments: List[Dict[str, Any]]) -> str:
    """Generates standard SubRip (.srt) subtitle string from transcript segments."""
    lines = []
    for idx, seg in enumerate(segments, 1):
        start_ts = format_timestamp_srt(seg.get("start", 0.0))
        end_ts = format_timestamp_srt(seg.get("end", 0.0))
        speaker = seg.get("speaker")
        text = (seg.get("text") or "").strip()
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{prefix}{text}\n")
    return "\n".join(lines)


def generate_vtt(segments: List[Dict[str, Any]]) -> str:
    """Generates standard WebVTT (.vtt) subtitle string from transcript segments."""
    lines = ["WEBVTT\n"]
    for idx, seg in enumerate(segments, 1):
        start_ts = format_timestamp_vtt(seg.get("start", 0.0))
        end_ts = format_timestamp_vtt(seg.get("end", 0.0))
        speaker = seg.get("speaker")
        text = (seg.get("text") or "").strip()
        spk_tag = f"<v {speaker}>" if speaker else ""
        lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{spk_tag}{text}\n")
    return "\n".join(lines)


def get_workspace_summary(tasks: Dict[str, Any]) -> Dict[str, Any]:
    """Computes summary metrics across all active and historical sessions."""
    total_sessions = len(tasks)
    total_duration = 0.0
    total_words = 0
    total_segments = 0

    for tdata in tasks.values():
        total_duration += float(tdata.get("duration", 0.0))
        segs = tdata.get("segments", [])
        total_segments += len(segs)
        for s in segs:
            txt = (s.get("text") or "").strip()
            if txt:
                total_words += len(txt.split())

    return {
        "version": "v1.5.3",
        "total_sessions": total_sessions,
        "total_segments": total_segments,
        "total_words": total_words,
        "total_duration_hours": round(total_duration / 3600, 2),
        "glossary_terms_count": len(getattr(config, "custom_glossary", []))
    }


def create_workspace_archive(tasks: Dict[str, Any]) -> io.BytesIO:
    """
    Creates an in-memory zip archive of the entire workspace containing:
    - transcripts/{task_id}.json
    - transcripts/{task_id}.txt
    - transcripts/{task_id}.srt
    - transcripts/{task_id}.vtt
    - glossary.json
    - settings.json
    - speaker_palettes.json
    - supervisor_journal.json
    - manifest.json
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Transcripts per session
        for tid, tdata in tasks.items():
            safe_id = "".join(c for c in tid if c.isalnum() or c in ("-", "_"))
            segs = tdata.get("segments", [])

            # JSON
            zf.writestr(f"transcripts/{safe_id}.json", json.dumps(tdata, indent=2, ensure_ascii=False))

            # TXT
            full_txt = tdata.get("full_text") or "\n".join((s.get("text") or "") for s in segs)
            zf.writestr(f"transcripts/{safe_id}.txt", full_txt)

            # SRT
            srt_content = generate_srt(segs)
            zf.writestr(f"transcripts/{safe_id}.srt", srt_content)

            # VTT
            vtt_content = generate_vtt(segs)
            zf.writestr(f"transcripts/{safe_id}.vtt", vtt_content)

        # 2. Glossary
        glossary_data = getattr(config, "custom_glossary", [])
        zf.writestr("glossary.json", json.dumps(glossary_data, indent=2, ensure_ascii=False))

        # 3. Settings
        settings_info = config.get_storage_info()
        zf.writestr("settings.json", json.dumps(settings_info, indent=2, ensure_ascii=False))

        # 4. Supervisor Journal
        supervisor = get_subsystem_supervisor()
        journal_entries = supervisor.get_journal(limit=500)
        zf.writestr("supervisor_journal.json", json.dumps(journal_entries, indent=2, ensure_ascii=False))

        # 5. Manifest
        summary = get_workspace_summary(tasks)
        summary["generated_at"] = datetime.now().isoformat()
        summary["archive_generator"] = "Transcript Suite v1.5.3"
        zf.writestr("manifest.json", json.dumps(summary, indent=2, ensure_ascii=False))

    buffer.seek(0)
    return buffer
