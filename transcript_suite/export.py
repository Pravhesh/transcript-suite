"""
Transcript export utilities.
Generates clean, readable plain text transcripts with customizable speaker aliases and timestamps.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    """Formats seconds into [HH:MM:SS] or [MM:SS]."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def export_text(
    segments: List[Dict[str, Any]],
    output_path: Optional[str | Path] = None,
    speaker_aliases: Optional[Dict[str, str]] = None,
    include_timestamps: bool = True,
    include_speakers: bool = True
) -> str:
    """Exports segments to clean plain text format."""
    aliases = speaker_aliases or {}
    lines = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        speaker = aliases.get(seg.get("speaker", "Speaker 0"), seg.get("speaker", "Speaker 0"))
        parts = []
        if include_timestamps:
            parts.append(format_timestamp(seg.get("start", 0.0)))
        if include_speakers:
            parts.append(f"{speaker}:")
        parts.append(text)
        lines.append(" ".join(parts))

    formatted_content = "\n\n".join(lines)
    if output_path:
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(formatted_content, encoding="utf-8")
    return formatted_content


class TranscriptExporter:
    export_text = staticmethod(export_text)
