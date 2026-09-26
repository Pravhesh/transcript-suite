"""
Multi-File Sequential Ingest Queue Manager (Sub-Phase 5.1.A).

Provides sequential queueing, ordering, cancellation, and execution
controls for batch audio ingestion, ensuring strict adherence to the
8 GB consumer GPU memory ceiling.
"""

import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any


@dataclass
class BatchItem:
    item_id: str
    filename: str
    file_path: str
    file_size_mb: float
    status: str = "queued"  # "queued", "processing", "completed", "failed", "cancelled"
    progress: float = 0.0
    task_id: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["file_size_mb"] = round(self.file_size_mb, 2)
        d["progress"] = round(self.progress, 1)
        return d


class BatchIngestManager:
    """
    Thread-safe sequential batch manager for multi-file audio ingestion.
    Enforces 1-file-at-a-time processing to respect the 8 GB consumer GPU ceiling.
    """

    def __init__(self):
        self._items: Dict[str, BatchItem] = {}
        self._queue: List[str] = []  # Ordered item IDs
        self._current_item_id: Optional[str] = None
        self._is_paused: bool = False

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def enqueue(
        self,
        filename: str,
        file_path: str | Path,
        file_size_mb: float,
        options: Optional[Dict[str, Any]] = None
    ) -> BatchItem:
        """Enqueues a new audio file for sequential processing."""
        item_id = str(uuid.uuid4())[:8]
        item = BatchItem(
            item_id=item_id,
            filename=filename,
            file_path=str(file_path),
            file_size_mb=float(file_size_mb),
            options=options or {}
        )
        self._items[item_id] = item
        self._queue.append(item_id)
        return item

    def remove(self, item_id: str) -> bool:
        """Removes a queued item from the batch queue. Cannot remove if currently processing."""
        if item_id not in self._items:
            return False
        item = self._items[item_id]
        if item.status == "processing":
            return False
        if item_id in self._queue:
            self._queue.remove(item_id)
        item.status = "cancelled"
        return True

    def reorder(self, item_id: str, new_position: int) -> bool:
        """
        Moves a pending item to a new 0-indexed position within the queued items.
        Position 0 is the next item to run.
        """
        if item_id not in self._queue:
            return False
        self._queue.remove(item_id)
        new_pos = max(0, min(new_position, len(self._queue)))
        self._queue.insert(new_pos, item_id)
        return True

    def clear_pending(self) -> int:
        """Clears all queued items that have not yet started."""
        cleared = 0
        for item_id in list(self._queue):
            item = self._items.get(item_id)
            if item and item.status == "queued":
                item.status = "cancelled"
                self._queue.remove(item_id)
                cleared += 1
        return cleared

    def pause(self):
        """Pauses sequential execution of subsequent items."""
        self._is_paused = True

    def resume(self):
        """Resumes sequential execution."""
        self._is_paused = False

    def get_next_to_process(self) -> Optional[BatchItem]:
        """
        Returns the next queued item if no item is currently processing and the manager is not paused.
        """
        if self._is_paused:
            return None
        if self._current_item_id is not None:
            curr = self._items.get(self._current_item_id)
            if curr and curr.status == "processing":
                return None  # Still running current task

        while self._queue:
            next_id = self._queue.pop(0)
            item = self._items.get(next_id)
            if item and item.status == "queued":
                return item
        return None

    def mark_started(self, item_id: str, task_id: str):
        """Marks a batch item as processing and associates it with a background task ID."""
        if item_id in self._items:
            item = self._items[item_id]
            item.status = "processing"
            item.task_id = task_id
            item.started_at = time.time()
            self._current_item_id = item_id

    def mark_completed(self, item_id: str):
        """Marks a batch item as successfully finished."""
        if item_id in self._items:
            item = self._items[item_id]
            item.status = "completed"
            item.progress = 100.0
            item.completed_at = time.time()
        if self._current_item_id == item_id:
            self._current_item_id = None

    def mark_failed(self, item_id: str, error: str):
        """Marks a batch item as failed with an error message."""
        if item_id in self._items:
            item = self._items[item_id]
            item.status = "failed"
            item.error = str(error)
            item.completed_at = time.time()
        if self._current_item_id == item_id:
            self._current_item_id = None

    def update_progress(self, item_id: str, progress: float):
        """Updates live progress percentage for an item."""
        if item_id in self._items:
            self._items[item_id].progress = float(progress)

    def get_status(self) -> Dict[str, Any]:
        """Returns full status overview of the batch processor."""
        queued_items = []
        for idx, iid in enumerate(self._queue):
            item = self._items.get(iid)
            if item:
                d = item.to_dict()
                d["queue_position"] = idx + 1
                queued_items.append(d)

        current_dict = None
        if self._current_item_id:
            curr = self._items.get(self._current_item_id)
            if curr:
                current_dict = curr.to_dict()

        history_items = [
            item.to_dict() for item in self._items.values()
            if item.status in ("completed", "failed", "cancelled")
        ]
        history_items.sort(key=lambda x: x.get("completed_at") or 0, reverse=True)

        return {
            "is_paused": self._is_paused,
            "has_active_task": current_dict is not None,
            "current_item": current_dict,
            "queued_count": len(queued_items),
            "queue": queued_items,
            "history": history_items[:20],
            "total_items": len(self._items)
        }


# Singleton instance
_batch_manager_instance: Optional[BatchIngestManager] = None


def get_batch_manager() -> BatchIngestManager:
    global _batch_manager_instance
    if _batch_manager_instance is None:
        _batch_manager_instance = BatchIngestManager()
    return _batch_manager_instance
