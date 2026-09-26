"""
FastAPI Backend Application for Transcript Suite.
"""

import os
from pathlib import Path
import uuid
import asyncio
import io
import csv
import json
import time
import gc
import torch
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Response, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
import tempfile
from ..config import config
from ..pipeline import TranscriptionPipeline, get_active_pipeline
from ..audio.loader import AudioLoader
from ..asr.memory import VRAMManager, get_subsystem_supervisor, format_memory_audit_text, purge_page_cache
from ..asr.model_manager import model_manager
from contextlib import asynccontextmanager
from ..diarization.pyannote import verify_pyannote_access, auto_verify_on_startup
from ..export import TranscriptExporter
from ..audio.spectrogram import generate_spectrogram_image
from ..batch.manager import get_batch_manager, BatchItem
from ..checkpointing.manager import get_checkpoint_manager
from ..storage.retention import get_retention_manager
from ..workspace.backup import create_workspace_archive, get_workspace_summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warms up background caches and verifies Hugging Face credentials on startup."""
    auto_verify_on_startup()
    yield


app = FastAPI(title="Transcript Suite API", lifespan=lifespan)


# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

# In-memory task registry and control events
import threading

TASKS: Dict[str, Dict[str, Any]] = {}
TASK_CONTROLS: Dict[str, Dict[str, Any]] = {}
vram_manager = VRAMManager()

# Global Telemetry & Execution Log Ring Buffers
GLOBAL_LOGS: List[Dict[str, Any]] = []
GLOBAL_TRACE: List[Dict[str, Any]] = []
MAX_HISTORY = 2000


def add_log(
    task_id: Optional[str],
    level: str,
    message: str,
    stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    now = datetime.now()
    stats = stats or vram_manager.get_stats()
    entry = {
        "timestamp": now.isoformat(),
        "time_str": now.strftime("%H:%M:%S"),
        "task_id": task_id or "system",
        "level": level.upper(),
        "message": message,
        "ram_used_gb": stats.get("sys_ram_used_gb", 0.0),
        "ram_total_gb": stats.get("sys_ram_total_gb", 0.0),
        "ram_pct": stats.get("sys_ram_percent", 0.0),
        "vram_alloc_gb": stats.get("allocated_gb", 0.0),
        "vram_reserved_gb": stats.get("reserved_gb", 0.0),
        "vram_total_gb": stats.get("total_gb", 0.0),
        "vram_pct": stats.get("percent_used", 0.0),
    }
    GLOBAL_LOGS.append(entry)
    if len(GLOBAL_LOGS) > MAX_HISTORY:
        GLOBAL_LOGS.pop(0)
    if task_id and task_id in TASKS:
        TASKS[task_id].setdefault("logs", []).append(entry)
    return entry


def add_trace_sample(task_id: Optional[str] = None) -> Dict[str, Any]:
    now = datetime.now()
    stats = vram_manager.get_stats()
    active_task = TASKS.get(task_id) if task_id else None
    if not active_task:
        for tid, t in TASKS.items():
            if t.get("status") in ("processing", "queued", "paused"):
                active_task = t
                task_id = tid
                break

    task_name = active_task.get("filename", "System Idle") if active_task else "System Idle"
    task_status = active_task.get("status", "idle") if active_task else "idle"
    stage = active_task.get("message", "Ready") if active_task else "Ready"
    start_time = active_task.get("start_ts") if active_task else None
    elapsed = round(now.timestamp() - start_time, 1) if start_time else 0.0

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    elapsed_str = f"{mins:02d}:{secs:02d}"

    proc_ram = stats.get("proc_ram_used_gb", 0.0)
    sys_ram = stats.get("sys_ram_used_gb", 0.0)
    sys_ram_pct = stats.get("sys_ram_percent", 0.0)
    sys_without_suite = stats.get("sys_ram_without_suite_gb", max(0.0, round(sys_ram - proc_ram, 2)))

    entry = {
        "timestamp": now.isoformat(),
        "time_str": now.strftime("%H:%M:%S"),
        "elapsed_s": elapsed,
        "elapsed_str": elapsed_str,
        "task_id": task_id or "idle",
        "task_name": task_name,
        "status": task_status,
        "stage": stage,
        "proc_ram_used_gb": proc_ram,
        "app_ram_gb": proc_ram,
        "ram_used_gb": sys_ram,
        "sys_ram_used_gb": sys_ram,
        "sys_ram_without_suite_gb": sys_without_suite,
        "ram_total_gb": stats.get("sys_ram_total_gb", 0.0),
        "sys_ram_total_gb": stats.get("sys_ram_total_gb", 0.0),
        "ram_pct": sys_ram_pct,
        "sys_ram_pct": sys_ram_pct,
        "vram_alloc_gb": stats.get("allocated_gb", 0.0),
        "vram_reserved_gb": stats.get("reserved_gb", 0.0),
        "vram_total_gb": stats.get("total_gb", 0.0),
        "vram_pct": stats.get("percent_used", 0.0),
    }
    GLOBAL_TRACE.append(entry)
    if len(GLOBAL_TRACE) > MAX_HISTORY:
        GLOBAL_TRACE.pop(0)
    if task_id and task_id in TASKS:
        TASKS[task_id].setdefault("trace", []).append(entry)
    return entry


def _telemetry_sampler_loop():
    while True:
        try:
            add_trace_sample()
        except Exception:
            pass
        time.sleep(1.0)


_telemetry_thread = threading.Thread(target=_telemetry_sampler_loop, daemon=True)
_telemetry_thread.start()

add_log(None, "INFO", "Transcript Suite Web Server initialized with GPU acceleration.")


class ExportRequest(BaseModel):
    segments: list[dict]
    speaker_aliases: Optional[dict[str, str]] = None
    include_timestamps: bool = True
    include_speakers: bool = True


@app.get("/")
async def index():
    """Serves the main application SPA."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return PlainTextResponse("Transcript Suite Web UI not found.")


def get_path_size(p: Path) -> int:
    """Calculates disk usage of a path safely in bytes."""
    if not p.exists():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for item in p.rglob("*"):
            if item.is_file() and not item.is_symlink():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


def get_cache_breakdown() -> Dict[str, Any]:
    """Inspects RAM, VRAM, and on-disk model/temp cache footprints."""
    mem_stats = vram_manager.get_stats()
    
    hf_models = []
    hf_total = 0
    seen_hf = set()
    for hf_dir in model_manager.hf_cache_dirs:
        if hf_dir.exists():
            for d in hf_dir.iterdir():
                if d.is_dir() and d.name.startswith("models--"):
                    clean_name = d.name.replace("models--", "").replace("--", "/")
                    if clean_name not in seen_hf:
                        seen_hf.add(clean_name)
                        sz = get_path_size(d)
                        hf_total += sz
                        if sz > 5 * 1024 * 1024:
                            hf_models.append({"name": clean_name, "bytes": sz, "mb": round(sz / (1024 * 1024), 1)})
    hf_models.sort(key=lambda x: x["bytes"], reverse=True)
        
    nemo_models = []
    nemo_total = 0
    seen_nemo = set()
    for nemo_dir in model_manager.nemo_cache_dirs:
        if nemo_dir.exists():
            for d in nemo_dir.rglob("*.nemo"):
                if d.is_file() and d.name not in seen_nemo:
                    seen_nemo.add(d.name)
                    sz = d.stat().st_size
                    nemo_total += sz
                    if sz > 1 * 1024 * 1024:
                        nemo_models.append({"name": d.name, "bytes": sz, "mb": round(sz / (1024 * 1024), 1)})
    nemo_models.sort(key=lambda x: x["bytes"], reverse=True)
        
    temp_audio_bytes = 0
    for t_dir in [config.tmp_dir, Path("/tmp")]:
        if t_dir.exists():
            for f in t_dir.glob("*"):
                try:
                    if f.is_file() and (f.suffix.lower() in [".wav", ".aac", ".mp3", ".m4a", ".flac"] or "transcript_suite" in f.name):
                        temp_audio_bytes += f.stat().st_size
                    elif f.is_dir() and "transcript_suite" in f.name:
                        temp_audio_bytes += get_path_size(f)
                except OSError:
                    pass
    upload_bytes = get_path_size(config.upload_dir)
    total_temp = temp_audio_bytes + upload_bytes
    total_disk = hf_total + nemo_total + total_temp

    return {
        "memory": {
            "vram_allocated_mb": round(mem_stats.get("allocated_gb", 0.0) * 1024, 1),
            "vram_reserved_mb": round(mem_stats.get("reserved_gb", 0.0) * 1024, 1),
            "vram_total_mb": round(mem_stats.get("total_gb", 0.0) * 1024, 1),
            "app_ram_rss_mb": round(mem_stats.get("app_ram_rss_gb", 0.0) * 1024, 1),
            "sys_ram_used_gb": mem_stats.get("sys_ram_used_gb", 0.0),
            "sys_ram_total_gb": mem_stats.get("sys_ram_total_gb", 0.0),
        },
        "storage": {
            "hf_total_bytes": hf_total,
            "hf_total_mb": round(hf_total / (1024 * 1024), 1),
            "hf_total_gb": round(hf_total / (1024 * 1024 * 1024), 2),
            "hf_models": hf_models,
            "nemo_total_bytes": nemo_total,
            "nemo_total_mb": round(nemo_total / (1024 * 1024), 1),
            "nemo_total_gb": round(nemo_total / (1024 * 1024 * 1024), 2),
            "nemo_models": nemo_models,
            "temp_audio_bytes": total_temp,
            "temp_audio_mb": round(total_temp / (1024 * 1024), 1),
            "total_disk_bytes": total_disk,
            "total_disk_gb": round(total_disk / (1024 * 1024 * 1024), 2),
        }
    }


@app.get("/api/vram")
async def get_vram():
    """Returns real-time GPU VRAM and System RAM telemetry."""
    return vram_manager.get_stats()


@app.get("/api/cache/stats")
async def get_cache_stats_endpoint():
    """Returns granular memory and on-disk storage breakdown."""
    return get_cache_breakdown()


