/* ==========================================================================
   Transcript Suite - Client Application Logic
   ========================================================================== */

let selectedFile = null;
let currentTaskId = null;
let currentSegments = [];
let speakerAliases = {};
let wavesurfer = null;
let activeSpeakerFilter = "ALL";

// DOM Elements
const themeSelect = document.getElementById("themeSelect");
const vramMeter = document.getElementById("vramMeter");
const vramBarFill = document.getElementById("vramBarFill");
const vramText = document.getElementById("vramText");

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const dropzoneText = document.getElementById("dropzoneText");
const btnStart = document.getElementById("btnStart");
const speakerLabelsCheckbox = document.getElementById("speakerLabelsCheckbox");
const diarizerSelect = document.getElementById("diarizerSelect");

const progressCard = document.getElementById("progressCard");
const progressStatus = document.getElementById("progressStatus");
const progressPercentage = document.getElementById("progressPercentage");
const progressFill = document.getElementById("progressFill");

const playerCard = document.getElementById("playerCard");
const btnPlayPause = document.getElementById("btnPlayPause");
const btnBack5 = document.getElementById("btnBack5");
const btnFwd5 = document.getElementById("btnFwd5");
const playbackSpeed = document.getElementById("playbackSpeed");
const playerTime = document.getElementById("playerTime");

const transcriptCard = document.getElementById("transcriptCard");
const transcriptFeed = document.getElementById("transcriptFeed");
const speakerFilters = document.getElementById("speakerFilters");
const searchInput = document.getElementById("searchInput");
const btnExportTxt = document.getElementById("btnExportTxt");

const renameModal = document.getElementById("renameModal");
const btnRenameModal = document.getElementById("btnRenameModal");
const btnCancelRename = document.getElementById("btnCancelRename");
const btnSaveAliases = document.getElementById("btnSaveAliases");
const aliasInputsContainer = document.getElementById("aliasInputsContainer");

// --- 1. Theme Management ---
function initTheme() {
  const saved = localStorage.getItem("ts_theme") || "foggy-woodland";
  document.body.dataset.theme = saved;
  themeSelect.value = saved;
}

themeSelect.addEventListener("change", (e) => {
  const theme = e.target.value;
  document.body.dataset.theme = theme;
  localStorage.setItem("ts_theme", theme);
  updateWaveformTheme();
});

// --- 2. VRAM Monitoring ---
async function fetchVRAM() {
  try {
    const res = await fetch("/api/vram");
    if (!res.ok) return;
    const data = await res.json();
    if (data.available) {
      vramBarFill.style.width = `${data.percent_used}%`;
      vramText.innerText = `${data.reserved_gb} / ${data.total_gb} GB (${data.percent_used}%)`;
    } else {
      vramText.innerText = "CPU Mode";
    }
  } catch (err) {
    // Silent fail on polling error
  }
}
setInterval(fetchVRAM, 3000);
fetchVRAM();

// --- 3. Drag & Drop File Handling ---
dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (e.dataTransfer.files.length > 0) {
    handleFileSelect(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files.length > 0) {
    handleFileSelect(e.target.files[0]);
  }
});

function handleFileSelect(file) {
  selectedFile = file;
  dropzoneText.innerHTML = `<strong>Selected:</strong> ${file.name} (${(file.size / (1024*1024)).toFixed(1)} MB)`;
  btnStart.disabled = false;
}

// --- 4. Transcription Initiation & Polling ---
btnStart.addEventListener("click", async () => {
  if (!selectedFile) return;

  btnStart.disabled = true;
  progressCard.style.display = "block";
  progressStatus.innerText = "Uploading audio...";
  progressFill.style.width = "5%";
  progressPercentage.innerText = "5%";

  const formData = new FormData();
  formData.append("audio", selectedFile);
  formData.append("diarizer", diarizerSelect.value);
  formData.append("speaker_labels", speakerLabelsCheckbox.checked);

  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: formData });
    if (!res.ok) throw new Error("Failed to start transcription");
    const data = await res.json();
    currentTaskId = data.task_id;
    pollTaskStatus(currentTaskId);
  } catch (err) {
    alert("Error starting transcription: " + err.message);
    btnStart.disabled = false;
    progressCard.style.display = "none";
  }
});

