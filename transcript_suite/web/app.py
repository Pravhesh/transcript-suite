"""
FastAPI Backend Application for Transcript Suite.
"""

from pathlib import Path
import uuid
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
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


@app.post("/api/transcribe")
async def create_transcription_task(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    diarizer: str = Form("nemo"),
    speaker_labels: bool = Form(True),
    enable_enhancer: bool = Form(True),
    enable_ambiguity: bool = Form(True),
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

    pause_event = threading.Event()
    pause_event.set()  # Not paused by default
    stop_event = threading.Event()

    TASK_CONTROLS[task_id] = {
        "pause_event": pause_event,
        "stop_event": stop_event,
        "pipeline": None
    }

    TASKS[task_id] = {
        "id": task_id,
        "filename": audio.filename,
        "file_path": str(saved_path),
        "status": "queued",
        "progress": 0.0,
        "message": "Queued in processing pipeline...",
        "segments": [],
        "full_text": "",
        "vram": vram_manager.get_stats()
    }

    background_tasks.add_task(
        run_transcription_worker,
        task_id=task_id,
        file_path=saved_path,
        diarizer=diarizer,
        speaker_labels=speaker_labels,
        enable_enhancer=enable_enhancer,
        enable_ambiguity=enable_ambiguity,
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
        TASKS[task_id]["message"] = "Paused"
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

    vram_manager.clear_cache()

    if task_id in TASKS:
        TASKS[task_id]["status"] = "stopped"
        TASKS[task_id]["message"] = "Stopped by user"
    return {"status": "stopped"}


def run_transcription_worker(
    task_id: str,
    file_path: Path,
    diarizer: str,
    speaker_labels: bool,
    enable_enhancer: bool,
    enable_ambiguity: bool,
    hf_token: Optional[str]
):
    ctrl = TASK_CONTROLS.get(task_id)
    pause_evt = ctrl["pause_event"] if ctrl else None
    stop_evt = ctrl["stop_event"] if ctrl else None

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
            TASKS[task_id]["vram"] = vram_manager.get_stats()

    try:
        result = pipeline.process_file(
            file_path=file_path,
            enable_diarization=speaker_labels,
            enable_enhancer=enable_enhancer,
            enable_ambiguity_resolver=enable_ambiguity,
            progress_callback=on_progress,
            pause_event=pause_evt,
            stop_event=stop_evt
        )
        TASKS[task_id]["status"] = "completed"
        TASKS[task_id]["progress"] = 100.0
        TASKS[task_id]["message"] = "Completed"
        TASKS[task_id]["segments"] = result["segments"]
        TASKS[task_id]["full_text"] = result["full_text"]
        TASKS[task_id]["duration"] = result["duration"]
        TASKS[task_id]["elapsed"] = result["elapsed_seconds"]
        TASKS[task_id]["vram"] = vram_manager.get_stats()
    except InterruptedError:
        print(f"[Task {task_id}] Stopped by user request.")
        if task_id in TASKS:
            TASKS[task_id]["status"] = "stopped"
            TASKS[task_id]["message"] = "Stopped by user"
    except Exception as e:
        print(f"[Error in Task {task_id}] {e}")
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["message"] = str(e)
    finally:
        # Aggressive memory cleanup when worker finishes or stops
        vram_manager.clear_cache()



@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Poll task progress and results."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    return TASKS[task_id]


@app.get("/api/audio/{task_id}")
async def get_audio_stream(task_id: str):
    """Streams the uploaded audio for playback."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")
    audio_path = Path(TASKS[task_id]["file_path"])
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file on disk missing")
    return FileResponse(audio_path)


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