@app.post("/api/cache/clear")
async def clear_cache_endpoint(request: Request):
    """
    Granular cache clear endpoint:
    - target: 'vram' | 'ram' | 'temp_audio' | 'all'
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    target = data.get("target", "all") if isinstance(data, dict) else "all"
    import ctypes
    
    cleared = []
    try:
        if target in ("vram", "all"):
            vram_manager.clear_cache()
            if torch.cuda.is_available():
                try:
                    torch.cuda.ipc_collect()
                except Exception:
                    pass
            cleared.append("GPU VRAM Cache")
            
        if target in ("ram", "all"):
            gc.collect()
            gc.collect()
            try:
                libc = ctypes.CDLL("libc.so.6")
                libc.malloc_trim(0)
            except Exception:
                pass
            cleared.append("Process Heap (RAM)")
            
        if target in ("temp_audio", "all"):
            # 1. Clean stale /tmp audio files safely
            temp_dir = Path("/tmp")
            if temp_dir.exists():
                for f in temp_dir.glob("*"):
                    try:
                        if "transcript_suite" in f.name or (f.is_file() and f.suffix.lower() in [".wav", ".aac", ".mp3", ".flac"]):
                            if time.time() - f.stat().st_mtime > 10:
                                if f.is_file():
                                    f.unlink()
                                elif f.is_dir():
                                    import shutil
                                    shutil.rmtree(f, ignore_errors=True)
                    except Exception:
                        pass

            # 2. Clean stale uploads and chunks in config.upload_dir (preserving only currently active tasks)
            active_tids = {tid for tid, t in TASKS.items() if t.get("status") in ("processing", "queued", "paused")}
            if config.upload_dir.exists():
                for item in config.upload_dir.iterdir():
                    try:
                        is_active = any(item.name.startswith(tid) for tid in active_tids)
                        if not is_active:
                            if item.is_file():
                                item.unlink()
                            elif item.is_dir():
                                import shutil
                                shutil.rmtree(item, ignore_errors=True)
                    except Exception:
                        pass
            cleared.append("Temporary Audio & Chunks")
            
        stats = vram_manager.get_stats()
        breakdown = get_cache_breakdown()
        add_log(None, "MEM", f"Cache cleared ({', '.join(cleared)}). Free VRAM: {stats.get('free_gb', 0)} GB, App RAM: {stats.get('app_ram_rss_gb', 0)} GB.", stats)
        add_trace_sample()
        return {"status": "cleared", "targets": cleared, "stats": stats, "breakdown": breakdown}
    except Exception as exc:
        add_log(None, "ERROR", f"Memory flush encountered error: {exc}")
        return {"status": "error", "message": str(exc), "targets": cleared}


@app.post("/api/memory/clear")
async def clear_system_memory(request: Request):
    """Backwards-compatible endpoint for proactive memory cleanup."""
    return await clear_cache_endpoint(request)


@app.post("/api/memory/drop-cache")
async def drop_page_cache_endpoint():
    """Purges Linux page cache (model checkpoints, audio buffers) and trims glibc heap."""
    res = purge_page_cache()
    supervisor = get_subsystem_supervisor()
    supervisor.log_journal(
        severity="INFO",
        subsystem="audio_buffers",
        event_type="PAGE_CACHE_PURGE",
        message=f"Page cache purged. Freed {res.get('freed_cached_mb', 0)} MB cached pages ({res.get('files_purged', 0)} files).",
        details=res
    )
    return res


@app.get("/api/telemetry/deep-memory")
async def get_deep_memory():
    """Returns deep system process RAM, suite memory sections, and granular GPU breakdown."""
    return vram_manager.get_deep_memory_trace()


@app.get("/api/telemetry/deep-memory/export")
async def export_deep_memory_trace(format: str = Query("txt")):
    """Exports complete process list and memory breakdown as formatted text or JSON."""
    trace = vram_manager.get_deep_memory_trace()
    if format.lower() == "json":
        return trace
    text = format_memory_audit_text(trace)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@app.get("/api/models")
async def get_models_overview():
    """Returns active council roster, discovered local checkpoints, curated presets, and download state."""
    checkpoints = model_manager.list_installed_checkpoints()
    total_bytes = sum(cp["size_bytes"] for cp in checkpoints)
    return {
        "roster": model_manager.get_active_roster(),
        "checkpoints": checkpoints,
        "presets": model_manager.get_preset_catalog(),
        "total_checkpoint_bytes": total_bytes,
        "total_checkpoint_gb": round(total_bytes / (1024 ** 3), 2),
        "install_status": model_manager.get_download_status()
    }


@app.post("/api/models/roster")
async def update_roster(request: Request):
    """Updates the active multi-model council roster and persists to settings.json."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid roster payload")
    
    updated = model_manager.update_active_roster(payload)
    add_log(None, "CONFIG", f"Council roster updated: Speech-LLM='{updated.get('model_name')}', Whisper='{updated.get('whisper_model')}', CTC='{updated.get('conformer_model')}', TDT='{updated.get('parakeet_model')}', Boost='{updated.get('vocal_boost_level')}'")
    return {"status": "updated", "roster": updated}


@app.post("/api/models/install")
async def install_model(request: Request):
    """Starts or queues background download of a Hugging Face or NeMo model checkpoint."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    model_id = payload.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    framework = payload.get("framework", "huggingface")
    role = payload.get("role")

    result = model_manager.start_download_task(model_id=model_id, framework=framework, role=role)
    add_log(None, "DOWNLOAD", result.get("message", f"Initiated install of '{model_id}' ({framework})."))
    return result


@app.post("/api/models/install/cancel")
async def cancel_model_install(request: Request):
    """Cancels active model download or removes an item from the download queue."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    model_id = payload.get("model_id")
    result = model_manager.cancel_download(model_id=model_id)
    add_log(None, "DOWNLOAD", result.get("message", f"Cancelled download for {model_id}"))
    return result


@app.get("/api/models/install/status")
async def get_model_install_status():
    """Polls background model download status and queued items."""
    return model_manager.get_download_status()


@app.delete("/api/models/checkpoints")
async def delete_model_checkpoint(request: Request):
    """Deletes an installed model checkpoint directory or .nemo file from disk."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    checkpoint_id = payload.get("id") or payload.get("path")
    if not checkpoint_id:
        raise HTTPException(status_code=400, detail="Checkpoint ID or path is required")

    try:
        res = model_manager.delete_checkpoint(checkpoint_id)
        add_log(None, "MEM", f"Deleted model checkpoint '{checkpoint_id}'. Reclaimed {res.get('reclaimed_gb', 0)} GB disk.")
        return res
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --- PyAnnote & Supervisor Endpoints ---

@app.get("/api/pyannote/status")
async def get_pyannote_status():
    """Returns PyAnnote installation status and Hugging Face token verification details."""
    tok = getattr(config, "hf_token", None) or os.getenv("HF_TOKEN")
    return verify_pyannote_access(tok)


@app.post("/api/pyannote/token")
async def configure_pyannote_token(request: Request):
    """Saves Hugging Face token persistently and tests access against gated PyAnnote models."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    raw_tok = payload.get("token")
    token = raw_tok.strip() if isinstance(raw_tok, str) and raw_tok.strip() else None

    # Save to persistent settings (settings.json) and config
    config.save_persistent_settings({"hf_token": token})
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token
        try:
            hf_cache_dir = Path.home() / ".cache" / "huggingface"
            hf_cache_dir.mkdir(parents=True, exist_ok=True)
            (hf_cache_dir / "token").write_text(token, encoding="utf-8")
        except Exception:
            pass
    else:
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
        try:
            hf_cache_token = Path.home() / ".cache" / "huggingface" / "token"
            if hf_cache_token.exists():
                hf_cache_token.unlink()
        except Exception:
            pass

    status = verify_pyannote_access(token, force_refresh=True)
    supervisor = get_subsystem_supervisor()
    supervisor.log_journal(
        severity="INFO" if status.get("ready") else ("WARNING" if status.get("token_provided") else "INFO"),
        subsystem="diarizer",
        event_type="PYANNOTE_TOKEN_UPDATE",
        message=f"PyAnnote token updated. Status: {status.get('message')}",
        details={"ready": status.get("ready"), "username": status.get("username"), "token_provided": bool(token)}
    )
    return status


@app.get("/api/supervisor/subsystems")
async def get_supervisor_subsystems():
    """Returns real-time internal subsystem status, VRAM footprints, and governor metrics."""
    supervisor = get_subsystem_supervisor()
    return supervisor.sample_telemetry()


@app.post("/api/supervisor/unload")
async def supervisor_force_unload(request: Request):
    """Forces immediate eviction of a specific model stage to free VRAM."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    stage = payload.get("stage", "all")
    pipeline = get_active_pipeline()
    supervisor = get_subsystem_supervisor()

    if pipeline:
        res = pipeline.force_eject(stage)
    else:
        vram_manager.clear_cache()
        stats = vram_manager.get_stats()
        res = {
            "success": True,
            "ejected_models": [stage],
            "current_vram_gb": stats.get("reserved_gb", 0.0),
            "free_vram_gb": stats.get("free_gb", 0.0),
            "app_ram_gb": stats.get("proc_ram_used_gb", 0.0)
        }

    supervisor.log_journal(
        severity="INFO",
        subsystem="supervisor",
        event_type="MANUAL_FORCE_UNLOAD",
        message=f"Manual force unload executed for stage '{stage}'.",
        details=res
    )
    return res


@app.post("/api/supervisor/abort")
async def supervisor_emergency_abort():
    """Emergency aborts any active transcription task and clears memory."""
    supervisor = get_subsystem_supervisor()
    aborted_tasks = []
    for tid, ctrl in TASK_CONTROLS.items():
        if "stop_event" in ctrl and not ctrl["stop_event"].is_set():
            ctrl["stop_event"].set()
            aborted_tasks.append(tid)
        if tid in TASKS and TASKS[tid]["status"] in ("running", "queued"):
            TASKS[tid]["status"] = "cancelled"
            TASKS[tid]["error"] = "Emergency aborted by user via Supervisor Control."

    pipeline = get_active_pipeline()
    if pipeline:
        pipeline.force_eject("all")
    else:
        vram_manager.clear_cache()

    supervisor.log_journal(
        severity="EMERGENCY",
        subsystem="supervisor",
        event_type="EMERGENCY_ABORT",
        message=f"Emergency abort triggered. Aborted tasks: {aborted_tasks}."
    )
    return {"success": True, "aborted_tasks": aborted_tasks, "message": "Emergency abort signal dispatched."}


@app.post("/api/supervisor/governor")
async def update_supervisor_governor(request: Request):
    """Updates governor safety ceiling threshold and predictive throttling state."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    ceiling = payload.get("ceiling_gb")
    enabled = payload.get("enabled")
    updates = {}
    if ceiling is not None:
        updates["vram_governor_threshold_gb"] = float(ceiling)
    if enabled is not None:
        updates["predictive_emergency_enabled"] = bool(enabled)

    saved = config.save_persistent_settings(updates)
    supervisor = get_subsystem_supervisor()
    supervisor.log_journal(
        severity="INFO",
        subsystem="governor",
        event_type="GOVERNOR_SETTINGS_UPDATED",
        message=f"Governor settings updated: ceiling={config.vram_governor_threshold_gb} GB, predictive={config.predictive_emergency_enabled}.",
        details=updates
    )
    return {"status": "updated", "governor": saved}


@app.get("/api/supervisor/journal")
async def get_supervisor_journal(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = Query(None),
    search: Optional[str] = Query(None)
):
    """Returns filtered supervisor audit journal entries."""
    supervisor = get_subsystem_supervisor()
    return {
        "events": supervisor.get_journal(limit=limit, severity=severity, search=search),
        "active_alert": supervisor.active_emergency_alert
    }


@app.get("/api/supervisor/journal/export")
async def export_supervisor_journal(format: str = Query("json")):
    """Exports supervisor journal as downloadable JSON or CSV."""
    supervisor = get_subsystem_supervisor()
    content = supervisor.export_journal(export_format=format)
    media_type = "text/csv" if format.lower() == "csv" else "application/json"
    filename = f"supervisor_journal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/supervisor/journal/clear")
async def clear_supervisor_journal():
    """Clears the supervisor journal history."""
    supervisor = get_subsystem_supervisor()
    supervisor.clear_journal()
    return {"status": "cleared", "message": "Supervisor journal log cleared."}


@app.get("/api/storage/detailed")
async def get_detailed_storage():
    """Returns granular disk consumption across model hub, nemo, temp audio, exports, and kernels."""
    supervisor = get_subsystem_supervisor()
    return supervisor.get_detailed_storage_breakdown()