async function pollTaskStatus(taskId) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) return;
      const data = await res.json();

      progressStatus.innerText = data.message || "Processing...";
      progressFill.style.width = `${data.progress}%`;
      progressPercentage.innerText = `${Math.round(data.progress)}%`;

      if (data.status === "completed") {
        clearInterval(timer);
        progressCard.style.display = "none";
        onTranscriptionSuccess(data);
      } else if (data.status === "failed") {
        clearInterval(timer);
        alert("Transcription failed: " + data.message);
        btnStart.disabled = false;
        progressCard.style.display = "none";
      }
    } catch (e) {
      // Continue polling
    }
  }, 1000);
}

// --- 5. Rendering Transcript & Audio Player ---
function onTranscriptionSuccess(data) {
  currentSegments = data.segments;
  initAudioPlayer(data.id);
  renderSpeakerFilters();
  renderTranscriptFeed();
  playerCard.style.display = "block";
  transcriptCard.style.display = "block";
}

function initAudioPlayer(taskId) {
  if (wavesurfer) {
    wavesurfer.destroy();
  }

  wavesurfer = WaveSurfer.create({
    container: '#waveform',
    waveColor: '#3d4841',
    progressColor: '#637a6b',
    cursorColor: '#8a9b8f',
    height: 70,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    url: `/api/audio/${taskId}`
  });

  wavesurfer.on('timeupdate', (currentTime) => {
    updatePlaybackTime(currentTime, wavesurfer.getDuration());
    syncActiveSegment(currentTime);
  });

  wavesurfer.on('play', () => btnPlayPause.innerText = "⏸ Pause");
  wavesurfer.on('pause', () => btnPlayPause.innerText = "▶ Play");
}

function updateWaveformTheme() {
  if (!wavesurfer) return;
  const currentTheme = document.body.dataset.theme;
  if (currentTheme === "forest-sage") {
    wavesurfer.setOptions({ waveColor: '#2b3930', progressColor: '#708a78' });
  } else if (currentTheme === "nordic-slate") {
    wavesurfer.setOptions({ waveColor: '#252e3d', progressColor: '#647f96' });
  } else if (currentTheme === "warm-umber") {
    wavesurfer.setOptions({ waveColor: '#362b25', progressColor: '#856f62' });
  } else {
    // foggy-woodland
    wavesurfer.setOptions({ waveColor: '#343b37', progressColor: '#556c7f' });
  }
}

btnPlayPause.addEventListener("click", () => wavesurfer && wavesurfer.playPause());
btnBack5.addEventListener("click", () => wavesurfer && wavesurfer.setTime(Math.max(0, wavesurfer.getCurrentTime() - 5)));
btnFwd5.addEventListener("click", () => wavesurfer && wavesurfer.setTime(Math.min(wavesurfer.getDuration(), wavesurfer.getCurrentTime() + 5)));
playbackSpeed.addEventListener("change", (e) => wavesurfer && wavesurfer.setPlaybackRate(parseFloat(e.target.value)));

