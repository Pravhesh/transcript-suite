"""
GPU VRAM and System RAM Safety Manager tailored for 8GB VRAM & 16GB System RAM.
Includes glibc malloc_trim to force memory release back to Linux kernel.
"""

import gc
import os
import ctypes
import torch
import psutil
from typing import Dict, Any


class VRAMManager:
    def __init__(self, warning_threshold_gb: float = 7.0):
        self.warning_threshold_gb = warning_threshold_gb
        self.device_available = torch.cuda.is_available()

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns real-time GPU VRAM and System RAM telemetry.
        """
        stats: Dict[str, Any] = {
            "available": self.device_available,
            "total_gb": 0.0,
            "allocated_gb": 0.0,
            "reserved_gb": 0.0,
            "free_gb": 0.0,
            "percent_used": 0.0,
            "device_name": "CPU",
            "proc_ram_used_gb": 0.0,
            "sys_ram_total_gb": 0.0,
            "sys_ram_used_gb": 0.0,
            "sys_ram_free_gb": 0.0,
            "sys_ram_percent": 0.0
        }

        # Process RSS (actual RAM consumed by this process) and System RAM
        try:
            proc = psutil.Process()
            rss_gb = round(proc.memory_info().rss / (1024 ** 3), 2)
            stats["proc_ram_used_gb"] = rss_gb
            stats["app_ram_rss_gb"] = rss_gb
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory()
            stats["sys_ram_total_gb"] = round(mem.total / (1024 ** 3), 2)
            stats["sys_ram_used_gb"] = round(mem.used / (1024 ** 3), 2)
            stats["sys_ram_free_gb"] = round(mem.available / (1024 ** 3), 2)
            stats["sys_ram_percent"] = round(mem.percent, 1)
        except Exception:
            pass

        if self.device_available:
            total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            allocated = torch.cuda.memory_allocated(0) / (1024 ** 3)
            reserved = torch.cuda.memory_reserved(0) / (1024 ** 3)
            free = total - reserved

            stats.update({
                "total_gb": round(total, 2),
                "allocated_gb": round(allocated, 2),
                "reserved_gb": round(reserved, 2),
                "free_gb": round(free, 2),
                "percent_used": round((reserved / total) * 100, 1),
                "device_name": torch.cuda.get_device_name(0)
            })

        return stats

    def clear_cache(self, force: bool = False):
        """
        Cleans up lingering CUDA memory allocations and triggers garbage collection.
        Forces glibc to trim heap and return RAM pages directly to the OS.
        """
        gc.collect()
        if self.device_available:
            torch.cuda.empty_cache()

        # Force glibc to return free heap memory to the Linux kernel
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
        except Exception:
            pass

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

