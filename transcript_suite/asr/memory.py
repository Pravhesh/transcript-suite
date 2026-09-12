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

    def get_deep_memory_trace(self) -> Dict[str, Any]:
        """
        Deep memory usage trace:
        1. Process internal memory sections (RSS, VMS, Shared, Data/Heap).
        2. Top 10 memory-consuming system processes (PID, Name, User, RAM MB/GB, % RAM).
        3. Detailed GPU VRAM breakdown (Allocated, Reserved, OS/External, Free Headroom).
        """
        trace: Dict[str, Any] = {
            "process": {
                "pid": os.getpid(),
                "name": "transcript-suite",
                "rss_mb": 0.0,
                "rss_gb": 0.0,
                "vms_mb": 0.0,
                "shared_mb": 0.0,
                "data_mb": 0.0,
            },
            "system_ram": {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "percent": 0.0
            },
            "top_processes": [],
            "gpu": {
                "available": self.device_available,
                "device_name": "CPU",
                "total_gb": 0.0,
                "allocated_gb": 0.0,
                "reserved_gb": 0.0,
                "external_os_gb": 0.0,
                "free_gb": 0.0,
                "utilization_percent": 0.0
            }
        }

        # 1. Current process memory
        try:
            p = psutil.Process()
            minfo = p.memory_info()
            rss_mb = round(minfo.rss / (1024 * 1024), 1)
            trace["process"] = {
                "pid": p.pid,
                "name": p.name(),
                "rss_mb": rss_mb,
                "rss_gb": round(rss_mb / 1024, 2),
                "vms_mb": round(minfo.vms / (1024 * 1024), 1),
                "shared_mb": round(getattr(minfo, "shared", 0) / (1024 * 1024), 1),
                "data_mb": round(getattr(minfo, "data", 0) / (1024 * 1024), 1),
            }
        except Exception:
            pass

        # 2. Host system memory
        try:
            mem = psutil.virtual_memory()
            trace["system_ram"] = {
                "total_gb": round(mem.total / (1024 ** 3), 2),
                "used_gb": round(mem.used / (1024 ** 3), 2),
                "free_gb": round(mem.available / (1024 ** 3), 2),
                "percent": round(mem.percent, 1)
            }
        except Exception:
            pass

        # 3. Top system processes by RAM
        try:
            procs = []
            for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "memory_percent"]):
                try:
                    info = proc.info
                    p_minfo = info.get("memory_info")
                    if p_minfo:
                        p_rss_mb = round(p_minfo.rss / (1024 * 1024), 1)
                        procs.append({
                            "pid": info["pid"],
                            "name": info.get("name") or "unknown",
                            "user": info.get("username") or "unknown",
                            "rss_mb": p_rss_mb,
                            "rss_gb": round(p_rss_mb / 1024, 2),
                            "percent": round(info.get("memory_percent") or 0.0, 1)
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            procs.sort(key=lambda x: x["rss_mb"], reverse=True)
            trace["top_processes"] = procs[:10]
        except Exception:
            pass

        # 4. GPU VRAM Breakdown
        if self.device_available:
            try:
                total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                allocated = torch.cuda.memory_allocated(0) / (1024 ** 3)
                reserved = torch.cuda.memory_reserved(0) / (1024 ** 3)
                free_device, total_device = torch.cuda.mem_get_info()
                free_gb = free_device / (1024 ** 3)
                external_os_gb = max(0.0, (total - free_gb) - reserved)

                trace["gpu"] = {
                    "available": True,
                    "device_name": torch.cuda.get_device_name(0),
                    "total_gb": round(total, 2),
                    "allocated_gb": round(allocated, 2),
                    "reserved_gb": round(reserved, 2),
                    "external_os_gb": round(external_os_gb, 2),
                    "free_gb": round(free_gb, 2),
                    "utilization_percent": round(((total - free_gb) / total) * 100, 1)
                }
            except Exception as e:
                print(f"[VRAM Warning] Failed to compute GPU memory trace: {e}")

        return trace


def get_deep_memory_trace() -> Dict[str, Any]:
    """Top-level helper to obtain deep memory trace."""
    return VRAMManager().get_deep_memory_trace()

