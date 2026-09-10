# 🌲 Transcript Suite

A high-performance speech transcription suite powered by **NVIDIA Canary-Qwen-2.5B**, tailored for Linux / Arch Linux systems with 8 GB VRAM GPUs (e.g. RTX 4060 Laptop). Features **Speaker Diarization**, **AAC audio optimization**, **CLI tools**, and a **nature-themed dark Web UI** with audio-transcript synchronization.

---

## Key Features

- **NVIDIA Canary-Qwen-2.5B**: State-of-the-art hybrid speech-augmented language model for speech-to-text with truecasing and punctuation.
- **VRAM Safety Guard**: Automatic chunking (15–25s) with proactive CUDA cache flushes, ensuring zero Out-of-Memory errors on 8 GB GPUs.
- **Speaker Diarization**:
  - **Route A (Default)**: NVIDIA NeMo TitaNet (100% local, open-weights, zero token required).
  - **Route B (Optional)**: PyAnnote Audio 3.x (via `--diarizer pyannote --hf-token <TOKEN>`).
- **AAC Audio Optimized**: Native FFmpeg ingest and downmix to 16kHz mono.
- **Nature-Themed Web UI (Strictly Dark & Low-Strain)**:
  - 🌲 **Foggy Woodland (Default)**: Overcast forest morning with damp tree bark browns, wet pine greens, misty grays, and cold stream blues.
  - 🌿 **Forest Sage**: Deep charcoal pine with soft sage leaf and lichen accents.
  - ⛰️ **Nordic Slate**: Desaturated storm slate and dusty blue-gray.
  - ☕ **Warm Umber**: Roasted espresso timber and muted linen clay.
  - *(Strictly no light mode)*.
- **Interactive Audio Playhead Sync**:
  - Waveform visualizer powered by WaveSurfer.js.
  - Click any transcript segment to seek audio immediately.
  - Playing audio highlights the active speaker segment in real-time.
- **In-Place Transcript Editing**: Fix misheard terms or names directly in the browser.
- **Speaker Aliasing**: Rename `Speaker 0` → `Alice` or leave as default.
- **Plain Text Export**: Instant download of clean, formatted transcripts.

---

## Quick Start

### 1. Environment Activation
```bash
source .venv/bin/activate
```

### 2. Check System & GPU Diagnostics
```bash
transcript-suite check-gpu
```

### 3. CLI Audio Transcription
Transcribe a single AAC file:
```bash
transcript-suite transcribe sample.aac --output transcript.txt
```

Batch process a folder of audio files:
```bash
transcript-suite process ./recordings/ --pattern "*.aac"
```

### 4. Launch the Web UI
```bash
transcript-suite serve --port 8000
```
Open your browser at `http://127.0.0.1:8000`.
