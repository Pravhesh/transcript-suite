import gc
import os
import time
import json
import io
import csv
import ctypes
from pathlib import Path
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional
import torch
import psutil


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
            "sys_ram_percent": 0.0,
            "sys_ram_without_suite_gb": 0.0
        }

        # Process RSS (actual RAM consumed by this process) and System RAM
        rss_gb = 0.0
        try:
            proc = psutil.Process()
            rss_gb = round(proc.memory_info().rss / (1024 ** 3), 2)
            stats["proc_ram_used_gb"] = rss_gb
            stats["app_ram_rss_gb"] = rss_gb
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory()
            sys_used = round(mem.used / (1024 ** 3), 2)
            stats["sys_ram_total_gb"] = round(mem.total / (1024 ** 3), 2)
            stats["sys_ram_used_gb"] = sys_used
            stats["sys_ram_free_gb"] = round(mem.available / (1024 ** 3), 2)
            stats["sys_ram_percent"] = round(mem.percent, 1)
            stats["sys_ram_without_suite_gb"] = max(0.0, round(sys_used - rss_gb, 2))
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
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        proc_rss_mb = 0.0
        try:
            p = psutil.Process()
            minfo = p.memory_info()
            proc_rss_mb = round(minfo.rss / (1024 * 1024), 1)
            trace["process"] = {
                "pid": p.pid,
                "name": p.name(),
                "rss_mb": proc_rss_mb,
                "rss_gb": round(proc_rss_mb / 1024, 2),
                "vms_mb": round(minfo.vms / (1024 * 1024), 1),
                "shared_mb": round(getattr(minfo, "shared", 0) / (1024 * 1024), 1),
                "data_mb": round(getattr(minfo, "data", 0) / (1024 * 1024), 1),
            }
        except Exception:
            pass

        # 2. Top system processes & sum of all processes across OS
        total_procs_count = 0
        total_all_procs_rss_mb = 0.0
        procs = []
        try:
            for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "memory_percent"]):
                total_procs_count += 1
                try:
                    info = proc.info
                    p_minfo = info.get("memory_info")
                    if p_minfo:
                        p_rss_mb = round(p_minfo.rss / (1024 * 1024), 1)
                        total_all_procs_rss_mb += p_rss_mb
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
            trace["all_processes"] = procs
        except Exception:
            pass

        # 3. Host system memory & breakdown (Kernel Zswap, Shared, Cache)
        try:
            mem = psutil.virtual_memory()
            zswap_mb = 0.0
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("Zswap:"):
                            zswap_mb = round(int(line.split()[1]) / 1024, 1)
                            break
            except Exception:
                pass

            app_rss_gb = round(proc_rss_mb / 1024, 2)
            all_procs_gb = round(total_all_procs_rss_mb / 1024, 2)
            other_procs_gb = max(0.0, round((total_all_procs_rss_mb - proc_rss_mb) / 1024, 2))
            shared_gb = round(getattr(mem, "shared", 0) / (1024 ** 3), 2)
            cached_gb = round(getattr(mem, "cached", 0) / (1024 ** 3), 2)
            zswap_gb = round(zswap_mb / 1024, 2)

            used_gb = round(mem.used / (1024 ** 3), 2)
            sys_without_suite_gb = max(0.0, round(used_gb - app_rss_gb, 2))

            trace["system_ram"] = {
                "total_gb": round(mem.total / (1024 ** 3), 2),
                "used_gb": used_gb,
                "free_gb": round(mem.available / (1024 ** 3), 2),
                "percent": round(mem.percent, 1),
                "app_rss_gb": app_rss_gb,
                "sys_ram_without_suite_gb": sys_without_suite_gb,
                "all_procs_gb": all_procs_gb,
                "other_procs_gb": other_procs_gb,
                "shared_gb": shared_gb,
                "cached_gb": cached_gb,
                "zswap_gb": zswap_gb,
                "total_procs_count": total_procs_count
            }
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


