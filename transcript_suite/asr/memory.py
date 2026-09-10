"""
GPU VRAM Safety and Memory Watcher tailored for 8GB VRAM (RTX 4060 Laptop).
"""

import gc
import torch
from typing import Dict, Any


class VRAMManager:
    def __init__(self, warning_threshold_gb: float = 7.0):
        self.warning_threshold_gb = warning_threshold_gb
        self.device_available = torch.cuda.is_available()

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns real-time GPU VRAM telemetry.
        """
        if not self.device_available:
            return {
                "available": False,
                "total_gb": 0.0,
                "allocated_gb": 0.0,
                "reserved_gb": 0.0,
                "free_gb": 0.0,
                "percent_used": 0.0,
                "device_name": "CPU"
            }

        total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        allocated = torch.cuda.memory_allocated(0) / (1024 ** 3)
        reserved = torch.cuda.memory_reserved(0) / (1024 ** 3)
        free = total - reserved

        return {
            "available": True,
            "total_gb": round(total, 2),
            "allocated_gb": round(allocated, 2),
            "reserved_gb": round(reserved, 2),
            "free_gb": round(free, 2),
            "percent_used": round((reserved / total) * 100, 1),
            "device_name": torch.cuda.get_device_name(0)
        }

    def clear_cache(self, force: bool = False):
        """
        Cleans up lingering CUDA memory allocations and triggers garbage collection.
        """
        if self.device_available:
            gc.collect()
            torch.cuda.empty_cache()

    def assert_safe_headroom(self):
        """
        Checks if VRAM headroom is dangerously low.
        """
        if not self.device_available:
            return
        stats = self.get_stats()
        if stats["reserved_gb"] >= self.warning_threshold_gb:
            self.clear_cache()
            stats = self.get_stats()
            if stats["reserved_gb"] >= self.warning_threshold_gb:
                print(f"[VRAM Warning] VRAM usage is high: {stats['reserved_gb']} GB / {stats['total_gb']} GB")
