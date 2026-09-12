"""
FastAPI Backend Application for Transcript Suite.
"""

from pathlib import Path
import uuid
import asyncio
import io
import csv
import time
import gc
import torch
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Response, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from ..config import config
from ..pipeline import TranscriptionPipeline
from ..asr.memory import VRAMManager
from ..asr.model_manager import model_manager
from ..export import TranscriptExporter

app = FastAPI(title="Transcript Suite API")

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
    
    hf_cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    nemo_cache_dir = Path.home() / ".cache" / "torch" / "NeMo"
    temp_dir = Path("/tmp")
    
    hf_models = []
    hf_total = 0
    if hf_cache_dir.exists():
        for d in hf_cache_dir.iterdir():
            if d.is_dir():
                sz = get_path_size(d)
                hf_total += sz
                if sz > 5 * 1024 * 1024:
                    clean_name = d.name.replace("models--", "").replace("--", "/")
                    hf_models.append({"name": clean_name, "bytes": sz, "mb": round(sz / (1024 * 1024), 1)})
        hf_models.sort(key=lambda x: x["bytes"], reverse=True)
        
    nemo_models = []
    nemo_total = 0
    if nemo_cache_dir.exists():
        for d in nemo_cache_dir.iterdir():
            sz = get_path_size(d)
            nemo_total += sz
            if sz > 5 * 1024 * 1024:
                nemo_models.append({"name": d.name, "bytes": sz, "mb": round(sz / (1024 * 1024), 1)})
        nemo_models.sort(key=lambda x: x["bytes"], reverse=True)
        
    temp_audio_bytes = 0
    if temp_dir.exists():
        for f in temp_dir.glob("*"):
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


@app.get("/api/telemetry/deep-memory")
async def get_deep_memory():
    """Returns deep system process RAM, suite memory sections, and granular GPU breakdown."""
    return vram_manager.get_deep_memory_trace()


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
    """Starts background download of a Hugging Face or NeMo model checkpoint."""
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
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result.get("message"))

    add_log(None, "DOWNLOAD", f"Initiated background install of '{model_id}' ({framework}).")
    return result