def format_memory_audit_text(trace: Optional[Dict[str, Any]] = None) -> str:
    """
    Formats complete system memory, GPU VRAM, Suite process heap, and full process list
    into a clean, aligned, human-readable ASCII text audit suitable for clipboard or logs.
    """
    if trace is None:
        trace = get_deep_memory_trace()

    lines = []
    lines.append("=" * 80)
    lines.append("TRANSCRIPT SUITE - SYSTEM MEMORY & PROCESS AUDIT")
    ts = trace.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"Timestamp: {ts}")
    lines.append("=" * 80)
    lines.append("")

    # 1. Host System RAM
    sys = trace.get("system_ram", {})
    lines.append("1. HOST SYSTEM RAM BREAKDOWN")
    lines.append("-" * 80)
    total_ram = sys.get("total_gb", 0.0)
    used_ram = sys.get("used_gb", 0.0)
    pct_ram = sys.get("percent", 0.0)
    app_rss = sys.get("app_rss_gb", 0.0)
    without_suite = sys.get("sys_ram_without_suite_gb", max(0.0, round(used_ram - app_rss, 2)))
    free_ram = sys.get("free_gb", 0.0)

    lines.append(f"Total Physical RAM:       {total_ram:.2f} GB")
    lines.append(f"Total Machine RAM Used:   {used_ram:.2f} GB ({pct_ram:.1f}%)")
    lines.append(f"↳ Suite Memory Only (RSS): {app_rss:.2f} GB (Process RSS)")
    lines.append(f"↳ RAM Without Suite:       {without_suite:.2f} GB (OS daemons & other apps)")
    zswap = sys.get("zswap_gb", 0.0)
    shm = sys.get("shared_gb", 0.0)
    cache = sys.get("cached_gb", 0.0)
    lines.append(f"↳ Kernel Zswap & Shared:   zswap: {zswap:.2f} GB | shm: {shm:.2f} GB | cache: {cache:.2f} GB")
    lines.append(f"Free / Available RAM:      {free_ram:.2f} GB")
    lines.append("")

    # 2. GPU VRAM
    gpu = trace.get("gpu", {})
    if gpu.get("available"):
        lines.append(f"2. GPU VRAM BREAKDOWN - {gpu.get('device_name', 'CUDA Device')}")
        lines.append("-" * 80)
        lines.append(f"Total GPU VRAM:           {gpu.get('total_gb', 0.0):.2f} GB")
        lines.append(f"Allocated (PyTorch):      {gpu.get('allocated_gb', 0.0):.2f} GB")
        lines.append(f"Reserved CUDA Pool:       {gpu.get('reserved_gb', 0.0):.2f} GB")
        lines.append(f"External OS / Display:    {gpu.get('external_os_gb', 0.0):.2f} GB")
        lines.append(f"Free Device VRAM:         {gpu.get('free_gb', 0.0):.2f} GB ({gpu.get('utilization_percent', 0.0):.1f}% utilized)")
    else:
        lines.append("2. GPU VRAM: GPU acceleration not active / running on CPU")
    lines.append("")

    # 3. Transcript Suite Process Details
    proc = trace.get("process", {})
    suite_pid = proc.get("pid")
    lines.append(f"3. TRANSCRIPT SUITE PROCESS DETAILS (PID: {suite_pid})")
    lines.append("-" * 80)
    lines.append(f"Process Name:             {proc.get('name', 'python')}")
    lines.append(f"Resident Memory (RSS):    {proc.get('rss_mb', 0.0):.1f} MB ({proc.get('rss_gb', 0.0):.2f} GB)")
    lines.append(f"Virtual Memory (VMS):     {proc.get('vms_mb', 0.0):.1f} MB")
    lines.append(f"Shared Libraries:         {proc.get('shared_mb', 0.0):.1f} MB")
    lines.append(f"Heap / Data Segments:     {proc.get('data_mb', 0.0):.1f} MB")
    lines.append("")

    # 4. Entire Process List
    all_procs = trace.get("all_processes") or trace.get("top_processes") or []
    lines.append(f"4. COMPLETE SYSTEM PROCESS LIST ({len(all_procs)} Active Processes)")
    lines.append("-" * 80)
    lines.append(f"{'PID':<8} | {'USER':<16} | {'RAM (MB)':>10} | {'RAM (GB)':>9} | {'% MEM':>6} | {'PROCESS NAME'}")
    lines.append("-" * 80)

    for p in all_procs:
        p_pid = p.get("pid", 0)
        p_name = p.get("name", "unknown")
        if p_pid == suite_pid:
            p_name = f"{p_name} [This Suite]"
        p_user = str(p.get("user", "unknown"))[:16]
        p_rss_mb = p.get("rss_mb", 0.0)
        p_rss_gb = p.get("rss_gb", 0.0)
        p_pct = p.get("percent", 0.0)
        lines.append(f"{p_pid:<8} | {p_user:<16} | {p_rss_mb:>9.1f}M | {p_rss_gb:>8.2f}G | {p_pct:>5.1f}% | {p_name}")

    lines.append("-" * 80)
    lines.append(f"Total Process Count: {len(all_procs)} | Aggregated Process RSS: {sys.get('all_procs_gb', 0.0):.2f} GB")
    lines.append("=" * 80)
    return "\n".join(lines)