@app.post("/api/storage/purge")
async def purge_storage_target(request: Request):
    """Purges selected cache or temp storage target."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    target = payload.get("target", "temp_audio")
    supervisor = get_subsystem_supervisor()
    res = supervisor.purge_storage(target)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res


@app.get("/api/settings")
async def get_settings_endpoint():
    """Returns persistent storage settings, directory metrics, and configuration options."""
    info = config.get_storage_info()
    return {
        "storage": info,
        "settings": {
            "base_dir": str(config.base_dir),
            "default_diarizer": config.default_diarizer,
            "vocal_boost_level": config.vocal_boost_level,
            "vram_governor_threshold_gb": config.vram_governor_threshold_gb,
            "predictive_emergency_enabled": config.predictive_emergency_enabled,
            "enable_audex_adjudicator": config.enable_audex_adjudicator,
            "audex_model_id": config.audex_model_id,
            "attention_backend": getattr(config, "attention_backend", "sdpa"),
            "enable_sdpa": getattr(config, "enable_sdpa", True),
            "custom_glossary": getattr(config, "custom_glossary", []),
            "token_configured": bool(config.hf_token)
        }
    }


@app.post("/api/settings")
async def update_settings_endpoint(request: Request):
    """Updates persistent storage base_dir or pipeline settings."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")

    new_base_dir = data.get("base_dir")
    if new_base_dir:
        try:
            config.update_storage_root(new_base_dir)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to update storage root: {e}")

    # Update other allowed settings
    config.save_persistent_settings(data)

    # Sync with active pipeline instance if present
    pipeline = get_active_pipeline()
    if pipeline:
        if "attention_backend" in data:
            pipeline.attention_backend = data["attention_backend"]
            if hasattr(pipeline.transcriber, "attention_backend"):
                pipeline.transcriber.attention_backend = data["attention_backend"]
            if hasattr(pipeline.council, "attention_backend"):
                pipeline.council.attention_backend = data["attention_backend"]
        if "custom_glossary" in data:
            pipeline.custom_glossary = list(data["custom_glossary"])
            if hasattr(pipeline.transcriber, "glossary"):
                pipeline.transcriber.glossary = pipeline.custom_glossary
            if hasattr(pipeline.council, "glossary"):
                pipeline.council.glossary = pipeline.custom_glossary

    supervisor = get_subsystem_supervisor()
    supervisor.log_journal(
        severity="INFO",
        subsystem="web_server",
        event_type="SETTINGS_UPDATED",
        message="Persistent settings updated.",
        details=data
    )
    return {
        "status": "updated",
        "storage": config.get_storage_info(),
        "settings": config.load_persistent_settings()
    }


@app.get("/api/glossary")
async def get_glossary_endpoint():
    """Returns the persistent custom phonetic and domain glossary."""
    terms = config.get_glossary()
    return {
        "glossary": terms,
        "count": len(terms)
    }


