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
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Response, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from ..config import config
from ..pipeline import TranscriptionPipeline
from ..asr.memory import VRAMManager
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

    entry = {
        "timestamp": now.isoformat(),
        "time_str": now.strftime("%H:%M:%S"),
        "elapsed_s": elapsed,
        "task_id": task_id or "idle",
        "task_name": task_name,
        "status": task_status,
        "stage": stage,
        "proc_ram_used_gb": stats.get("proc_ram_used_gb", 0.0),
        "ram_used_gb": stats.get("sys_ram_used_gb", 0.0),
        "ram_total_gb": stats.get("sys_ram_total_gb", 0.0),
        "ram_pct": stats.get("sys_ram_percent", 0.0),
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


@app.get("/api/vram")
async def get_vram():
    """Returns real-time GPU VRAM and System RAM telemetry."""
    return vram_manager.get_stats()


@app.post("/api/memory/clear")
async def clear_system_memory():
    """
    Manually triggers proactive memory cleanup:
    - Runs 2 gc collection passes
    - Clears PyTorch CUDA cached memory and IPC memory
    - Calls malloc_trim(0) to release glibc heap pages directly back to Linux OS
    """
    import gc
    gc.collect()
    gc.collect()
    vram_manager.clear_cache()
    if torch.cuda.is_available():
        torch.cuda.ipc_collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    stats = vram_manager.get_stats()
    add_log(None, "MEM", f"Proactive memory trim: Cache flushed, heap trimmed back to OS. Free VRAM: {stats.get('free_gb', 0)} GB, System RAM used: {stats.get('sys_ram_used_gb', 0)} GB.", stats)
    add_trace_sample()
    return {"status": "cleared", "stats": stats}


@app.post("/api/transcribe")
async def create_transcription_task(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    diarizer: str = Form("nemo"),
    speaker_labels: bool = Form(True),
    enable_enhancer: bool = Form(True),
    enable_ambiguity: bool = Form(True),
    enable_council: bool = Form(True),
    hf_token: Optional[str] = Form(None)
):
    """
    Uploads an AAC/audio file and starts background transcription with enhancer and ambiguity controls.
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
    """Stops and cancels an in-progress transcription task, freeing memory."""
    ctrl = TASK_CONTROLS.get(task_id)
    if ctrl:
        ctrl["stop_event"].set()
        ctrl["pause_event"].set()  # Unblock if currently paused
        if ctrl.get("pipeline") and hasattr(ctrl["pipeline"].transcriber, "unload_model"):
            ctrl["pipeline"].transcriber.unload_model()
        if ctrl.get("pipeline") and hasattr(ctrl["pipeline"], "council") and hasattr(ctrl["pipeline"].council, "unload_members"):
            ctrl["pipeline"].council.unload_members()

    vram_manager.clear_cache()

    if task_id in TASKS:
        TASKS[task_id]["status"] = "stopped"
        TASKS[task_id]["message"] = "Stopped by user"
    add_log(task_id, "WARN", "Transcription stopped and cancelled by user. VRAM reclaimed.")
    add_trace_sample(task_id)
    return {"status": "stopped"}


def run_transcription_worker(
    task_id: str,
    file_path: Path,
    diarizer: str,
    speaker_labels: bool,
    enable_enhancer: bool,
    enable_ambiguity: bool,
    enable_council: bool,
    hf_token: Optional[str]
):
    ctrl = TASK_CONTROLS.get(task_id)
    pause_evt = ctrl["pause_event"] if ctrl else None
    stop_evt = ctrl["stop_event"] if ctrl else None

    add_log(task_id, "INFO", f"Pipeline starting: Diarizer={diarizer}, GPU Enhancer={enable_enhancer}, Ambiguity Resolver={enable_ambiguity}, Council={enable_council}")
    pipeline = TranscriptionPipeline(diarizer_type=diarizer, hf_token=hf_token)
    if ctrl:
        ctrl["pipeline"] = pipeline

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
            
            # Identify log level
            stage_l = stage.lower()
            lvl = "CHUNK" if ("chunk" in stage_l and ("transcribed" in stage_l or "council" in stage_l)) else "STAGE"
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

    return {
        "samples": tail,
        "total_recorded": len(source),
        "interval_seconds": interval,
        "current": vram_manager.get_stats(),
        "active_task": active_task
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