class SubsystemSupervisor:
    """
    Advanced Supervisory Engine that tracks and controls each internal process
    and subsystem of Transcript Suite with predictive emergency handling and event journaling.
    """
    _instance: Optional["SubsystemSupervisor"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SubsystemSupervisor, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        from ..config import config
        self.config = config
        self.vram_manager = VRAMManager()
        self.journal_file = Path(self.config.base_dir) / "supervisor_journal.jsonl"
        self.recent_events: deque = deque(maxlen=250)
        self.vram_history: deque = deque(maxlen=60)  # (timestamp, allocated_mb, reserved_mb)
        self.last_velocity_mb_s: float = 0.0
        self.active_emergency_alert: Optional[Dict[str, Any]] = None
        self.suggested_batch_throttles: Dict[str, int] = {}
        self._load_recent_journal()

        # Known internal subsystems of Transcript Suite
        self.subsystems: Dict[str, Dict[str, Any]] = {
            "audio_preprocessor": {
                "id": "audio_preprocessor",
                "name": "Audio Preprocessor & Vocal Limiter",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": "Torch STFT & Bandpass Filter",
                "can_eject": False,
                "last_updated": datetime.now().isoformat()
            },
            "stage_1_canary": {
                "id": "stage_1_canary",
                "name": "Pass 1: Canary-Qwen Lead Justice",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": self.config.model_name,
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "stage_2_whisper": {
                "id": "stage_2_whisper",
                "name": "Pass 2: Whisper Cross-Examiner",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": self.config.whisper_model,
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "stage_3a_conformer": {
                "id": "stage_3a_conformer",
                "name": "Pass 3A: Conformer-CTC Anchor",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": self.config.conformer_model,
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "stage_3b_parakeet": {
                "id": "stage_3b_parakeet",
                "name": "Pass 3B: Parakeet-TDT Transducer",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": self.config.parakeet_model,
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "stage_4_diarizer": {
                "id": "stage_4_diarizer",
                "name": "Pass 4: Speaker Diarization",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": self.config.default_diarizer,
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "stage_5_audex": {
                "id": "stage_5_audex",
                "name": "Pass 5: Audex-2B Supreme Adjudicator",
                "category": "pipeline_stage",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": getattr(self.config, "audex_model_id", "nvidia/Nemotron-Labs-Audex-2B"),
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            },
            "web_server": {
                "id": "web_server",
                "name": "Web Server & Telemetry Engine",
                "category": "service",
                "state": "running",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": "FastAPI / Uvicorn",
                "can_eject": False,
                "last_updated": datetime.now().isoformat()
            },
            "audio_buffers": {
                "id": "audio_buffers",
                "name": "Live Tensors & Audio Cache",
                "category": "cache",
                "state": "idle",
                "vram_allocated_mb": 0.0,
                "vram_reserved_mb": 0.0,
                "vram_peak_mb": 0.0,
                "ram_rss_mb": 0.0,
                "last_runtime_sec": 0.0,
                "rtfx": 0.0,
                "active_model": "PyTorch RAM / VRAM Buffers",
                "can_eject": True,
                "last_updated": datetime.now().isoformat()
            }
        }

        # Log supervisor startup
        self.log_journal(
            severity="INFO",
            subsystem="supervisor",
            event_type="SUPERVISOR_ONLINE",
            message="Subsystem Supervisor & Predictive Emergency Governor initialized.",
            details={"governor_threshold_gb": self.config.vram_governor_threshold_gb}
        )

    def _load_recent_journal(self):
        """Pre-loads recent events from journal file on disk."""
        if not self.journal_file.exists():
            return
        try:
            with open(self.journal_file, "r", encoding="utf-8") as f:
                lines = f.readlines()[-200:]
                for line in lines:
                    line = line.strip()
                    if line:
                        self.recent_events.append(json.loads(line))
        except Exception as e:
            print(f"[Supervisor Warning] Failed to read journal file: {e}")

    def log_journal(
        self,
        severity: str,
        subsystem: str,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Appends an event to the persistent journal and in-memory log.
        Severity: 'INFO', 'WARNING', 'EMERGENCY'.
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "severity": severity.upper(),
            "subsystem": subsystem,
            "event_type": event_type,
            "message": message,
            "details": details or {}
        }
        self.recent_events.append(entry)

        try:
            self.journal_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"[Supervisor Error] Failed to append to journal: {e}")

        return entry

    def record_stage_start(self, subsystem_id: str, active_model: Optional[str] = None):
        """Marks a pipeline subsystem as running and snapshots memory baselines."""
        if subsystem_id not in self.subsystems:
            return

        sub = self.subsystems[subsystem_id]
        sub["state"] = "running"
        sub["start_time"] = time.time()
        sub["last_runtime_sec"] = 0.0
        if active_model:
            sub["active_model"] = active_model
        sub["last_updated"] = datetime.now().isoformat()

        if torch.cuda.is_available():
            alloc = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
            res = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 1)
            sub["vram_allocated_mb"] = alloc
            sub["vram_reserved_mb"] = res
            sub["vram_peak_mb"] = max(sub["vram_peak_mb"], alloc)

        try:
            sub["ram_rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

        self.log_journal(
            severity="INFO",
            subsystem=subsystem_id,
            event_type="STAGE_START",
            message=f"Starting {sub['name']} using {sub['active_model']}.",
            details={"vram_allocated_mb": sub["vram_allocated_mb"], "ram_rss_mb": sub["ram_rss_mb"]}
        )

    def record_stage_end(
        self,
        subsystem_id: str,
        duration_sec: float,
        audio_duration_sec: Optional[float] = None
    ):
        """Records completion metrics for a pipeline stage."""
        if subsystem_id not in self.subsystems:
            return

        sub = self.subsystems[subsystem_id]
        sub["state"] = "loaded"
        sub["last_runtime_sec"] = round(duration_sec, 2)
        if audio_duration_sec and duration_sec > 0:
            sub["rtfx"] = round(audio_duration_sec / duration_sec, 2)
        sub["last_updated"] = datetime.now().isoformat()

        if torch.cuda.is_available():
            alloc = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
            res = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 1)
            sub["vram_allocated_mb"] = alloc
            sub["vram_reserved_mb"] = res
            sub["vram_peak_mb"] = max(sub["vram_peak_mb"], alloc)

        try:
            sub["ram_rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

        sub.pop("start_time", None)

        self.log_journal(
            severity="INFO",
            subsystem=subsystem_id,
            event_type="STAGE_COMPLETE",
            message=f"Completed {sub['name']} in {sub['last_runtime_sec']}s (RTFx: {sub['rtfx']}x).",
            details={
                "runtime_sec": sub["last_runtime_sec"],
                "rtfx": sub["rtfx"],
                "peak_vram_mb": sub["vram_peak_mb"]
            }
        )

    def record_stage_unload(self, subsystem_id: str, reclaimed_vram_mb: float = 0.0, reclaimed_ram_mb: float = 0.0):
        """Marks a pipeline subsystem as evicted/unloaded and logs memory delta."""
        if subsystem_id not in self.subsystems:
            return

        sub = self.subsystems[subsystem_id]
        sub["state"] = "evicted"
        sub["vram_allocated_mb"] = 0.0
        sub.pop("start_time", None)
        if torch.cuda.is_available():
            sub["vram_reserved_mb"] = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 1)
        sub["last_updated"] = datetime.now().isoformat()

        try:
            sub["ram_rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

        self.log_journal(
            severity="INFO",
            subsystem=subsystem_id,
            event_type="MODEL_EVICTED",
            message=f"Unloaded {sub['name']}. Reclaimed {round(reclaimed_vram_mb, 1)} MB VRAM.",
            details={
                "reclaimed_vram_mb": round(reclaimed_vram_mb, 1),
                "reclaimed_ram_mb": round(reclaimed_ram_mb, 1),
                "current_ram_mb": sub["ram_rss_mb"]
            }
        )

    def finalize_pipeline(self):
        """
        Ensures all running or loaded pipeline stages are cleanly transitioned to evicted
        upon pipeline completion, user stop, or error.
        """
        now = datetime.now().isoformat()
        for sub_id, sub in self.subsystems.items():
            if sub.get("category") == "pipeline_stage" and sub.get("state") in ("running", "loaded"):
                sub["state"] = "evicted"
                sub["vram_allocated_mb"] = 0.0
                sub.pop("start_time", None)
                sub["last_updated"] = now

    def sample_telemetry(self) -> Dict[str, Any]:
        """
        Samples real-time VRAM velocity and evaluates predictive emergency interventions.
        Returns live telemetry summary for the supervisor UI.
        """
        now = time.time()
        vram_stats = self.vram_manager.get_stats()
        allocated_mb = round(vram_stats.get("allocated_gb", 0.0) * 1024, 1)
        reserved_mb = round(vram_stats.get("reserved_gb", 0.0) * 1024, 1)
        total_mb = round(vram_stats.get("total_gb", 0.0) * 1024, 1)
        free_mb = max(0.0, total_mb - reserved_mb)
        proc_ram_mb = round(vram_stats.get("proc_ram_used_gb", 0.0) * 1024, 1)

        # Update server subsystem memory
        if "web_server" in self.subsystems:
            self.subsystems["web_server"]["ram_rss_mb"] = proc_ram_mb
            self.subsystems["web_server"]["last_updated"] = datetime.now().isoformat()

        # Update any currently running subsystems in real-time
        for sub_id, sub in self.subsystems.items():
            if sub.get("state") == "running":
                sub["vram_allocated_mb"] = allocated_mb
                sub["vram_reserved_mb"] = reserved_mb
                sub["vram_peak_mb"] = max(sub.get("vram_peak_mb", 0.0), allocated_mb, reserved_mb)
                sub["ram_rss_mb"] = proc_ram_mb
                if "start_time" in sub and sub["start_time"] > 0:
                    sub["last_runtime_sec"] = round(now - sub["start_time"], 1)
                sub["last_updated"] = datetime.now().isoformat()

        # Track velocity
        if self.vram_history:
            prev_t, prev_alloc, prev_res = self.vram_history[-1]
            dt = max(0.001, now - prev_t)
            # Velocity in MB per second
            self.last_velocity_mb_s = round((reserved_mb - prev_res) / dt, 1)

        self.vram_history.append((now, allocated_mb, reserved_mb))

        # Predictive Emergency Evaluation
        governor_threshold_mb = self.config.vram_governor_threshold_gb * 1024
        projected_5s_mb = reserved_mb + max(0.0, self.last_velocity_mb_s * 5.0)

        # Check if emergency action is required
        if self.config.predictive_emergency_enabled:
            # Threshold Level 1: Predictive spike crossing ceiling
            if projected_5s_mb > governor_threshold_mb or reserved_mb > governor_threshold_mb:
                # Level 1 Intervention: Flush PyTorch cache and glibc heap
                self.vram_manager.clear_cache()
                after_stats = self.vram_manager.get_stats()
                after_res_mb = round(after_stats.get("reserved_gb", 0.0) * 1024, 1)

                alert = {
                    "severity": "WARNING" if after_res_mb <= governor_threshold_mb else "EMERGENCY",
                    "title": "Predictive VRAM Ceiling Triggered",
                    "message": (
                        f"Memory usage ({round(reserved_mb / 1024, 2)} GB) or velocity "
                        f"({self.last_velocity_mb_s} MB/s) approached governor ceiling "
                        f"({self.config.vram_governor_threshold_gb} GB). Pre-emptively flushed driver pool."
                    ),
                    "action_taken": "LEVEL_1_FLUSH_POOL",
                    "timestamp": datetime.now().isoformat()
                }
                self.active_emergency_alert = alert
                self.log_journal(
                    severity=alert["severity"],
                    subsystem="governor",
                    event_type="PREDICTIVE_GOVERNOR_INTERVENTION",
                    message=alert["message"],
                    details={"velocity_mb_s": self.last_velocity_mb_s, "reclaimed_mb": round(reserved_mb - after_res_mb, 1)}
                )

                # If still above ceiling after flush, suggest Level 2 auto-throttle
                if after_res_mb > governor_threshold_mb:
                    self.suggested_batch_throttles["whisper"] = 2
                    self.suggested_batch_throttles["conformer"] = 4
                    self.suggested_batch_throttles["parakeet"] = 4
                    alert["action_taken"] = "LEVEL_2_THROTTLE_BATCHES"
                    alert["severity"] = "EMERGENCY"
            else:
                # Reset emergency alert if headroom returned to healthy
                if free_mb > 1500:
                    self.active_emergency_alert = None
                    self.suggested_batch_throttles.clear()

        return {
            "subsystems": list(self.subsystems.values()),
            "governor": {
                "enabled": self.config.predictive_emergency_enabled,
                "ceiling_gb": self.config.vram_governor_threshold_gb,
                "ceiling_mb": governor_threshold_mb,
                "current_reserved_mb": reserved_mb,
                "current_allocated_mb": allocated_mb,
                "free_headroom_mb": round(free_mb, 1),
                "velocity_mb_s": self.last_velocity_mb_s,
                "projected_5s_mb": round(projected_5s_mb, 1),
                "status": "EMERGENCY" if self.active_emergency_alert and self.active_emergency_alert["severity"] == "EMERGENCY" else (
                    "WARNING" if self.active_emergency_alert else "NORMAL"
                )
            },
            "active_alert": self.active_emergency_alert,
            "suggested_throttles": self.suggested_batch_throttles
        }

    def get_suggested_batch_size(self, stage: str, default_size: int) -> int:
        """Returns dynamically throttled batch size if predictive emergency is active."""
        if stage in self.suggested_batch_throttles:
            return min(default_size, self.suggested_batch_throttles[stage])
        return default_size

    def get_journal(
        self,
        limit: int = 50,
        severity: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Returns filtered journal entries in reverse chronological order."""
        events = list(self.recent_events)
        if severity and severity.upper() != "ALL":
            events = [e for e in events if e.get("severity") == severity.upper()]
        if search:
            q = search.lower()
            events = [
                e for e in events
                if q in e.get("message", "").lower() or q in e.get("subsystem", "").lower() or q in e.get("event_type", "").lower()
            ]
        events.reverse()
        return events[:limit]

    def export_journal(self, export_format: str = "json") -> str:
        """Exports the journal entries as either a JSON or CSV string."""
        events = list(self.recent_events)
        events.reverse()

        if export_format.lower() == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Timestamp", "Severity", "Subsystem", "Event Type", "Message", "Details"])
            for e in events:
                writer.writerow([
                    e.get("timestamp", ""),
                    e.get("severity", ""),
                    e.get("subsystem", ""),
                    e.get("event_type", ""),
                    e.get("message", ""),
                    json.dumps(e.get("details", {}))
                ])
            return output.getvalue()
        else:
            return json.dumps(events, indent=2)

    def clear_journal(self):
        """Truncates journal log file and clears in-memory buffer."""
        self.recent_events.clear()
        try:
            if self.journal_file.exists():
                self.journal_file.write_text("", encoding="utf-8")
        except Exception as e:
            print(f"[Supervisor Error] Failed to clear journal file: {e}")

        self.log_journal(
            severity="INFO",
            subsystem="supervisor",
            event_type="JOURNAL_CLEARED",
            message="Journal log cleared by user."
        )

    def get_detailed_storage_breakdown(self) -> Dict[str, Any]:
        """
        Inspects on-disk caches for models, audio buffers, output exports, and compiler kernels.
        """
        breakdown: Dict[str, Any] = {
            "targets": [],
            "total_bytes": 0,
            "total_gb": 0.0
        }

        paths_to_inspect = [
            {
                "id": "hf_cache",
                "name": "Hugging Face Model Hub",
                "path": Path.home() / ".cache" / "huggingface" / "hub",
                "category": "models",
                "can_purge": False,
                "hint": "Cached Hugging Face weights (Canary-Qwen, Whisper, Conformer)"
            },
            {
                "id": "nemo_cache",
                "name": "NeMo Checkpoints Cache",
                "path": Path.home() / ".cache" / "torch" / "NeMo",
                "category": "models",
                "can_purge": False,
                "hint": "NeMo .nemo archive checkpoints (TitaNet, Parakeet)"
            },
            {
                "id": "pyannote_cache",
                "name": "PyAnnote Model Cache",
                "path": Path.home() / ".cache" / "torch" / "pyannote",
                "category": "models",
                "can_purge": True,
                "hint": "PyAnnote segmentation and diarization pipeline weights"
            },
            {
                "id": "temp_audio",
                "name": "Temporary Audio & Chunks",
                "path": self.config.upload_dir,
                "category": "temp",
                "can_purge": True,
                "hint": "Temporary audio waveforms, converted AAC files, and resampled chunks"
            },
            {
                "id": "output_exports",
                "name": "Exported Transcripts & Artifacts",
                "path": self.config.output_dir,
                "category": "exports",
                "can_purge": True,
                "hint": "JSON, TXT, SRT, and VTT exported transcript files"
            },
            {
                "id": "kernel_cache",
                "name": "PyTorch Inductor & Triton Kernels",
                "path": Path.home() / ".cache" / "torch" / "inductor",
                "category": "cache",
                "can_purge": True,
                "hint": "Compiled GPU kernels and autotuned Triton heuristics"
            }
        ]

        total_bytes = 0
        for item in paths_to_inspect:
            target_path = item["path"]
            item_bytes = 0
            file_count = 0
            if target_path.exists():
                try:
                    for root, _, files in os.walk(target_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                sz = os.path.getsize(fp)
                                item_bytes += sz
                                file_count += 1
                            except (OSError, FileNotFoundError):
                                pass
                except Exception:
                    pass

            total_bytes += item_bytes
            breakdown["targets"].append({
                "id": item["id"],
                "name": item["name"],
                "path": str(target_path),
                "category": item["category"],
                "size_bytes": item_bytes,
                "size_mb": round(item_bytes / (1024 * 1024), 1),
                "size_gb": round(item_bytes / (1024 ** 3), 2),
                "file_count": file_count,
                "can_purge": item["can_purge"],
                "hint": item["hint"]
            })

        breakdown["total_bytes"] = total_bytes
        breakdown["total_gb"] = round(total_bytes / (1024 ** 3), 2)
        return breakdown

    def purge_storage(self, target_id: str) -> Dict[str, Any]:
        """
        Safely purges specific cache or temp directory targets to reclaim SSD space.
        """
        import shutil
        reclaimed_bytes = 0
        files_deleted = 0
        target_name = target_id

        target_map = {
            "temp_audio": [self.config.upload_dir],
            "output_exports": [self.config.output_dir],
            "kernel_cache": [
                Path.home() / ".cache" / "torch" / "inductor",
                Path.home() / ".triton" / "cache"
            ],
            "pyannote_cache": [Path.home() / ".cache" / "torch" / "pyannote"]
        }

        if target_id not in target_map and target_id != "all_temp":
            return {"success": False, "message": f"Purge target '{target_id}' is guarded or unknown."}

        dirs_to_clean = (
            [self.config.upload_dir, Path.home() / ".cache" / "torch" / "inductor"]
            if target_id == "all_temp"
            else target_map[target_id]
        )

        for p in dirs_to_clean:
            if p.exists():
                for item in p.iterdir():
                    try:
                        if item.is_file() or item.is_symlink():
                            sz = item.stat().st_size
                            item.unlink()
                            reclaimed_bytes += sz
                            files_deleted += 1
                        elif item.is_dir():
                            for root, _, files in os.walk(item):
                                for f in files:
                                    try:
                                        reclaimed_bytes += os.path.getsize(os.path.join(root, f))
                                        files_deleted += 1
                                    except Exception:
                                        pass
                            shutil.rmtree(item)
                    except Exception as e:
                        print(f"[Supervisor Purge Warning] Could not delete {item}: {e}")

        reclaimed_mb = round(reclaimed_bytes / (1024 * 1024), 1)
        self.log_journal(
            severity="INFO",
            subsystem="storage",
            event_type="STORAGE_PURGED",
            message=f"Purged {target_name}. Reclaimed {reclaimed_mb} MB across {files_deleted} files.",
            details={"reclaimed_bytes": reclaimed_bytes, "files_deleted": files_deleted}
        )

        return {
            "success": True,
            "target": target_id,
            "reclaimed_bytes": reclaimed_bytes,
            "reclaimed_mb": reclaimed_mb,
            "files_deleted": files_deleted,
            "message": f"Successfully cleaned {target_id}. Reclaimed {reclaimed_mb} MB ({files_deleted} files)."
        }


def get_subsystem_supervisor() -> SubsystemSupervisor:
    """Returns the global singleton SubsystemSupervisor instance."""
    return SubsystemSupervisor()