@app.post("/api/glossary")
async def update_glossary_endpoint(request: Request):
    """
    Updates or adds terms to the persistent custom glossary.
    Accepts:
      - {"terms": ["Docker", "Kubernetes", "ASR"]}
      - or {"term": "PostgreSQL"}
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")

    terms = []
    if "terms" in data and isinstance(data["terms"], list):
        terms = [str(t).strip() for t in data["terms"] if str(t).strip()]
    elif "term" in data and isinstance(data["term"], str):
        term = data["term"].strip()
        if term:
            terms = [term]

    if not terms:
        raise HTTPException(status_code=400, detail="Must provide 'term' (str) or 'terms' (list of str)")

    updated = config.update_glossary(terms)
    pipeline = get_active_pipeline()
    if pipeline:
        pipeline.custom_glossary = updated
        if hasattr(pipeline.transcriber, "glossary"):
            pipeline.transcriber.glossary = updated
        if hasattr(pipeline.council, "glossary"):
            pipeline.council.glossary = updated

    return {
        "status": "success",
        "glossary": updated,
        "count": len(updated)
    }


@app.delete("/api/glossary")
async def delete_glossary_endpoint(request: Request):
    """
    Deletes specific terms or clears the entire glossary.
    Accepts optional JSON payload:
      - {"term": "Docker"} or {"terms": ["Docker"]} to remove specific term(s)
      - empty body or {"clear_all": true} or query ?all=true to clear entire glossary
    """
    terms_to_remove = []
    clear_all = request.query_params.get("all") == "true"
    query_term = request.query_params.get("term")

    try:
        data = await request.json()
        if isinstance(data, dict):
            if data.get("clear_all"):
                clear_all = True
            elif "terms" in data and isinstance(data["terms"], list):
                terms_to_remove = [str(t).strip().lower() for t in data["terms"] if str(t).strip()]
            elif "term" in data and isinstance(data["term"], str):
                terms_to_remove = [data["term"].strip().lower()]
    except Exception:
        pass

    if query_term:
        terms_to_remove.append(query_term.strip().lower())

    if clear_all or (not terms_to_remove and not query_term and request.method == "DELETE"):
        updated = config.clear_glossary()
    else:
        current = config.get_glossary()
        updated = [t for t in current if t.lower() not in terms_to_remove]
        config.custom_glossary = updated
        config.save_persistent_settings({"custom_glossary": updated})

    pipeline = get_active_pipeline()
    if pipeline:
        pipeline.custom_glossary = updated
        if hasattr(pipeline.transcriber, "glossary"):
            pipeline.transcriber.glossary = updated
        if hasattr(pipeline.council, "glossary"):
            pipeline.council.glossary = updated

    return {
        "status": "success",
        "glossary": updated,
        "count": len(updated)
    }


@app.post("/api/storage/purge-all")
async def purge_all_storage_endpoint():
    """Purges all model caches and temporary scratch files for a clean slate."""
    import shutil
    purged = {}

    # 1. Clean scratch / tmp dir
    if config.tmp_dir.exists():
        count = 0
        for item in config.tmp_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                count += 1
            except Exception:
                pass
        purged["tmp_files"] = count

    # 2. Clean models dir
    if config.models_dir.exists():
        for sub in [config.hf_home, config.nemo_dir, config.torch_home]:
            if sub.exists():
                try:
                    shutil.rmtree(sub, ignore_errors=True)
                    sub.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
        purged["models_cleaned"] = True

    # 3. Clean legacy ~/.cache dirs
    legacy_hf = Path.home() / ".cache" / "huggingface" / "hub"
    legacy_nemo = Path.home() / ".cache" / "torch" / "NeMo"
    if legacy_hf.exists():
        shutil.rmtree(legacy_hf, ignore_errors=True)
    if legacy_nemo.exists():
        shutil.rmtree(legacy_nemo, ignore_errors=True)

    supervisor = get_subsystem_supervisor()
    supervisor.log_journal(
        severity="WARNING",
        subsystem="audio_buffers",
        event_type="STORAGE_ALL_PURGED",
        message="All model caches and scratch directories purged fresh.",
        details=purged
    )
    return {"status": "purged", "storage": config.get_storage_info(), "details": purged}


@app.post("/api/transcribe")
async def create_transcription_task(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    diarizer: str = Form("nemo"),
    speaker_labels: bool = Form(True),
    enable_enhancer: bool = Form(True),
    enable_ambiguity: bool = Form(True),
    enable_council: bool = Form(True),
    council_mode: str = Form("sequential"),
    vocal_boost_level: str = Form("adaptive"),
    enable_lufs: bool = Form(True),
    target_lufs: float = Form(-16.0),
    chunk_overlap: float = Form(0.5),
    enable_dedup: bool = Form(True),
    whisper_model: Optional[str] = Form(None),
    conformer_model: Optional[str] = Form(None),
    parakeet_model: Optional[str] = Form(None),
    model_name: Optional[str] = Form(None),
    hf_token: Optional[str] = Form(None),
    glossary: Optional[str] = Form(None),
    attention_backend: Optional[str] = Form(None)
):
    """
    Uploads an AAC/audio file and starts background transcription with enhancer, ambiguity, and council controls.
    """
    task_id = str(uuid.uuid4())
    file_ext = Path(audio.filename).suffix or ".aac"
    saved_path = config.upload_dir / f"{task_id}{file_ext}"

    # Parse glossary if provided
    parsed_glossary = None
    if glossary:
        try:
            val = json.loads(glossary)
            if isinstance(val, list):
                parsed_glossary = [str(x).strip() for x in val if str(x).strip()]
            elif isinstance(val, str) and val.strip():
                parsed_glossary = [t.strip() for t in val.split(",") if t.strip()]
        except Exception:
            parsed_glossary = [t.strip() for t in glossary.split(",") if t.strip()]

    backend = attention_backend or getattr(config, "attention_backend", "sdpa")

    # Save uploaded file
    with open(saved_path, "wb") as f:
        f.write(await audio.read())

    file_size_mb = round(saved_path.stat().st_size / (1024 * 1024), 2)
    pause_event = threading.Event()
    pause_event.set()  # Not paused by default
    stop_event = threading.Event()

    TASK_CONTROLS[task_id] = {
        "pause_event": pause_event,
        "stop_event": stop_event,
        "pipeline": None
    }

    start_ts = time.time()
    TASKS[task_id] = {
        "id": task_id,
        "filename": audio.filename,
        "file_path": str(saved_path),
        "status": "queued",
        "progress": 0.0,
        "message": "Queued in processing pipeline...",
        "segments": [],
        "full_text": "",
        "vram": vram_manager.get_stats(),
        "start_ts": start_ts,
        "logs": [],
        "trace": []
    }

    add_log(task_id, "INFO", f"Uploaded '{audio.filename}' ({file_size_mb} MB). Task queued for transcription.")
    add_trace_sample(task_id)

    background_tasks.add_task(
        run_transcription_worker,
        task_id=task_id,
        file_path=saved_path,
        diarizer=diarizer,
        speaker_labels=speaker_labels,
        enable_enhancer=enable_enhancer,
        enable_ambiguity=enable_ambiguity,
        enable_council=enable_council,
        council_mode=council_mode,
        vocal_boost_level=vocal_boost_level,
        enable_lufs=enable_lufs,
        target_lufs=target_lufs,
        chunk_overlap=chunk_overlap,
        enable_dedup=enable_dedup,
        whisper_model=whisper_model,
        conformer_model=conformer_model,
        parakeet_model=parakeet_model,
        model_name=model_name,
        hf_token=hf_token,
        glossary=parsed_glossary,
        attention_backend=backend
    )

    return {"task_id": task_id, "status": "queued"}


@app.post("/api/audio/diagnostics")
async def analyze_audio_diagnostics(audio: UploadFile = File(...)):
    """
    Pre-flight Audio Health Diagnostics & Stereo Phase Check (1.1.A & 1.1.B).
    Instantly computes SNR in dB, clipping percentage, mains DC offset,
    stereo phase correlation, and EBU R128 LUFS loudness (1.2.A).
    """
    temp_suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as tf:
        tf.write(await audio.read())
        temp_path = Path(tf.name)

    try:
        loader = AudioLoader(target_sr=config.sample_rate)
        report = loader.diagnose_audio(temp_path)
        report_dict = report.to_dict()
        return {
            "status": "ok",
            "filename": audio.filename,
            "health": report_dict,
            "audio_health": report_dict
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Audio health diagnostic failed: {str(e)}")
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/transcribe/stream")
async def create_streaming_transcription(
    audio: Optional[UploadFile] = File(None),
    audio_path: Optional[str] = Form(None),
    model: str = Form("canary"),
    enable_enhancer: bool = Form(False),
    min_chunk_duration: float = Form(3.0),
    max_chunk_duration: float = Form(12.0),
    speaker_alias: Optional[str] = Form(None),
    glossary: Optional[str] = Form(None),
    attention_backend: Optional[str] = Form(None)
):
    """
    Streams progressive speech transcriptions in real time via Server-Sent Events (SSE).
    Partitions audio dynamically into 5-15s acoustic windows via Silero-VAD.
    """
    if audio is None and not audio_path:
        raise HTTPException(status_code=400, detail="Must provide either 'audio' file upload or 'audio_path'.")

    task_id = str(uuid.uuid4())
    if audio is not None:
        file_ext = Path(audio.filename).suffix or ".aac"
        saved_path = config.upload_dir / f"{task_id}{file_ext}"
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        with open(saved_path, "wb") as f:
            f.write(await audio.read())
        orig_filename = audio.filename
    else:
        saved_path = Path(audio_path).resolve()
        if not saved_path.exists():
            raise HTTPException(status_code=404, detail=f"Specified audio_path not found: {audio_path}")
        orig_filename = saved_path.name

    # Parse glossary if provided
    parsed_glossary = None
    if glossary:
        try:
            val = json.loads(glossary)
            if isinstance(val, list):
                parsed_glossary = [str(x).strip() for x in val if str(x).strip()]
            elif isinstance(val, str) and val.strip():
                parsed_glossary = [t.strip() for t in val.split(",") if t.strip()]
        except Exception:
            parsed_glossary = [t.strip() for t in glossary.split(",") if t.strip()]

    backend = attention_backend or getattr(config, "attention_backend", "sdpa")

    pause_event = threading.Event()
    pause_event.set()
    stop_event = threading.Event()

    TASK_CONTROLS[task_id] = {
        "pause_event": pause_event,
        "stop_event": stop_event,
        "pipeline": None
    }

    start_ts = time.time()
    TASKS[task_id] = {
        "id": task_id,
        "filename": orig_filename,
        "file_path": str(saved_path),
        "status": "streaming",
        "progress": 0.0,
        "message": f"Streaming transcription ({model})...",
        "segments": [],
        "full_text": "",
        "vram": vram_manager.get_stats(),
        "start_ts": start_ts,
        "logs": [],
        "trace": []
    }

    add_log(task_id, "INFO", f"Initiated progressive stream for '{orig_filename}'.")
    add_trace_sample(task_id)

    async def sse_event_generator():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def run_worker():
            try:
                pipeline = get_active_pipeline() or TranscriptionPipeline(
                    attention_backend=backend,
                    custom_glossary=parsed_glossary
                )
                TASK_CONTROLS[task_id]["pipeline"] = pipeline
                for event in pipeline.stream_transcribe(
                    file_path=saved_path,
                    model_choice=model,
                    enable_enhancer=enable_enhancer,
                    min_chunk_duration=min_chunk_duration,
                    max_chunk_duration=max_chunk_duration,
                    speaker_alias=speaker_alias,
                    stop_event=stop_event,
                    glossary=parsed_glossary,
                    attention_backend=backend
                ):
                    ev_type = event.get("event")
                    ev_data = event.get("data", {})
                    if ev_type == "init":
                        TASKS[task_id]["duration"] = ev_data.get("duration", 0.0)
                        TASKS[task_id]["message"] = f"Streaming transcription ({model})..."
                        add_log(task_id, "INFO", f"Stream initialized for '{orig_filename}' ({ev_data.get('duration')}s)")
                    elif ev_type == "chunk_done":
                        seg = {
                            "start": ev_data.get("start"),
                            "end": ev_data.get("end"),
                            "duration": ev_data.get("duration"),
                            "speaker": ev_data.get("speaker", "Speaker 0"),
                            "text": ev_data.get("text", "")
                        }
                        TASKS[task_id]["segments"].append(seg)
                        text_val = ev_data.get("text", "")
                        if text_val:
                            curr_txt = TASKS[task_id].get("full_text", "")
                            TASKS[task_id]["full_text"] = (curr_txt + " " + text_val).strip()
                        add_log(task_id, "INFO", f"Stream chunk {ev_data.get('chunk_idx')} transcribed: {text_val[:40]}...")
                    elif ev_type == "complete":
                        TASKS[task_id]["status"] = "completed"
                        TASKS[task_id]["progress"] = 1.0
                        TASKS[task_id]["message"] = "Completed"
                        add_log(task_id, "INFO", f"Stream completed ({len(TASKS[task_id]['segments'])} chunks).")
                    elif ev_type == "stopped":
                        TASKS[task_id]["status"] = "stopped"
                        TASKS[task_id]["message"] = "Stopped by user"
                        add_log(task_id, "WARN", "Stream transcription stopped by user.")
                    elif ev_type == "error":
                        TASKS[task_id]["status"] = "failed"
                        TASKS[task_id]["message"] = ev_data.get("error", "Error")
                        add_log(task_id, "ERROR", f"Stream failed: {ev_data.get('error')}")

                    asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
            except Exception as exc:
                TASKS[task_id]["status"] = "failed"
                TASKS[task_id]["message"] = str(exc)
                err_ev = {"event": "error", "data": {"error": str(exc)}}
                asyncio.run_coroutine_threadsafe(queue.put(err_ev), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

        yield f"event: task_registered\ndata: {json.dumps({'task_id': task_id})}\n\n"

        while True:
            item = await queue.get()
            if item is None:
                break
            ev_name = item.get("event", "message")
            ev_payload = json.dumps(item.get("data", {}))
            yield f"event: {ev_name}\ndata: {ev_payload}\n\n"

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Task-ID": task_id
        }
    )





@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    """Pauses an in-progress transcription task."""
    ctrl = TASK_CONTROLS.get(task_id)
    if not ctrl:
        raise HTTPException(status_code=404, detail="Task control not found")
    ctrl["pause_event"].clear()
    if task_id in TASKS:
        TASKS[task_id]["status"] = "paused"
        TASKS[task_id]["message"] = "Paused by user"
    add_log(task_id, "WARN", "Transcription paused by user.")
    add_trace_sample(task_id)
    return {"status": "paused"}


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """Resumes a paused transcription task or resumes from an on-disk crash checkpoint."""
    ctrl = TASK_CONTROLS.get(task_id)
    # Case 1: In-memory task that is currently paused
    if ctrl and ctrl.get("pause_event") and not ctrl["pause_event"].is_set():
        stop_evt = ctrl.get("stop_event")
        if not (stop_evt and stop_evt.is_set()):
            ctrl["pause_event"].set()
            if task_id in TASKS:
                TASKS[task_id]["status"] = "processing"
                TASKS[task_id]["message"] = "Resuming..."
            add_log(task_id, "INFO", "Transcription resumed from pause.")
            add_trace_sample(task_id)
            return {"status": "resumed", "mode": "unpause"}

    # Case 2: Resume from on-disk crash checkpoint
    ckpt_mgr = get_checkpoint_manager()
    ckpt = ckpt_mgr.load_checkpoint(task_id)
    if ckpt:
        last_chunk = ckpt.get("last_chunk_index", -1)
        tot_chunks = ckpt.get("total_chunks", 0)
        resume_chunk = last_chunk + 1
        saved_segs = ckpt.get("segments", [])
        params = ckpt.get("params", {})
        file_path_str = ckpt.get("file_path")
        if not file_path_str:
            raise HTTPException(status_code=400, detail="Checkpoint missing audio file path.")
        file_path = Path(file_path_str)
        if not file_path.exists():
            raise HTTPException(status_code=400, detail=f"Source audio file '{file_path}' no longer exists on disk.")

        pause_event = threading.Event()
        pause_event.set()
        stop_event = threading.Event()
        TASK_CONTROLS[task_id] = {
            "pause_event": pause_event,
            "stop_event": stop_event,
            "pipeline": None
        }

        if task_id not in TASKS:
            TASKS[task_id] = {
                "id": task_id,
                "filename": ckpt.get("filename", file_path.name),
                "file_path": str(file_path),
                "status": "processing",
                "progress": round((resume_chunk / max(1, tot_chunks)) * 100, 1),
                "message": f"Resuming from chunk {resume_chunk + 1}/{tot_chunks}...",
                "segments": list(saved_segs),
                "full_text": " ".join(s.get("text", "") for s in saved_segs),
                "vram": vram_manager.get_stats(),
                "start_ts": time.time(),
                "logs": [],
                "trace": []
            }
        else:
            TASKS[task_id]["status"] = "processing"
            TASKS[task_id]["message"] = f"Resuming from chunk {resume_chunk + 1}/{tot_chunks}..."
            TASKS[task_id]["segments"] = list(saved_segs)

        add_log(task_id, "INFO", f"Resuming task from checkpoint at chunk {resume_chunk + 1}/{tot_chunks} ({len(saved_segs)} pre-existing segments).")
        add_trace_sample(task_id)

        worker_thread = threading.Thread(
            target=run_transcription_worker,
            kwargs={
                "task_id": task_id,
                "file_path": file_path,
                "diarizer": params.get("diarizer", "nemo"),
                "speaker_labels": params.get("speaker_labels", True),
                "enable_enhancer": params.get("enable_enhancer", True),
                "enable_ambiguity": params.get("enable_ambiguity", True),
                "enable_council": params.get("enable_council", True),
                "council_mode": params.get("council_mode", "sequential"),
                "vocal_boost_level": params.get("vocal_boost_level", "adaptive"),
                "enable_lufs": params.get("enable_lufs", True),
                "target_lufs": float(params.get("target_lufs", -16.0)),
                "chunk_overlap": float(params.get("chunk_overlap", 0.5)),
                "enable_dedup": params.get("enable_dedup", True),
                "whisper_model": params.get("whisper_model"),
                "conformer_model": params.get("conformer_model"),
                "parakeet_model": params.get("parakeet_model"),
                "model_name": params.get("model_name"),
                "hf_token": params.get("hf_token"),
                "glossary": params.get("glossary"),
                "attention_backend": params.get("attention_backend"),
                "resume_from_chunk": resume_chunk,
                "existing_segments": saved_segs
            },
            daemon=True
        )
        worker_thread.start()
        return {
            "status": "resumed",
            "mode": "checkpoint",
            "resume_chunk": resume_chunk,
            "total_chunks": tot_chunks,
            "existing_segments_count": len(saved_segs)
        }

    raise HTTPException(status_code=404, detail="No active task control or valid checkpoint found to resume.")


@app.get("/api/tasks/checkpoints")
async def list_checkpoints():
    """Lists resumable checkpoints saved on disk."""
    return {"checkpoints": get_checkpoint_manager().list_resumable_checkpoints()}


@app.get("/api/tasks/{task_id}/checkpoint")
async def get_task_checkpoint(task_id: str):
    """Retrieves checkpoint detail for a specific task."""
    ckpt = get_checkpoint_manager().load_checkpoint(task_id)
    if not ckpt:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return ckpt



@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    """Stops and cancels an in-progress transcription task."""
    ctrl = TASK_CONTROLS.get(task_id)
    if not ctrl:
        raise HTTPException(status_code=404, detail="Task control not found")
    ctrl["stop_event"].set()
    ctrl["pause_event"].set()  # Unblock if paused
    if ctrl.get("pipeline") and hasattr(ctrl["pipeline"].transcriber, "unload_model"):
        ctrl["pipeline"].transcriber.unload_model()
    if ctrl.get("pipeline") and hasattr(ctrl["pipeline"], "council") and hasattr(ctrl["pipeline"].council, "unload_members"):
        ctrl["pipeline"].council.unload_members()

    if task_id in TASKS:
        TASKS[task_id]["status"] = "stopped"
        TASKS[task_id]["message"] = "Stopped by user"
    add_log(task_id, "WARN", "Transcription stopped and cancelled by user. VRAM reclaimed.")
    add_trace_sample(task_id)
    return {"status": "stopped"}


@app.delete("/api/tasks/{task_id}/segments/{segment_idx}")
async def delete_segment(task_id: str, segment_idx: int):
    """Deletes a transcript segment from a task's in-memory record."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    segs = TASKS[task_id].get("segments", [])
    if segment_idx < 0 or segment_idx >= len(segs):
        raise HTTPException(status_code=404, detail="Segment index out of range")
    deleted = segs.pop(segment_idx)
    # Recalculate full text
    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    add_log(task_id, "INFO", f"Deleted segment {segment_idx} (Speaker: {deleted.get('speaker')}, '{deleted.get('text', '')[:30]}...')")
    return {"status": "deleted", "remaining_count": len(segs)}


