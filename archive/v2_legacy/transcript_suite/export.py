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


def format_srt_timestamp(seconds: float) -> str:
    """Formats seconds into SRT format: HH:MM:SS,mmm"""
    total_ms = int(round(seconds * 1000))
    hrs = total_ms // 3600000
    mins = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Formats seconds into WebVTT format: HH:MM:SS.mmm"""
    total_ms = int(round(seconds * 1000))
    hrs = total_ms // 3600000
    mins = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{ms:03d}"


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

    @staticmethod
    def export_srt(
        segments: List[Dict[str, Any]],
        output_path: Optional[str | Path] = None,
        speaker_aliases: Optional[Dict[str, str]] = None
    ) -> str:
        """Exports segments to SubRip Subtitle (.srt) format."""
        aliases = speaker_aliases or {}
        entries = []
        for idx, seg in enumerate(segments, start=1):
            text = seg.get("text", "").strip()
            if not text:
                continue
            start_s = float(seg.get("start", 0.0))
            end_s = float(seg.get("end", start_s + 1.0))
            raw_speaker = seg.get("speaker", "Speaker 0")
            speaker = aliases.get(raw_speaker, raw_speaker)

            start_str = format_srt_timestamp(start_s)
            end_str = format_srt_timestamp(end_s)

            entries.append(f"{idx}\n{start_str} --> {end_str}\n[{speaker}] {text}")

        content = "\n\n".join(entries)
        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        return content

    @staticmethod
    def export_vtt(
        segments: List[Dict[str, Any]],
        output_path: Optional[str | Path] = None,
        speaker_aliases: Optional[Dict[str, str]] = None
    ) -> str:
        """Exports segments to WebVTT (.vtt) format."""
        aliases = speaker_aliases or {}
        entries = ["WEBVTT\n"]
        for idx, seg in enumerate(segments, start=1):
            text = seg.get("text", "").strip()
            if not text:
                continue
            start_s = float(seg.get("start", 0.0))
            end_s = float(seg.get("end", start_s + 1.0))
            raw_speaker = seg.get("speaker", "Speaker 0")
            speaker = aliases.get(raw_speaker, raw_speaker)

            start_str = format_vtt_timestamp(start_s)
            end_str = format_vtt_timestamp(end_s)

            entries.append(f"{idx}\n{start_str} --> {end_str}\n<v {speaker}>{text}")

        content = "\n\n".join(entries)
        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        return content

    @staticmethod
    def export_json(
        task_data: Dict[str, Any],
        output_path: Optional[str | Path] = None
    ) -> str:
        """Exports full transcription task metadata and segments as structured JSON."""
        import json
        content = json.dumps(task_data, indent=2, ensure_ascii=False)
        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        return content

    @staticmethod
    def export_markdown(
        task_data: Dict[str, Any],
        output_path: Optional[str | Path] = None,
        speaker_aliases: Optional[Dict[str, str]] = None
    ) -> str:
        """Exports transcript with Council Deliberation and Audex <think> reasoning notes."""
        aliases = speaker_aliases or {}
        filename = task_data.get("filename", "Audio Recording")
        duration = float(task_data.get("duration", 0.0))
        created = task_data.get("created_at", "")
        segments = task_data.get("segments", [])

        mins = int(duration // 60)
        secs = int(duration % 60)
        dur_str = f"{mins:02d}:{secs:02d}"

        unique_speakers = sorted(list({aliases.get(s.get("speaker", ""), s.get("speaker", "")) for s in segments if s.get("speaker")}))

        lines = [
            f"# 🌲 Transcript: {filename}",
            f"- **Date / Time**: {created or 'N/A'}",
            f"- **Duration**: {dur_str} ({round(duration, 1)}s)",
            f"- **Speakers Identified**: {', '.join(unique_speakers) if unique_speakers else 'None'}",
            f"- **Total Segments**: {len(segments)}",
            "\n---\n"
        ]

        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue
            raw_speaker = seg.get("speaker", "Speaker 0")
            speaker = aliases.get(raw_speaker, raw_speaker)
            start_str = format_timestamp(float(seg.get("start", 0.0)))
            end_str = format_timestamp(float(seg.get("end", 0.0)))

            lines.append(f"### {start_str} - {end_str} | **{speaker}**")
            lines.append(f"> \"{text}\"")

            council = seg.get("council", {})
            if council:
                agr = council.get("agreement_type", "CONSENSUS")
                score = council.get("consensus_score", 1.0)
                notes = council.get("deliberation_notes", "").strip()
                lines.append(f"\n*Council Verdict:* `{agr}` (Score: `{score}`)")
                if notes:
                    lines.append(f"\n```text\n{notes}\n```")

            lines.append("\n")

        content = "\n".join(lines)
        if output_path:
            out = Path(output_path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
        return content