@app.get("/api/models/install/status")
async def get_model_install_status():
    """Polls background model download status."""
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
    whisper_model: Optional[str] = Form(None),
    conformer_model: Optional[str] = Form(None),
    parakeet_model: Optional[str] = Form(None),
    model_name: Optional[str] = Form(None),
    hf_token: Optional[str] = Form(None)
):
    """
    Uploads an AAC/audio file and starts background transcription with enhancer, ambiguity, and council controls.
    """
    task_id = str(uuid.uuid4())
    file_ext = Path(audio.filename).suffix or ".aac"
    saved_path = config.upload_dir / f"{task_id}{file_ext}"

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
        whisper_model=whisper_model,
        conformer_model=conformer_model,
        parakeet_model=parakeet_model,
        model_name=model_name,
        hf_token=hf_token
    )

    return {"task_id": task_id, "status": "queued"}



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
    """Resumes a paused transcription task."""
    ctrl = TASK_CONTROLS.get(task_id)
    if not ctrl:
        raise HTTPException(status_code=404, detail="Task control not found")
    ctrl["pause_event"].set()
    if task_id in TASKS:
        TASKS[task_id]["status"] = "processing"
        TASKS[task_id]["message"] = "Resuming..."
    add_log(task_id, "INFO", "Transcription resumed by user.")
    add_trace_sample(task_id)
    return {"status": "resumed"}


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
    """Updates a transcript segment text or speaker alias."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    segs = TASKS[task_id].get("segments", [])
    if segment_idx < 0 or segment_idx >= len(segs):
        raise HTTPException(status_code=404, detail="Segment index out of range")
    if "text" in payload:
        segs[segment_idx]["text"] = payload["text"]
    if "speaker" in payload:
        segs[segment_idx]["speaker"] = payload["speaker"]
    TASKS[task_id]["full_text"] = " ".join(s.get("text", "") for s in segs)
    return {"status": "updated", "segment": segs[segment_idx]}


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
    whisper_model: Optional[str] = None,
    conformer_model: Optional[str] = None,
    parakeet_model: Optional[str] = None,
    model_name: Optional[str] = None,
    hf_token: Optional[str] = None
):
    ctrl = TASK_CONTROLS.get(task_id)
    pause_evt = ctrl["pause_event"] if ctrl else None
    stop_evt = ctrl["stop_event"] if ctrl else None

    pipeline = TranscriptionPipeline(
        diarizer_type=diarizer,
        hf_token=hf_token,
        model_name=model_name,
        whisper_model=whisper_model,
        conformer_model=conformer_model,
        parakeet_model=parakeet_model,
        vocal_boost_level=vocal_boost_level
    )
    if ctrl:
        ctrl["pipeline"] = pipeline

    add_log(task_id, "INFO", f"Pipeline starting: Lead={pipeline.transcriber.model_name.split('/')[-1]}, Whisper={pipeline.council.whisper_model_id.split('/')[-1]}, CTC={pipeline.council.conformer_model_id.split('/')[-1]}, TDT={pipeline.council.parakeet_model_id.split('/')[-1]}, Boost={pipeline.vocal_boost_level}")

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
            progress_callback=on_progress,
            pause_event=pause_evt,
            stop_event=stop_evt,
            output_orig_path=orig_wav_path,
            output_processed_path=processed_wav_path,
            chunks_dir=chunks_dir
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
        stats = vram_manager.get_stats()
        TASKS[task_id]["vram"] = stats

        add_log(task_id, "SUCCESS", f"Transcription completed in {result['elapsed_seconds']}s ({len(result['segments'])} segments).", stats)
        add_trace_sample(task_id)
    except InterruptedError:
        print(f"[Task {task_id}] Stopped by user request.")
        if task_id in TASKS:
            TASKS[task_id]["status"] = "stopped"
            TASKS[task_id]["message"] = "Stopped by user"
        add_log(task_id, "WARN", "Task interrupted and cancelled by user.")
        add_trace_sample(task_id)
    except Exception as e:
        print(f"[Error in Task {task_id}] {e}")
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["message"] = str(e)
        add_log(task_id, "ERROR", f"Transcription failed: {e}")
        add_trace_sample(task_id)
    finally:
        # Aggressive memory cleanup when worker finishes or stops
        if ctrl and ctrl.get("pipeline"):
            if hasattr(ctrl["pipeline"].transcriber, "unload_model"):
                ctrl["pipeline"].transcriber.unload_model()
            if hasattr(ctrl["pipeline"], "council") and hasattr(ctrl["pipeline"].council, "unload_members"):
                ctrl["pipeline"].council.unload_members()
        vram_manager.clear_cache()
        add_log(task_id, "MEM", "Task worker terminated: models unloaded, caches cleared, heap trimmed.")
        add_trace_sample(task_id)



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


@app.get("/api/telemetry/export/trace.csv")
async def export_trace_csv(task_id: Optional[str] = None):
    """Exports recorded memory trace telemetry as CSV."""
    source = TASKS[task_id].get("trace", []) if (task_id and task_id in TASKS) else GLOBAL_TRACE
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp",
        "Time",
        "Elapsed_Sec",
        "Task_ID",
        "File_Name",
        "Status",
        "Stage",
        "App_RAM_GB",
        "Sys_RAM_Used_GB",
        "Sys_RAM_Total_GB",
        "Sys_RAM_Percent",
        "VRAM_Alloc_GB",
        "VRAM_Reserved_GB",
        "VRAM_Total_GB",
        "VRAM_Percent"
    ])
    for s in source:
        writer.writerow([
            s.get("timestamp", ""),
            s.get("time_str", ""),
            s.get("elapsed_s", 0.0),
            s.get("task_id", ""),
            s.get("task_name", ""),
            s.get("status", ""),
            s.get("stage", ""),
            s.get("proc_ram_used_gb", 0.0),
            s.get("ram_used_gb", 0.0),
            s.get("ram_total_gb", 0.0),
            s.get("ram_pct", 0.0),
            s.get("vram_alloc_gb", 0.0),
            s.get("vram_reserved_gb", 0.0),
            s.get("vram_total_gb", 0.0),
            s.get("vram_pct", 0.0)
        ])

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"memory_trace_{timestamp_str}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/api/telemetry/export/logs.csv")
async def export_logs_csv(task_id: Optional[str] = None):
    """Exports execution logs as CSV."""
    source = TASKS[task_id].get("logs", []) if (task_id and task_id in TASKS) else GLOBAL_LOGS
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp",
        "Time",
        "Level",
        "Task_ID",
        "Message",
        "RAM_Used_GB",
        "RAM_Total_GB",
        "RAM_Percent",
        "VRAM_Alloc_GB",
        "VRAM_Reserved_GB",
        "VRAM_Total_GB",
        "VRAM_Percent"
    ])
    for l in source:
        writer.writerow([
            l.get("timestamp", ""),
            l.get("time_str", ""),
            l.get("level", ""),
            l.get("task_id", ""),
            l.get("message", ""),
            l.get("ram_used_gb", 0.0),
            l.get("ram_total_gb", 0.0),
            l.get("ram_pct", 0.0),
            l.get("vram_alloc_gb", 0.0),
            l.get("vram_reserved_gb", 0.0),
            l.get("vram_total_gb", 0.0),
            l.get("vram_pct", 0.0)
        ])

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"execution_logs_{timestamp_str}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


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

