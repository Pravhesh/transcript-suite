"""
Transcript export utilities.
Generates clean, readable plain text transcripts with customizable speaker aliases and timestamps.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    """Formats seconds into [HH:MM:SS] or [MM:SS]."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"[{hrs:02d}:{mins:02d}:{secs:02d}]"
    return f"[{mins:02d}:{secs:02d}]"


class TranscriptExporter:
    @staticmethod
    def export_text(
        segments: List[Dict[str, Any]],
        output_path: Optional[str | Path] = None,
        speaker_aliases: Optional[Dict[str, str]] = None,
        include_timestamps: bool = True,
        include_speakers: bool = True
    ) -> str:
        """
        Exports segments to clean plain text format.
        """
        aliases = speaker_aliases or {}
        lines = []

        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue

            raw_speaker = seg.get("speaker", "Speaker 0")
            speaker = aliases.get(raw_speaker, raw_speaker)

            line_parts = []
            if include_timestamps:
                ts = format_timestamp(seg.get("start", 0.0))
                line_parts.append(ts)

            if include_speakers:
                line_parts.append(f"{speaker}:")

            line_parts.append(text)
            lines.append(" ".join(line_parts))

        formatted_content = "\n\n".join(lines)

        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(formatted_content, encoding="utf-8")

        return formatted_content