function formatSeconds(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function updatePlaybackTime(curr, total) {
  playerTime.innerText = `${formatSeconds(curr)} / ${formatSeconds(total || 0)}`;
}

// --- 6. Playhead Sync & Segment Rendering ---
function getSpeakerClass(speaker) {
  const match = speaker.match(/\d+/);
  const num = match ? parseInt(match[0], 10) % 4 : 0;
  return `spk-${num}`;
}

function renderTranscriptFeed() {
  transcriptFeed.innerHTML = "";
  const query = searchInput.value.toLowerCase();

  currentSegments.forEach((seg, index) => {
    const rawSpeaker = seg.speaker || "Speaker 0";
    const displayName = speakerAliases[rawSpeaker] || rawSpeaker;

    if (activeSpeakerFilter !== "ALL" && rawSpeaker !== activeSpeakerFilter) {
      return;
    }

    if (query && !seg.text.toLowerCase().includes(query)) {
      return;
    }

    const block = document.createElement("div");
    block.className = "segment-block";
    block.dataset.index = index;
    block.dataset.start = seg.start;
    block.dataset.end = seg.end;

    const spkClass = getSpeakerClass(rawSpeaker);

    block.innerHTML = `
      <div class="segment-header">
        <span class="speaker-badge ${spkClass}">${displayName}</span>
        <span class="timestamp-pill">[${formatSeconds(seg.start)} - ${formatSeconds(seg.end)}]</span>
      </div>
      <div class="segment-text" contenteditable="true" spellcheck="false">${seg.text}</div>
    `;

    // Click block or timestamp to jump audio
    block.addEventListener("click", (e) => {
      if (e.target.classList.contains("segment-text")) return; // Don't interrupt typing
      if (wavesurfer) {
        wavesurfer.setTime(seg.start);
        wavesurfer.play();
      }
    });

    // In-place edits update memory
    const textEl = block.querySelector(".segment-text");
    textEl.addEventListener("blur", () => {
      seg.text = textEl.innerText.trim();
    });

    transcriptFeed.appendChild(block);
  });
}

function syncActiveSegment(currentTime) {
  const blocks = transcriptFeed.querySelectorAll(".segment-block");
  blocks.forEach((b) => {
    const start = parseFloat(b.dataset.start);
    const end = parseFloat(b.dataset.end);
    if (currentTime >= start && currentTime <= end) {
      if (!b.classList.contains("active")) {
        b.classList.add("active");
        b.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } else {
      b.classList.remove("active");
    }
  });
}

// --- 7. Speaker Filters & Search ---
function renderSpeakerFilters() {
  const speakers = Array.from(new Set(currentSegments.map(s => s.speaker || "Speaker 0")));
  speakerFilters.innerHTML = `<div class="filter-pill ${activeSpeakerFilter === 'ALL' ? 'active' : ''}" data-speaker="ALL">All Speakers</div>`;

  speakers.forEach(spk => {
    const displayName = speakerAliases[spk] || spk;
    const pill = document.createElement("div");
    pill.className = `filter-pill ${activeSpeakerFilter === spk ? 'active' : ''}`;
    pill.dataset.speaker = spk;
    pill.innerText = displayName;
    pill.addEventListener("click", () => {
      activeSpeakerFilter = spk;
      renderSpeakerFilters();
      renderTranscriptFeed();
    });
    speakerFilters.appendChild(pill);
  });

  speakerFilters.querySelector('[data-speaker="ALL"]').addEventListener("click", () => {
    activeSpeakerFilter = "ALL";
    renderSpeakerFilters();
    renderTranscriptFeed();
  });
}

searchInput.addEventListener("input", () => renderTranscriptFeed());

// --- 8. Speaker Alias Management ---
btnRenameModal.addEventListener("click", () => {
  const speakers = Array.from(new Set(currentSegments.map(s => s.speaker || "Speaker 0")));
  aliasInputsContainer.innerHTML = "";

  speakers.forEach(spk => {
    const row = document.createElement("div");
    row.className = "alias-row";
    row.innerHTML = `
      <span style="font-size: 13px; color: var(--text-secondary);">${spk}</span>
      <input type="text" class="text-input alias-input" data-original="${spk}" value="${speakerAliases[spk] || ''}" placeholder="Enter name or leave as ${spk}">
    `;
    aliasInputsContainer.appendChild(row);
  });

  renameModal.style.display = "flex";
});

btnCancelRename.addEventListener("click", () => renameModal.style.display = "none");

btnSaveAliases.addEventListener("click", () => {
  const inputs = aliasInputsContainer.querySelectorAll(".alias-input");
  inputs.forEach(inp => {
    const original = inp.dataset.original;
    const val = inp.value.trim();
    if (val) {
      speakerAliases[original] = val;
    } else {
      delete speakerAliases[original];
    }
  });
  renameModal.style.display = "none";
  renderSpeakerFilters();
  renderTranscriptFeed();
});

// --- 9. Export .TXT ---
btnExportTxt.addEventListener("click", async () => {
  if (!currentSegments || currentSegments.length === 0) return;

  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        segments: currentSegments,
        speaker_aliases: speakerAliases,
        include_timestamps: true,
        include_speakers: true
      })
    });

    if (!res.ok) throw new Error("Export failed");
    const textData = await res.text();

    const blob = new Blob([textData], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const baseName = selectedFile ? selectedFile.name.replace(/\.[^/.]+$/, "") : "transcript";
    a.href = url;
    a.download = `${baseName}_transcript.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert("Export failed: " + err.message);
  }
});

initTheme();