@app.patch("/api/tasks/{task_id}/segments/{segment_idx}")
async def patch_segment(task_id: str, segment_idx: int, payload: Dict[str, Any]):
    """Updates a transcript segment text, speaker alias, review flag, or override."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    segs = TASKS[task_id].get("segments", [])
    if segment_idx < 0 or segment_idx >= len(segs):
        raise HTTPException(status_code=404, detail="Segment index out of range")
    if "text" in payload:
        segs[segment_idx]["text"] = payload["text"]
    if "speaker" in payload:
        segs[segment_idx]["speaker"] = payload["speaker"]
    if "needs_review" in payload:
        segs[segment_idx]["needs_review"] = bool(payload["needs_review"])
    if "edited" in payload:
        segs[segment_idx]["edited"] = bool(payload["edited"])
    if "winning_juror" in payload:
        segs[segment_idx]["winning_juror"] = payload["winning_juror"]
    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    return {"status": "updated", "segment": segs[segment_idx]}


@app.post("/api/tasks/{task_id}/segments/restore")
async def restore_segment(task_id: str, payload: Dict[str, Any]):
    """Restores a deleted segment back into the task at a specified index (for Undo operations)."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    segs = TASKS[task_id].setdefault("segments", [])
    segment_idx = int(payload.get("segment_idx", len(segs)))
    segment_data = payload.get("segment")
    if not segment_data or not isinstance(segment_data, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'segment' payload")
    
    idx = max(0, min(segment_idx, len(segs)))
    segs.insert(idx, segment_data)
    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    add_log(task_id, "INFO", f"Restored segment at index {idx} (Speaker: {segment_data.get('speaker')}, '{segment_data.get('text', '')[:30]}...')")
    return {"status": "restored", "index": idx, "remaining_count": len(segs)}


@app.get("/api/tasks/{task_id}/comparator")
async def get_task_comparator(task_id: str):
    """
    Sub-Phase 4.2.A: Per-Model Full Transcript Comparator Lab endpoint.
    Aggregates full per-model transcripts and comparative scorecards across all council jurors.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = TASKS[task_id]
    segs = task.get("segments", [])
    total_chunks = len(segs)

    # 1. Discover all unique models present across votes
    models_dict: Dict[str, Dict[str, Any]] = {}
    
    for seg in segs:
        council_data = seg.get("council") or {}
        votes = council_data.get("votes") or []
        for v in votes:
            m = v.get("member", "Unknown")
            if m not in models_dict:
                # Friendly display name
                clean_name = m.split("/")[-1].replace("_", " ").title()
                if "canary" in m.lower():
                    clean_name = "Canary-1B"
                elif "whisper" in m.lower():
                    clean_name = "Whisper Large V3"
                elif "conformer" in m.lower():
                    clean_name = "Conformer Ctc"
                elif "parakeet" in m.lower():
                    clean_name = "Parakeet TDT"

                models_dict[m] = {
                    "member": m,
                    "display_name": clean_name,
                    "role": v.get("role", "Juror"),
                    "hypotheses": [],
                    "confidences": [],
                    "win_count": 0,
                    "loop_count": 0
                }

    # Fallback if no multi-model council votes were recorded (single model)
    if not models_dict:
        lead_m = task.get("model_name", "Primary Model")
        clean_name = lead_m.split("/")[-1].replace("_", " ").title()
        models_dict[lead_m] = {
            "member": lead_m,
            "display_name": clean_name,
            "role": "Primary Transcriber",
            "hypotheses": [s.get("text", "") for s in segs],
            "confidences": [0.95] * len(segs),
            "win_count": len(segs),
            "loop_count": 0
        }

    # 2. Build alignments and per-model data
    chunk_alignments = []
    unanimous_chunks = 0
    majority_chunks = 0
    split_chunks = 0
    loop_breaker_chunks = 0

    for idx, seg in enumerate(segs):
        seg_text = seg.get("text", "").strip()
        c_data = seg.get("council") or {}
        agree_type = c_data.get("agreement_type", "UNANIMOUS")
        is_breaker = bool(seg.get("loop_circuit_breaker_tripped") or c_data.get("loop_circuit_breaker_tripped"))

        if is_breaker:
            loop_breaker_chunks += 1
        elif agree_type == "UNANIMOUS":
            unanimous_chunks += 1
        elif agree_type in ("MAJORITY", "CTC_ANCHORED", "AUDEX_ADJUDICATED"):
            majority_chunks += 1
        else:
            split_chunks += 1

        votes_in_seg = c_data.get("votes") or []
        votes_payload = []

        if votes_in_seg:
            for v in votes_in_seg:
                m = v.get("member", "Unknown")
                hyp = v.get("hypothesis", "").strip()
                conf = float(v.get("confidence", 0.85))
                is_win = (hyp.lower() == seg_text.lower()) if hyp and seg_text else False
                
                if m in models_dict:
                    models_dict[m]["hypotheses"].append(hyp)
                    models_dict[m]["confidences"].append(conf)
                    if is_win:
                        models_dict[m]["win_count"] += 1
                    if "loop" in v.get("role", "").lower():
                        models_dict[m]["loop_count"] += 1

                votes_payload.append({
                    "member": m,
                    "role": v.get("role", ""),
                    "hypothesis": hyp,
                    "confidence": round(conf, 3),
                    "is_winner": is_win
                })
        else:
            # Single model fallback alignment
            for m in models_dict:
                votes_payload.append({
                    "member": m,
                    "role": models_dict[m]["role"],
                    "hypothesis": seg_text,
                    "confidence": 0.95,
                    "is_winner": True
                })

        chunk_alignments.append({
            "index": idx,
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "duration": seg.get("duration", 0.0),
            "speaker": seg.get("speaker", "Speaker 0"),
            "consensus_text": seg_text,
            "agreement_type": agree_type,
            "consensus_score": round(float(c_data.get("consensus_score", 1.0)), 3),
            "disputed_tokens": c_data.get("disputed_tokens", []),
            "needs_review": bool(seg.get("needs_review", False)),
            "loop_circuit_breaker_tripped": is_breaker,
            "votes": votes_payload
        })

    # 3. Finalize scorecards
    consensus_full_text = " ".join(s.get("text", "").strip() for s in segs if s.get("text"))
    consensus_words = len(consensus_full_text.split())

    models_scorecards = []
    for m, info in models_dict.items():
        m_text = " ".join(h for h in info["hypotheses"] if h)
        m_words = len(m_text.split())
        avg_c = (sum(info["confidences"]) / len(info["confidences"])) if info["confidences"] else 0.85
        win_rate = (info["win_count"] / total_chunks) if total_chunks > 0 else 1.0

        # Simple token agreement rate against consensus
        c_set = set(consensus_full_text.lower().split())
        m_set = set(m_text.lower().split())
        overlap = len(c_set.intersection(m_set))
        union = len(c_set.union(m_set))
        agreement_score = round(overlap / union, 3) if union > 0 else 1.0

        models_scorecards.append({
            "member": info["member"],
            "display_name": info["display_name"],
            "role": info["role"],
            "full_text": m_text,
            "word_count": m_words,
            "avg_confidence": round(avg_c, 3),
            "win_count": info["win_count"],
            "win_rate": round(win_rate, 3),
            "agreement_score": agreement_score,
            "total_chunks": total_chunks
        })

    return {
        "task_id": task_id,
        "total_chunks": total_chunks,
        "consensus": {
            "full_text": consensus_full_text,
            "word_count": consensus_words,
            "unanimous_chunks": unanimous_chunks,
            "majority_chunks": majority_chunks,
            "split_chunks": split_chunks,
            "loop_breaker_chunks": loop_breaker_chunks,
            "unanimous_rate": round(unanimous_chunks / total_chunks, 3) if total_chunks > 0 else 1.0
        },
        "models": models_scorecards,
        "chunk_alignments": chunk_alignments
    }


@app.post("/api/tasks/{task_id}/normalize")
async def normalize_task_transcript(task_id: str, payload: Dict[str, Any]):
    """
    Sub-Phase 4.4.A: Deterministic Number & Disfluency Normalizer endpoint.
    Applies currency, percentage, number formatting, and disfluency stripping.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    from ..nlp.normalizer import normalize_transcript_text

    scope = payload.get("scope", "all")
    segment_idx = payload.get("segment_idx")
    indices = payload.get("segment_indices")
    if scope == "active" and segment_idx is not None:
        indices = [int(segment_idx)]

    convert_numbers = payload.get("convert_numbers", payload.get("normalize_numbers", True))
    norm_currencies = payload.get("normalize_currencies", convert_numbers)
    norm_percentages = payload.get("normalize_percentages", convert_numbers)
    remove_fillers = payload.get("remove_disfluencies", payload.get("remove_fillers", False))
    custom_fillers = payload.get("custom_fillers")

    segs = TASKS[task_id].get("segments", [])
    total_metrics = {
        "currency_replacements": 0,
        "percent_replacements": 0,
        "number_replacements": 0,
        "fillers_removed": 0,
        "segments_modified": 0
    }

    for idx, seg in enumerate(segs):
        if indices is not None and idx not in indices:
            continue
        orig_text = seg.get("text", "")
        new_text, m = normalize_transcript_text(
            orig_text,
            convert_numbers=convert_numbers,
            normalize_currencies=norm_currencies,
            normalize_percentages=norm_percentages,
            remove_disfluencies=remove_fillers,
            custom_fillers=custom_fillers
        )
        if m["changed"]:
            seg["text"] = new_text
            seg["edited"] = True
            total_metrics["segments_modified"] += 1
            total_metrics["currency_replacements"] += m["currency_replacements"]
            total_metrics["percent_replacements"] += m["percent_replacements"]
            total_metrics["number_replacements"] += m["number_replacements"]
            total_metrics["fillers_removed"] += m["fillers_removed"]

    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    add_log(task_id, "INFO", f"Normalized transcript: {total_metrics['segments_modified']} segments modified, {total_metrics['fillers_removed']} fillers removed.")
    return {
        "status": "ok",
        "action": "normalized",
        "segments": segs,
        "metrics": total_metrics,
        "modified_segments_count": total_metrics["segments_modified"]
    }


@app.post("/api/tasks/{task_id}/segments/merge")
async def merge_segments(task_id: str, payload: Dict[str, Any]):
    """
    Sub-Phase 4.4.B: Merge segment at first_idx with following segment (first_idx + 1).
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    segs = TASKS[task_id].get("segments", [])
    first_idx = int(payload.get("first_idx", -1))
    if first_idx < 0 or first_idx >= len(segs) - 1:
        raise HTTPException(status_code=400, detail="Invalid first_idx for merge; must have a subsequent segment")

    seg1 = segs[first_idx]
    seg2 = segs[first_idx + 1]

    merged_text = f"{seg1.get('text', '').strip()} {seg2.get('text', '').strip()}".strip()
    merged_start = min(seg1.get("start", 0.0), seg2.get("start", 0.0))
    merged_end = max(seg1.get("end", 0.0), seg2.get("end", 0.0))

    merged_seg = {
        "start": round(merged_start, 2),
        "end": round(merged_end, 2),
        "duration": round(merged_end - merged_start, 2),
        "speaker": seg1.get("speaker", "Speaker 0"),
        "text": merged_text,
        "edited": True,
        "needs_review": bool(seg1.get("needs_review") or seg2.get("needs_review")),
        "council": seg1.get("council")
    }

    segs[first_idx] = merged_seg
    segs.pop(first_idx + 1)

    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    add_log(task_id, "INFO", f"Merged segment {first_idx} with {first_idx + 1} into [{merged_start:.1f}s - {merged_end:.1f}s].")
    return {
        "status": "ok",
        "action": "merged",
        "merged_index": first_idx,
        "merged_segment": merged_seg,
        "segment": merged_seg,
        "total_segments": len(segs),
        "remaining_count": len(segs)
    }


@app.post("/api/tasks/{task_id}/segments/split")
async def split_segment(task_id: str, payload: Dict[str, Any]):
    """
    Sub-Phase 4.4.B: Split segment at seg_idx based on character offset or timestamp.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    segs = TASKS[task_id].get("segments", [])
    seg_idx = int(payload.get("seg_idx", -1))
    if seg_idx < 0 or seg_idx >= len(segs):
        raise HTTPException(status_code=400, detail="Invalid seg_idx for split")

    seg = segs[seg_idx]
    text = seg.get("text", "")
    char_offset = payload.get("char_offset")
    split_time = payload.get("split_time")

    if char_offset is not None:
        char_offset = int(char_offset)
        if char_offset <= 0 or char_offset >= len(text):
            raise HTTPException(status_code=400, detail="char_offset must be strictly inside segment text bounds")
        ratio = char_offset / (len(text) if len(text) > 0 else 1)
        computed_split_time = seg.get("start", 0.0) + ratio * (seg.get("end", 0.0) - seg.get("start", 0.0))
        part_a_text = text[:char_offset].strip()
        part_b_text = text[char_offset:].strip()
    elif split_time is not None:
        computed_split_time = float(split_time)
        if computed_split_time <= seg.get("start", 0.0) or computed_split_time >= seg.get("end", 0.0):
            raise HTTPException(status_code=400, detail="split_time must be strictly between segment start and end")
        dur = seg.get("end", 0.0) - seg.get("start", 0.0)
        ratio = (computed_split_time - seg.get("start", 0.0)) / (dur if dur > 0 else 1.0)
        char_pos = max(1, int(ratio * len(text)))
        part_a_text = text[:char_pos].strip()
        part_b_text = text[char_pos:].strip()
    else:
        raise HTTPException(status_code=400, detail="Must supply char_offset or split_time for split")

    computed_split_time = round(computed_split_time, 2)

    seg_a = {
        "start": seg.get("start", 0.0),
        "end": computed_split_time,
        "duration": round(computed_split_time - seg.get("start", 0.0), 2),
        "speaker": seg.get("speaker", "Speaker 0"),
        "text": part_a_text,
        "edited": True,
        "needs_review": seg.get("needs_review", False),
        "council": seg.get("council")
    }

    seg_b = {
        "start": computed_split_time,
        "end": seg.get("end", 0.0),
        "duration": round(seg.get("end", 0.0) - computed_split_time, 2),
        "speaker": seg.get("speaker", "Speaker 0"),
        "text": part_b_text,
        "edited": True,
        "needs_review": seg.get("needs_review", False),
        "council": seg.get("council")
    }

    segs[seg_idx] = seg_a
    segs.insert(seg_idx + 1, seg_b)

    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    add_log(task_id, "INFO", f"Split segment {seg_idx} into [{seg_a['start']}s - {seg_a['end']}s] and [{seg_b['start']}s - {seg_b['end']}s].")
    return {
        "status": "ok",
        "action": "split",
        "first_segment": seg_a,
        "second_segment": seg_b,
        "segment_a": seg_a,
        "segment_b": seg_b,
        "total_segments": len(segs),
        "index_a": seg_idx,
        "index_b": seg_idx + 1,
        "remaining_count": len(segs)
    }


@app.post("/api/tasks/{task_id}/speakers/palette")
async def update_speaker_palette(task_id: str, payload: Dict[str, Any]):
    """
    Sub-Phase 4.4.F: Speaker Palette & Role Avatar Customizer endpoint.
    Updates aliases, color accents, and role icons.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    palette = payload.get("palette", {})
    TASKS[task_id]["speaker_palette"] = palette

    if "aliases" in payload:
        TASKS[task_id]["speaker_aliases"] = payload["aliases"]

    return {"status": "ok", "action": "updated", "palette": palette}


def _launch_next_batch_item():
    """Checks batch queue and launches next item sequentially if idle."""
    batch_mgr = get_batch_manager()
    next_item = batch_mgr.get_next_to_process()
    if not next_item:
        return
    _launch_batch_item(next_item)


def _launch_batch_item(item: BatchItem):
    """Initializes task entry and starts worker thread for a batch item."""
    task_id = str(uuid.uuid4())
    batch_mgr = get_batch_manager()
    batch_mgr.mark_started(item.item_id, task_id)

    file_path = Path(item.file_path)
    opts = item.options or {}

    pause_event = threading.Event()
    pause_event.set()
    stop_event = threading.Event()
    TASK_CONTROLS[task_id] = {
        "pause_event": pause_event,
        "stop_event": stop_event,
        "pipeline": None
    }

    start_ts = time.time()
    TASKS[task_id] = {
        "id": task_id,
        "filename": item.filename,
        "file_path": str(file_path),
        "status": "processing",
        "progress": 0.0,
        "message": f"Processing batch item: {item.filename}...",
        "segments": [],
        "full_text": "",
        "vram": vram_manager.get_stats(),
        "start_ts": start_ts,
        "logs": [],
        "trace": [],
        "batch_item_id": item.item_id
    }

    add_log(task_id, "INFO", f"Batch worker started for item '{item.filename}' ({item.file_size_mb} MB).")
    add_trace_sample(task_id)

    worker_thread = threading.Thread(
        target=run_transcription_worker,
        kwargs={
            "task_id": task_id,
            "file_path": file_path,
            "diarizer": opts.get("diarizer", "nemo"),
            "speaker_labels": opts.get("speaker_labels", True),
            "enable_enhancer": opts.get("enable_enhancer", True),
            "enable_ambiguity": opts.get("enable_ambiguity", True),
            "enable_council": opts.get("enable_council", True),
            "council_mode": opts.get("council_mode", "sequential"),
            "vocal_boost_level": opts.get("vocal_boost_level", "adaptive"),
            "enable_lufs": opts.get("enable_lufs", True),
            "target_lufs": float(opts.get("target_lufs", -16.0)),
            "chunk_overlap": float(opts.get("chunk_overlap", 0.5)),
            "enable_dedup": opts.get("enable_dedup", True),
            "whisper_model": opts.get("whisper_model"),
            "conformer_model": opts.get("conformer_model"),
            "parakeet_model": opts.get("parakeet_model"),
            "model_name": opts.get("model_name"),
            "hf_token": opts.get("hf_token"),
            "glossary": opts.get("glossary"),
            "attention_backend": opts.get("attention_backend"),
            "batch_item_id": item.item_id
        },
        daemon=True
    )
    worker_thread.start()


def run_transcription_worker(
    task_id: str,
    file_path: Path,
    diarizer: str,
    speaker_labels: bool,
    enable_enhancer: bool,
    enable_ambiguity: bool,
    enable_council: bool,
    council_mode: str,
    vocal_boost_level: str = "adaptive",
    enable_lufs: bool = True,
    target_lufs: float = -16.0,
    chunk_overlap: float = 0.5,
    enable_dedup: bool = True,
    whisper_model: Optional[str] = None,
    conformer_model: Optional[str] = None,
    parakeet_model: Optional[str] = None,
    model_name: Optional[str] = None,
    hf_token: Optional[str] = None,
    glossary: Optional[List[str]] = None,
    attention_backend: Optional[str] = None,
    resume_from_chunk: Optional[int] = None,
    existing_segments: Optional[List[Dict[str, Any]]] = None,
    batch_item_id: Optional[str] = None
):
    ckpt_mgr = get_checkpoint_manager()
    batch_mgr = get_batch_manager()
    ctrl = TASK_CONTROLS.get(task_id)
    pause_evt = ctrl["pause_event"] if ctrl else None
    stop_evt = ctrl["stop_event"] if ctrl else None

    # Prepopulate segments in task record if resuming
    if existing_segments and task_id in TASKS:
        TASKS[task_id]["segments"] = list(existing_segments)
        TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in existing_segments)

    pipeline = TranscriptionPipeline(
        diarizer_type=diarizer,
        hf_token=hf_token,
        model_name=model_name,
        whisper_model=whisper_model,
        conformer_model=conformer_model,
        parakeet_model=parakeet_model,
        vocal_boost_level=vocal_boost_level,
        attention_backend=attention_backend,
        custom_glossary=glossary
    )
    if ctrl:
        ctrl["pipeline"] = pipeline

    add_log(task_id, "INFO", f"Pipeline starting: Lead={pipeline.transcriber.model_name.split('/')[-1]}, Whisper={pipeline.council.whisper_model_id.split('/')[-1]}, CTC={pipeline.council.conformer_model_id.split('/')[-1]}, TDT={pipeline.council.parakeet_model_id.split('/')[-1]}, Attn={pipeline.attention_backend}, Boost={pipeline.vocal_boost_level}")

    def on_progress(stage: str, frac: float, current_seg: Optional[Dict[str, Any]]):
        if task_id in TASKS:
            if TASKS[task_id]["status"] != "paused":
                TASKS[task_id]["status"] = "processing"
            TASKS[task_id]["progress"] = round(frac * 100, 1)
            TASKS[task_id]["message"] = stage
            if current_seg:
                TASKS[task_id]["segments"].append(current_seg)
            stats = vram_manager.get_stats()
            TASKS[task_id]["vram"] = stats
            if batch_item_id:
                batch_mgr.update_progress(batch_item_id, round(frac * 100, 1))
            
            # Identify log level: explicitly tag [MEM] unload events
            stage_l = stage.lower()
            if "[mem]" in stage_l or "unloaded" in stage_l:
                lvl = "MEM"
            elif "chunk" in stage_l and ("transcribed" in stage_l or "council" in stage_l):
                lvl = "CHUNK"
            else:
                lvl = "STAGE"
            add_log(task_id, lvl, f"{stage} [{round(frac * 100, 1)}%]", stats)
            add_trace_sample(task_id)

    def on_checkpoint(chunk_idx: int, total_chunks: int, current_segs: List[Dict[str, Any]]):
        task_meta = TASKS.get(task_id, {})
        ckpt_mgr.save_checkpoint(
            task_id=task_id,
            filename=task_meta.get("filename", file_path.name),
            file_path=str(file_path),
            last_chunk_index=chunk_idx,
            total_chunks=total_chunks,
            segments=current_segs,
            params={
                "diarizer": diarizer,
                "speaker_labels": speaker_labels,
                "enable_enhancer": enable_enhancer,
                "enable_ambiguity": enable_ambiguity,
                "enable_council": enable_council,
                "council_mode": council_mode,
                "vocal_boost_level": vocal_boost_level,
                "enable_lufs": enable_lufs,
                "target_lufs": target_lufs,
                "chunk_overlap": chunk_overlap,
                "enable_dedup": enable_dedup,
                "whisper_model": whisper_model,
                "conformer_model": conformer_model,
                "parakeet_model": parakeet_model,
                "model_name": model_name,
                "hf_token": hf_token,
                "glossary": glossary,
                "attention_backend": attention_backend
            },
            duration=task_meta.get("duration", 0.0),
            status=task_meta.get("status", "in_progress")
        )

    orig_wav_path = config.upload_dir / f"{task_id}_orig.wav"
    processed_wav_path = config.upload_dir / f"{task_id}_processed.wav"
    chunks_dir = config.upload_dir / f"{task_id}_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    TASKS[task_id]["orig_file_path"] = str(orig_wav_path)
    TASKS[task_id]["processed_file_path"] = str(processed_wav_path)

    try:
        result = pipeline.process_file(
            file_path=file_path,
            enable_diarization=speaker_labels,
            enable_enhancer=enable_enhancer,
            enable_ambiguity_resolver=enable_ambiguity,
            enable_council=enable_council,
            council_mode=council_mode,
            enable_lufs_norm=enable_lufs,
            target_lufs=target_lufs,
            chunk_overlap_s=chunk_overlap,
            enable_boundary_dedup=enable_dedup,
            progress_callback=on_progress,
            pause_event=pause_evt,
            stop_event=stop_evt,
            output_orig_path=orig_wav_path,
            output_processed_path=processed_wav_path,
            chunks_dir=chunks_dir,
            glossary=glossary,
            attention_backend=attention_backend,
            resume_from_chunk=resume_from_chunk,
            existing_segments=existing_segments,
            checkpoint_callback=on_checkpoint
        )
        TASKS[task_id]["status"] = "completed"
        TASKS[task_id]["progress"] = 100.0
        TASKS[task_id]["message"] = "Completed"
        TASKS[task_id]["segments"] = result["segments"]
        TASKS[task_id]["full_text"] = result["full_text"]
        TASKS[task_id]["duration"] = result["duration"]
        TASKS[task_id]["elapsed"] = result["elapsed_seconds"]
        TASKS[task_id]["processed_file_path"] = str(processed_wav_path)
        TASKS[task_id]["has_processed_audio"] = processed_wav_path.exists()
        TASKS[task_id]["chunks_dir"] = str(chunks_dir)
        TASKS[task_id]["audio_health"] = result.get("audio_health", {})
        stats = vram_manager.get_stats()
        TASKS[task_id]["vram"] = stats

        ckpt_mgr.mark_completed(task_id)
        if batch_item_id:
            batch_mgr.mark_completed(batch_item_id)

        add_log(task_id, "SUCCESS", f"Transcription completed in {result['elapsed_seconds']}s ({len(result['segments'])} segments).", stats)
        add_trace_sample(task_id)
    except InterruptedError:
        print(f"[Task {task_id}] Stopped by user request.")
        if task_id in TASKS:
            TASKS[task_id]["status"] = "stopped"
            TASKS[task_id]["message"] = "Stopped by user"
            on_checkpoint(
                chunk_idx=len(TASKS[task_id].get("segments", [])) - 1,
                total_chunks=0,
                current_segs=TASKS[task_id].get("segments", [])
            )
        if batch_item_id:
            batch_mgr.mark_failed(batch_item_id, "Stopped by user")
        add_log(task_id, "WARN", "Task interrupted and cancelled by user.")
        add_trace_sample(task_id)
    except Exception as e:
        print(f"[Error in Task {task_id}] {e}")
        if task_id in TASKS:
            TASKS[task_id]["status"] = "failed"
            TASKS[task_id]["message"] = str(e)
            on_checkpoint(
                chunk_idx=len(TASKS[task_id].get("segments", [])) - 1,
                total_chunks=0,
                current_segs=TASKS[task_id].get("segments", [])
            )
        if batch_item_id:
            batch_mgr.mark_failed(batch_item_id, str(e))
        add_log(task_id, "ERROR", f"Transcription failed: {e}")
        add_trace_sample(task_id)
    finally:
        # Aggressive memory cleanup when worker finishes or stops
        if ctrl and ctrl.get("pipeline"):
            p = ctrl["pipeline"]
            if hasattr(p, "force_eject"):
                try:
                    p.force_eject("all")
                except Exception:
                    pass
            elif hasattr(p, "council") and hasattr(p.council, "unload_members"):
                p.council.unload_members()
            if hasattr(p, "supervisor"):
                p.supervisor.finalize_pipeline()
        vram_manager.clear_cache()
        add_log(task_id, "MEM", "Task worker terminated: models unloaded, caches cleared, heap trimmed.")
        add_trace_sample(task_id)

        # Check and run auto-purge if enabled
        try:
            retention_mgr = get_retention_manager()
            if retention_mgr.get_policy().get("auto_purge_enabled"):
                retention_mgr.execute_purge(TASKS)
        except Exception as pe:
            print(f"[Auto-Purge Warning] {pe}")

        # Trigger next batch item sequentially if available
        try:
            _launch_next_batch_item()
        except Exception as be:
            print(f"[Batch Launch Error] {be}")




@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Poll task progress and results."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    return TASKS[task_id]


@app.get("/api/audio/{task_id}")
async def get_audio_stream(task_id: str):
    """Streams the original audio for playback (serves 16kHz aligned WAV if processed, else source file)."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    orig_wav = TASKS[task_id].get("orig_file_path")
    if orig_wav and Path(orig_wav).exists():
        return FileResponse(Path(orig_wav), media_type="audio/wav")
    audio_path = Path(TASKS[task_id]["file_path"])
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file on disk missing")
    return FileResponse(audio_path)


@app.get("/api/audio/{task_id}/processed")
async def get_processed_audio_stream(task_id: str):
    """Streams the model-ingested 16kHz enhanced audio for synchronized comparison."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    processed_path = TASKS[task_id].get("processed_file_path")
    if not processed_path or not Path(processed_path).exists():
        # Fallback to original audio if processed file does not exist
        orig_path = Path(TASKS[task_id]["file_path"])
        if orig_path.exists():
            return FileResponse(orig_path)
        raise HTTPException(status_code=404, detail="Processed audio not available")
    return FileResponse(Path(processed_path), media_type="audio/wav")


@app.get("/api/audio/{task_id}/chunk/{seg_index}")
async def get_chunk_audio_stream(task_id: str, seg_index: int):
    """
    Streams the exact audio chunk evaluated by the model.
    If the chunk was auto-slowed by ambiguity resolver, serves the 0.75x sample.
    Otherwise slices on-the-fly from the processed audio.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    task = TASKS[task_id]
    chunks_dir = task.get("chunks_dir")
    if chunks_dir:
        slow_file = Path(chunks_dir) / f"chunk_{seg_index}_slow.wav"
        if slow_file.exists():
            return FileResponse(slow_file, media_type="audio/wav")

    # Fallback: slice from processed or original audio
    segments = task.get("segments", [])
    if seg_index < 0 or seg_index >= len(segments):
        raise HTTPException(status_code=404, detail="Segment index out of range")

    seg = segments[seg_index]
    source_audio = task.get("processed_file_path") or task.get("file_path")
    if not source_audio or not Path(source_audio).exists():
        raise HTTPException(status_code=404, detail="Audio source not found")

    cache_dir = Path(chunks_dir) if chunks_dir else config.upload_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    chunk_cache_file = cache_dir / f"chunk_{seg_index}_slice.wav"

    if not chunk_cache_file.exists():
        import soundfile as sf
        try:
            with sf.SoundFile(source_audio) as f:
                sr = f.samplerate
                start_frame = int(seg["start"] * sr)
                num_frames = int((seg["end"] - seg["start"]) * sr)
                f.seek(max(0, start_frame))
                data = f.read(num_frames)
                sf.write(str(chunk_cache_file), data, sr, subtype="PCM_16")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to slice audio chunk: {e}")

    return FileResponse(chunk_cache_file, media_type="audio/wav")


@app.get("/api/audio/{task_id}/slice")
async def get_audio_slice(
    task_id: str,
    start: float = Query(0.0, ge=0.0, description="Start timestamp in seconds"),
    end: float = Query(..., gt=0.0, description="End timestamp in seconds"),
    processed: bool = Query(False, description="Use processed 16kHz audio if available"),
    speed: float = Query(1.0, ge=0.25, le=4.0, description="Optional playback rate time-stretching (e.g. 0.75x)")
):
    """
    Extracts and streams a sub-second precision audio slice [start, end] for
    word/syllable auditioning (Feature 3.2.B) and region loop testing (Feature 3.2.A).
    Supports optional time-stretch playback (e.g. 0.75x speed).
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    if end <= start:
        raise HTTPException(status_code=400, detail="End timestamp must be strictly greater than start timestamp")

    task = TASKS[task_id]
    source_audio = (task.get("processed_audio") or task.get("file_path")) if processed else (task.get("file_path") or task.get("processed_audio"))
    if not source_audio or not Path(source_audio).exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    cache_dir = Path(task.get("chunks_dir") or config.upload_dir) / "slices"
    cache_dir.mkdir(parents=True, exist_ok=True)
    slice_filename = f"slice_{start:.3f}_{end:.3f}_spd{speed:.2f}.wav"
    slice_path = cache_dir / slice_filename

    if not slice_path.exists():
        import soundfile as sf
        import torch
        import torchaudio
        try:
            with sf.SoundFile(source_audio) as f:
                sr = f.samplerate
                total_frames = len(f)
                start_frame = int(start * sr)
                start_frame = min(start_frame, max(0, total_frames - 1))
                num_frames = int((end - start) * sr)
                num_frames = max(1, min(num_frames, total_frames - start_frame))
                f.seek(start_frame)
                data = f.read(num_frames, dtype="float32")

            if abs(speed - 1.0) > 0.02:
                tensor_data = torch.from_numpy(data)
                if tensor_data.ndim == 1:
                    tensor_data = tensor_data.unsqueeze(0)
                else:
                    tensor_data = tensor_data.transpose(0, 1)
                stretched = torchaudio.transforms.Resample(orig_freq=max(100, int(sr * speed)), new_freq=sr)(tensor_data)
                out_data = stretched.transpose(0, 1).numpy()
                sf.write(str(slice_path), out_data, sr, subtype="PCM_16")
            else:
                sf.write(str(slice_path), data, sr, subtype="PCM_16")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to slice audio: {e}")

    return FileResponse(slice_path, media_type="audio/wav")


@app.get("/api/audio/{task_id}/spectrogram")
async def get_audio_spectrogram(
    task_id: str,
    processed: bool = False,
    width: int = Query(1200, ge=100, le=4000),
    height: int = Query(96, ge=32, le=512)
):
    """
    Generates or retrieves cached FFT Mel-Spectrogram Waterfall PNG (Feature 3.1.B).
    Reveals speech formants (300 Hz - 3.5 kHz) vs background noise / sibilants.
    """
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    task = TASKS[task_id]
    target_path = None
    if processed:
        target_path = task.get("processed_file_path")
    if not target_path or not Path(target_path).exists():
        target_path = task.get("orig_file_path") or task.get("file_path")

    if not target_path or not Path(target_path).exists():
        raise HTTPException(status_code=404, detail="Audio file on disk missing")

    try:
        png_bytes = generate_spectrogram_image(
            audio_path_or_tensor=target_path,
            width=width,
            height=height
        )
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate spectrogram: {e}")


@app.post("/api/export")
async def export_transcript(req: ExportRequest):
    """Generates formatted plain text transcript with aliases."""
    text = TranscriptExporter.export_text(
        segments=req.segments,
        speaker_aliases=req.speaker_aliases,
        include_timestamps=req.include_timestamps,
        include_speakers=req.include_speakers
    )
    return PlainTextResponse(text, media_type="text/plain")


# =========================================================================
# Telemetry & Execution Log API Endpoints
# =========================================================================

@app.get("/api/telemetry/trace")
async def get_telemetry_trace(
    task_id: Optional[str] = None,
    interval: int = 2,
    limit: int = 400
):
    """
    Returns time-series memory trace samples.
    interval parameter subsamples points (step=2 for 2s, step=5 for 5s, step=10 for 10s).
    """
    source = TASKS[task_id].get("trace", []) if (task_id and task_id in TASKS) else GLOBAL_TRACE
    step = max(1, interval)
    sampled = source[::step] if step > 1 else list(source)
    tail = sampled[-limit:] if limit > 0 else sampled

    active_task = None
    for tid, t in TASKS.items():
        if t.get("status") in ("processing", "queued", "paused"):
            active_task = {
                "id": tid,
                "filename": t.get("filename"),
                "status": t.get("status"),
                "stage": t.get("message"),
                "progress": t.get("progress", 0.0)
            }
            break

    peak_vram = max([s.get("vram_reserved_gb", 0.0) or s.get("vram_alloc_gb", 0.0) for s in tail], default=0.0)
    peak_ram = max([s.get("ram_used_gb", 0.0) or s.get("sys_ram_used_gb", 0.0) for s in tail], default=0.0)
    peak_proc_ram = max([s.get("proc_ram_used_gb", 0.0) or s.get("app_ram_gb", 0.0) for s in tail], default=0.0)

    # Fallback to current stats if no samples yet
    curr_stats = vram_manager.get_stats()
    if peak_vram == 0.0:
        peak_vram = curr_stats.get("reserved_gb", 0.0) or curr_stats.get("allocated_gb", 0.0)
    if peak_ram == 0.0:
        peak_ram = curr_stats.get("sys_ram_used_gb", 0.0)
    if peak_proc_ram == 0.0:
        peak_proc_ram = curr_stats.get("proc_ram_used_gb", 0.0)

    return {
        "samples": tail,
        "total_recorded": len(source),
        "interval_seconds": interval,
        "current": curr_stats,
        "active_task": active_task,
        "peak": {
            "peak_vram_gb": round(peak_vram, 2),
            "peak_ram_gb": round(peak_ram, 2),
            "peak_proc_ram_gb": round(peak_proc_ram, 2)
        }
    }


@app.get("/api/telemetry/logs")
async def get_telemetry_logs(
    task_id: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 400
):
    """Returns execution logs with level and task filtering."""
    source = TASKS[task_id].get("logs", []) if (task_id and task_id in TASKS) else GLOBAL_LOGS
    filtered = source
    if level and level.upper() != "ALL":
        filtered = [l for l in filtered if l.get("level") == level.upper()]
    tail = filtered[-limit:] if limit > 0 else filtered
    return {
        "logs": tail,
        "total_recorded": len(source)
    }


def _csv_response(headers: list, rows: list, filename_prefix: str) -> Response:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    filename = f"{filename_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/telemetry/export/trace.csv")
async def export_trace_csv(task_id: Optional[str] = None):
    """Exports recorded memory trace telemetry as CSV."""
    source = TASKS[task_id].get("trace", []) if (task_id and task_id in TASKS) else GLOBAL_TRACE
    headers = ["Timestamp", "Time", "Elapsed_Sec", "Task_ID", "File_Name", "Status", "Stage", "App_RAM_GB", "Sys_RAM_Used_GB", "Sys_RAM_Total_GB", "Sys_RAM_Percent", "VRAM_Alloc_GB", "VRAM_Reserved_GB", "VRAM_Total_GB", "VRAM_Percent"]
    rows = [[s.get("timestamp", ""), s.get("time_str", ""), s.get("elapsed_s", 0.0), s.get("task_id", ""), s.get("task_name", ""), s.get("status", ""), s.get("stage", ""), s.get("proc_ram_used_gb", 0.0), s.get("ram_used_gb", 0.0), s.get("ram_total_gb", 0.0), s.get("ram_pct", 0.0), s.get("vram_alloc_gb", 0.0), s.get("vram_reserved_gb", 0.0), s.get("vram_total_gb", 0.0), s.get("vram_pct", 0.0)] for s in source]
    return _csv_response(headers, rows, "memory_trace")


@app.get("/api/telemetry/export/logs.csv")
async def export_logs_csv(task_id: Optional[str] = None):
    """Exports execution logs as CSV."""
    source = TASKS[task_id].get("logs", []) if (task_id and task_id in TASKS) else GLOBAL_LOGS
    headers = ["Timestamp", "Time", "Level", "Task_ID", "Message", "RAM_Used_GB", "RAM_Total_GB", "RAM_Percent", "VRAM_Alloc_GB", "VRAM_Reserved_GB", "VRAM_Total_GB", "VRAM_Percent"]
    rows = [[l.get("timestamp", ""), l.get("time_str", ""), l.get("level", ""), l.get("task_id", ""), l.get("message", ""), l.get("ram_used_gb", 0.0), l.get("ram_total_gb", 0.0), l.get("ram_pct", 0.0), l.get("vram_alloc_gb", 0.0), l.get("vram_reserved_gb", 0.0), l.get("vram_total_gb", 0.0), l.get("vram_pct", 0.0)] for l in source]
    return _csv_response(headers, rows, "execution_logs")


@app.get("/api/telemetry/export/logs.txt")
async def export_logs_txt(task_id: Optional[str] = None):
    """Exports execution logs as plain text report."""
    source = TASKS[task_id].get("logs", []) if (task_id and task_id in TASKS) else GLOBAL_LOGS
    lines = []
    lines.append("=========================================================================")
    lines.append(f"Transcript Suite Execution Log Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=========================================================================\n")
    for l in source:
        t = l.get("time_str", "")
        lvl = l.get("level", "INFO").ljust(7)
        msg = l.get("message", "")
        tid = l.get("task_id", "")
        ram = l.get("ram_used_gb", 0.0)
        vram = l.get("vram_alloc_gb", 0.0)
        lines.append(f"[{t}] [{lvl}] [{tid[:8]}] {msg} | RAM: {ram}GB, VRAM: {vram}GB")

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"execution_logs_{timestamp_str}.txt"
    return Response(
        content="\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/telemetry/clear")
async def clear_telemetry():
    """Clears telemetry trace and log history."""
    GLOBAL_LOGS.clear()
    GLOBAL_TRACE.clear()
    add_log(None, "INFO", "Telemetry trace and execution logs cleared by user.")
    return {"status": "cleared"}


# =========================================================================
# Sub-Phase 5.1: Multi-File Sequential Ingest Queue (v1.5.1)
# =========================================================================

class ReorderBatchRequest(BaseModel):
    item_id: str
    new_position: int


@app.post("/api/ingest/batch")
async def enqueue_batch_audio(
    files: List[UploadFile] = File(...),
    diarizer: str = Form("nemo"),
    speaker_labels: bool = Form(True),
    enable_enhancer: bool = Form(True),
    enable_ambiguity: bool = Form(True),
    enable_council: bool = Form(True),
    council_mode: str = Form("sequential"),
    vocal_boost_level: str = Form("adaptive"),
    enable_lufs: bool = Form(True),
    target_lufs: float = Form(-16.0),
    chunk_overlap: float = Form(0.5),
    enable_dedup: bool = Form(True),
    whisper_model: Optional[str] = Form(None),
    conformer_model: Optional[str] = Form(None),
    parakeet_model: Optional[str] = Form(None),
    model_name: Optional[str] = Form(None),
    hf_token: Optional[str] = Form(None),
    glossary: Optional[str] = Form(None),
    attention_backend: Optional[str] = Form(None)
):
    """
    Accepts multi-file audio batch upload and enqueues items for sequential processing.
    """
    batch_mgr = get_batch_manager()
    parsed_glossary = None
    if glossary:
        try:
            val = json.loads(glossary)
            if isinstance(val, list):
                parsed_glossary = [str(x).strip() for x in val if str(x).strip()]
            elif isinstance(val, str) and val.strip():
                parsed_glossary = [t.strip() for t in val.split(",") if t.strip()]
        except Exception:
            parsed_glossary = [t.strip() for t in glossary.split(",") if t.strip()]

    backend = attention_backend or getattr(config, "attention_backend", "sdpa")

    common_options = {
        "diarizer": diarizer,
        "speaker_labels": speaker_labels,
        "enable_enhancer": enable_enhancer,
        "enable_ambiguity": enable_ambiguity,
        "enable_council": enable_council,
        "council_mode": council_mode,
        "vocal_boost_level": vocal_boost_level,
        "enable_lufs": enable_lufs,
        "target_lufs": target_lufs,
        "chunk_overlap": chunk_overlap,
        "enable_dedup": enable_dedup,
        "whisper_model": whisper_model,
        "conformer_model": conformer_model,
        "parakeet_model": parakeet_model,
        "model_name": model_name,
        "hf_token": hf_token,
        "glossary": parsed_glossary,
        "attention_backend": backend
    }

    enqueued_items = []
    for file in files:
        unique_file_id = str(uuid.uuid4())[:8]
        ext = Path(file.filename).suffix or ".wav"
        saved_path = config.upload_dir / f"batch_{unique_file_id}_{file.filename}"
        with open(saved_path, "wb") as f:
            f.write(await file.read())

        size_mb = round(saved_path.stat().st_size / (1024 * 1024), 2)
        item = batch_mgr.enqueue(
            filename=file.filename,
            file_path=saved_path,
            file_size_mb=size_mb,
            options=common_options
        )
        enqueued_items.append(item.to_dict())

    # Launch immediately if idle
    _launch_next_batch_item()

    return {
        "status": "enqueued",
        "count": len(enqueued_items),
        "items": enqueued_items,
        "batch_status": batch_mgr.get_status()
    }


@app.get("/api/ingest/batch")
async def get_batch_status():
    """Returns current sequential batch queue state, active item, and recent history."""
    return get_batch_manager().get_status()


@app.delete("/api/ingest/batch/{item_id}")
async def cancel_batch_item(item_id: str):
    """Removes a queued item from the batch queue."""
    success = get_batch_manager().remove(item_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot remove item (already processing or not found)")
    return {"status": "cancelled", "item_id": item_id}


@app.post("/api/ingest/batch/reorder")
async def reorder_batch_item(req: ReorderBatchRequest):
    """Reorders a pending queued item."""
    success = get_batch_manager().reorder(req.item_id, req.new_position)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot reorder item")
    return {"status": "reordered", "batch_status": get_batch_manager().get_status()}


@app.post("/api/ingest/batch/clear")
async def clear_batch_queue():
    """Clears all pending items from batch queue."""
    cleared = get_batch_manager().clear_pending()
    return {"status": "cleared", "cleared_count": cleared}


@app.post("/api/ingest/batch/pause")
async def pause_batch_processing():
    """Pauses sequential execution of subsequent batch items."""
    get_batch_manager().pause()
    return {"status": "paused", "is_paused": True}


@app.post("/api/ingest/batch/resume")
async def resume_batch_processing():
    """Resumes sequential execution of batch items and triggers next item if idle."""
    get_batch_manager().resume()
    _launch_next_batch_item()
    return {"status": "resumed", "is_paused": False}


# =========================================================================
# Sub-Phase 5.2.B: Storage Quota & Auto-Purge Retention Policies (v1.5.2)
# =========================================================================

class RetentionPolicyUpdate(BaseModel):
    audio_retention_days: Optional[int] = None
    storage_quota_gb: Optional[float] = None
    auto_purge_enabled: Optional[bool] = None


@app.get("/api/storage/retention")
async def get_storage_retention_status():
    """Returns current audio retention policy and dry-run purge target scan."""
    mgr = get_retention_manager()
    scan = mgr.scan_purging_targets(TASKS)
    return scan


@app.post("/api/storage/retention/policy")
async def update_storage_retention_policy(req: RetentionPolicyUpdate):
    """Updates retention policy settings (days, quota GB, auto_purge)."""
    mgr = get_retention_manager()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    new_policy = mgr.update_policy(updates)
    return {"status": "updated", "policy": new_policy}


@app.post("/api/storage/retention/run")
async def run_storage_retention_purge():
    """Manually triggers retention purge of expired/quota-exceeding raw audio files."""
    mgr = get_retention_manager()
    res = mgr.execute_purge(TASKS)
    return res


# =========================================================================
# Sub-Phase 5.3.A: Workspace Full Archive Backup (v1.5.3)
# =========================================================================

@app.get("/api/workspace/summary")
async def get_workspace_meta_summary():
    """Returns workspace-wide summary metrics (sessions, duration, words, glossaries)."""
    return get_workspace_summary(TASKS)


@app.get("/api/workspace/backup")
async def export_workspace_backup():
    """Streams full workspace archive (.zip) containing transcripts, SRT, VTT, glossaries, settings, and journal."""
    zip_buffer = create_workspace_archive(TASKS)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transcript_workspace_backup_{timestamp}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


