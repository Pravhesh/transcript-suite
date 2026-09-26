"""
Storage Quotas & Auto-Purge Retention Policies (Sub-Phase 5.2.B).

Automatically purges old or quota-exceeding raw audio files (*_orig.wav,
*_processed.wav, *_chunks/) while preserving JSON transcripts, metadata,
and subtitles forever with zero data loss.
"""

import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from ..config import config
from ..asr.memory import get_subsystem_supervisor


class RetentionManager:
    """
    Manages audio lifecycle retention policies and disk quota enforcement.
    Strictly guarantees that JSON transcripts, SRT subtitles, and metadata
    are never deleted during an audio purge.
    """

    def __init__(self):
        self.supervisor = get_subsystem_supervisor()

    def get_policy(self) -> Dict[str, Any]:
        """Returns the active retention policy configuration."""
        return {
            "audio_retention_days": int(getattr(config, "audio_retention_days", 14)),
            "storage_quota_gb": float(getattr(config, "storage_quota_gb", 15.0)),
            "auto_purge_enabled": bool(getattr(config, "auto_purge_enabled", False))
        }

    def update_policy(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates and persists retention policy settings to settings.json."""
        allowed = {}
        if "audio_retention_days" in updates:
            allowed["audio_retention_days"] = max(0, int(updates["audio_retention_days"]))
        if "storage_quota_gb" in updates:
            allowed["storage_quota_gb"] = max(1.0, float(updates["storage_quota_gb"]))
        if "auto_purge_enabled" in updates:
            allowed["auto_purge_enabled"] = bool(updates["auto_purge_enabled"])

        saved = config.save_persistent_settings(allowed)
        for k, v in allowed.items():
            setattr(config, k, v)

        self.supervisor.log_journal(
            severity="INFO",
            subsystem="storage",
            event_type="RETENTION_POLICY_UPDATED",
            message=f"Retention policy updated: days={self.get_policy()['audio_retention_days']}, quota={self.get_policy()['storage_quota_gb']} GB, auto_purge={self.get_policy()['auto_purge_enabled']}.",
            details=allowed
        )
        return self.get_policy()

    def scan_purging_targets(self, tasks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Performs a dry-run scan of audio files that are candidates for purging.
        Returns candidate files, total reclaimable MB, and active audio disk usage.
        """
        policy = self.get_policy()
        retention_days = policy["audio_retention_days"]
        quota_gb = policy["storage_quota_gb"]
        quota_bytes = quota_gb * (1024 ** 3)

        now = time.time()
        retention_cutoff = now - (retention_days * 86400) if retention_days > 0 else 0

        audio_files: List[Tuple[Path, float, float]] = []  # (path, size_bytes, mtime)
        upload_dir = config.upload_dir
        tmp_dir = config.tmp_dir

        # Scan upload_dir for audio and chunks
        if upload_dir.exists():
            for p in upload_dir.iterdir():
                if p.is_dir() and p.name.endswith("_chunks"):
                    dir_size = sum(f.stat().st_size for f in p.glob("*.wav") if f.is_file())
                    audio_files.append((p, dir_size, p.stat().st_mtime))
                elif p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".aac", ".m4a", ".flac"):
                    audio_files.append((p, p.stat().st_size, p.stat().st_mtime))

        total_audio_bytes = sum(size for _, size, _ in audio_files)
        total_audio_mb = round(total_audio_bytes / (1024 * 1024), 2)

        candidates: List[Path] = []
        reclaimable_bytes = 0

        # 1. Target files older than retention days
        for path, size, mtime in audio_files:
            if retention_cutoff > 0 and mtime < retention_cutoff:
                candidates.append(path)
                reclaimable_bytes += size

        # 2. If remaining audio still exceeds quota, purge oldest files first
        remaining_files = [(p, s, m) for p, s, m in audio_files if p not in candidates]
        remaining_files.sort(key=lambda x: x[2])  # Sort by mtime ascending (oldest first)

        current_remaining_bytes = total_audio_bytes - reclaimable_bytes
        for path, size, _ in remaining_files:
            if current_remaining_bytes > quota_bytes:
                candidates.append(path)
                reclaimable_bytes += size
                current_remaining_bytes -= size
            else:
                break

        return {
            "policy": policy,
            "total_audio_mb": total_audio_mb,
            "reclaimable_mb": round(reclaimable_bytes / (1024 * 1024), 2),
            "candidates_count": len(candidates),
            "candidates": [str(c) for c in candidates]
        }

    def execute_purge(self, tasks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes retention purge of expired or quota-exceeding raw audio files.
        Guarantees that JSON transcripts, SRT subtitles, and metadata remain intact.
        """
        scan = self.scan_purging_targets(tasks)
        candidates = [Path(p) for p in scan.get("candidates", [])]

        freed_bytes = 0
        removed_count = 0

        for path in candidates:
            try:
                if path.is_dir():
                    size = sum(f.stat().st_size for f in path.glob("*.wav") if f.is_file())
                    shutil.rmtree(path)
                    freed_bytes += size
                    removed_count += 1
                elif path.is_file():
                    size = path.stat().st_size
                    path.unlink()
                    freed_bytes += size
                    removed_count += 1
            except Exception as e:
                print(f"[Retention Error] Failed to delete {path}: {e}")

        # Update tasks dictionary if provided
        preserved_transcripts = 0
        if tasks is not None:
            for tid, tdata in tasks.items():
                orig_wav = tdata.get("orig_file_path")
                proc_wav = tdata.get("processed_file_path")
                if (orig_wav and not Path(orig_wav).exists()) or (proc_wav and not Path(proc_wav).exists()):
                    tdata["audio_purged"] = True
                if tdata.get("segments"):
                    preserved_transcripts += 1

        freed_mb = round(freed_bytes / (1024 * 1024), 2)

        self.supervisor.log_journal(
            severity="INFO",
            subsystem="storage",
            event_type="RETENTION_PURGE_EXECUTED",
            message=f"Retention purge executed: freed {freed_mb} MB across {removed_count} audio files/dirs. {preserved_transcripts} transcripts preserved intact.",
            details={"freed_mb": freed_mb, "removed_count": removed_count}
        )

        return {
            "status": "success",
            "freed_mb": freed_mb,
            "removed_count": removed_count,
            "preserved_transcripts": preserved_transcripts,
            "remaining_audio_mb": round((scan.get("total_audio_mb", 0.0) - freed_mb), 2)
        }


_retention_manager_instance: Optional[RetentionManager] = None


def get_retention_manager() -> RetentionManager:
    global _retention_manager_instance
    if _retention_manager_instance is None:
        _retention_manager_instance = RetentionManager()
    return _retention_manager_instance
