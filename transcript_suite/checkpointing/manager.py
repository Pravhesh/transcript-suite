"""
Automatic Crash Checkpointing & Resumable Tasks (Sub-Phase 5.2.A).

Persists transcription state atomically after each chunk, enabling seamless
resumption of interrupted or crashed transcription pipelines from chunk N+1.
"""

import os
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..config import config


class CheckpointManager:
    """
    Manages atomic on-disk task checkpoints in $BASE_DIR/checkpoints/.
    """

    def __init__(self, checkpoints_dir: Optional[Path] = None):
        self.checkpoints_dir = checkpoints_dir or (config.base_dir / "checkpoints")
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[Checkpoint Warning] Could not create {self.checkpoints_dir}: {e}")

    def _get_path(self, task_id: str) -> Path:
        return self.checkpoints_dir / f"checkpoint_{task_id}.json"

    def save_checkpoint(
        self,
        task_id: str,
        filename: str,
        file_path: str | Path,
        last_chunk_index: int,
        total_chunks: int,
        segments: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        duration: float = 0.0,
        status: str = "in_progress"
    ) -> bool:
        """
        Atomically saves task checkpoint after a chunk is processed.
        Uses a temporary file and atomic replace to prevent corrupt state.
        """
        self._ensure_dir()
        data = {
            "task_id": task_id,
            "filename": filename,
            "file_path": str(file_path),
            "last_chunk_index": int(last_chunk_index),
            "total_chunks": int(total_chunks),
            "segments": segments,
            "params": params or {},
            "duration": float(duration),
            "status": status,
            "updated_at": time.time()
        }

        target_path = self._get_path(task_id)
        tmp_path = target_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, target_path)
            return True
        except Exception as e:
            print(f"[Checkpoint Error] Failed to write checkpoint for {task_id}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    def load_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Loads task checkpoint data if it exists and is valid."""
        path = self._get_path(task_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"[Checkpoint Error] Failed to read checkpoint {path}: {e}")
            return None

    def list_resumable_checkpoints(self) -> List[Dict[str, Any]]:
        """Lists all incomplete checkpoints available on disk that can be resumed."""
        self._ensure_dir()
        resumable = []
        for p in self.checkpoints_dir.glob("checkpoint_*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") in ("in_progress", "interrupted", "stopped", "failed"):
                    # Only resumable if it didn't finish all chunks
                    last_idx = data.get("last_chunk_index", -1)
                    tot_chunks = data.get("total_chunks", 0)
                    if tot_chunks > 0 and last_idx < tot_chunks - 1:
                        resumable.append({
                            "task_id": data.get("task_id"),
                            "filename": data.get("filename"),
                            "last_chunk_index": last_idx,
                            "total_chunks": tot_chunks,
                            "segments_count": len(data.get("segments", [])),
                            "duration": data.get("duration", 0.0),
                            "status": data.get("status"),
                            "updated_at": data.get("updated_at")
                        })
            except Exception:
                continue
        resumable.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
        return resumable

    def mark_completed(self, task_id: str):
        """Marks checkpoint as completed or removes it."""
        path = self._get_path(task_id)
        if path.exists():
            try:
                # Retain with status="completed" or delete
                path.unlink()
            except Exception as e:
                print(f"[Checkpoint Warning] Could not remove completed checkpoint: {e}")

    def delete_checkpoint(self, task_id: str) -> bool:
        """Deletes checkpoint for a specific task."""
        path = self._get_path(task_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception:
                return False
        return False


_checkpoint_manager_instance: Optional[CheckpointManager] = None


def get_checkpoint_manager() -> CheckpointManager:
    global _checkpoint_manager_instance
    if _checkpoint_manager_instance is None:
        _checkpoint_manager_instance = CheckpointManager()
    return _checkpoint_manager_instance
