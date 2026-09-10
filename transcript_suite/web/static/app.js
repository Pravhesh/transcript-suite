/* ==========================================================================
   Transcript Suite - Client Application Logic
   ========================================================================== */

let selectedFile = null;
let currentTaskId = null;
let currentSegments = [];
let speakerAliases = {};
let wavesurferOrig = null;
let wavesurferModel = null;
let isSeekingSync = false;
let activeSpeakerFilter = "ALL";
let soloState = { orig: false, model: false };
let muteState = { orig: false, model: false };
let volumeState = { orig: 1.0, model: 1.0 };
let chunkAuditionAudio = null;

// DOM Elements
const themeSelect = document.getElementById("themeSelect");
const vramMeter = document.getElementById("vramMeter");
const vramBarFill = document.getElementById("vramBarFill");
const vramText = document.getElementById("vramText");
const ramBarFill = document.getElementById("ramBarFill");
const ramText = document.getElementById("ramText");

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const dropzoneText = document.getElementById("dropzoneText");
const btnStart = document.getElementById("btnStart");
const speakerLabelsCheckbox = document.getElementById("speakerLabelsCheckbox");
const voiceEnhancerCheckbox = document.getElementById("voiceEnhancerCheckbox");
const ambiguityCheckbox = document.getElementById("ambiguityCheckbox");
const diarizerSelect = document.getElementById("diarizerSelect");

const progressCard = document.getElementById("progressCard");
const progressStatus = document.getElementById("progressStatus");
const progressPercentage = document.getElementById("progressPercentage");
const progressFill = document.getElementById("progressFill");
const btnPause = document.getElementById("btnPause");
const btnResume = document.getElementById("btnResume");
const btnStop = document.getElementById("btnStop");

let pollTimer = null;

const playerCard = document.getElementById("playerCard");
const btnABOriginal = document.getElementById("btnABOriginal");
const btnABMix = document.getElementById("btnABMix");
const btnABModel = document.getElementById("btnABModel");
const audioCrossfader = document.getElementById("audioCrossfader");

const soloOrig = document.getElementById("soloOrig");
const muteOrig = document.getElementById("muteOrig");
const volOrig = document.getElementById("volOrig");

const soloModel = document.getElementById("soloModel");
const muteModel = document.getElementById("muteModel");
const volModel = document.getElementById("volModel");

const btnPlayPause = document.getElementById("btnPlayPause");
const btnBack5 = document.getElementById("btnBack5");
const btnFwd5 = document.getElementById("btnFwd5");
const playbackSpeed = document.getElementById("playbackSpeed");
const playerTime = document.getElementById("playerTime");

const transcriptCard = document.getElementById("transcriptCard");
const transcriptFeed = document.getElementById("transcriptFeed");
const speakerFilters = document.getElementById("speakerFilters");
const reviewFilterPill = document.getElementById("reviewFilterPill");
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

// --- 2. VRAM & System RAM Monitoring ---
async function fetchMemoryStats() {
  try {
    const res = await fetch("/api/vram");
    if (!res.ok) return;
    const data = await res.json();
    
    // System RAM
    if (data.sys_ram_total_gb > 0) {
      ramBarFill.style.width = `${data.sys_ram_percent}%`;
      ramText.innerText = `${data.sys_ram_used_gb} / ${data.sys_ram_total_gb} GB (${data.sys_ram_percent}%)`;
    }

    // GPU VRAM
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
setInterval(fetchMemoryStats, 2500);
fetchMemoryStats();


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

  btnPause.style.display = "inline-block";
  btnResume.style.display = "none";
  btnStop.style.display = "inline-block";

  const formData = new FormData();
  formData.append("audio", selectedFile);
  formData.append("diarizer", diarizerSelect.value);
  formData.append("speaker_labels", speakerLabelsCheckbox.checked);
  formData.append("enable_enhancer", voiceEnhancerCheckbox ? voiceEnhancerCheckbox.checked : true);
  formData.append("enable_ambiguity", ambiguityCheckbox ? ambiguityCheckbox.checked : true);

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

btnPause.addEventListener("click", async () => {
  if (!currentTaskId) return;
  try {
    await fetch(`/api/tasks/${currentTaskId}/pause`, { method: "POST" });
    btnPause.style.display = "none";
    btnResume.style.display = "inline-block";
    progressStatus.innerText = "Paused by user";
  } catch (err) {
    console.error("Failed to pause:", err);
  }
});

btnResume.addEventListener("click", async () => {
  if (!currentTaskId) return;
  try {
    await fetch(`/api/tasks/${currentTaskId}/resume`, { method: "POST" });
    btnResume.style.display = "none";
    btnPause.style.display = "inline-block";
    progressStatus.innerText = "Resuming...";
  } catch (err) {
    console.error("Failed to resume:", err);
  }
});

btnStop.addEventListener("click", async () => {
  if (!currentTaskId) return;
  if (!confirm("Are you sure you want to cancel and stop this transcription?")) return;
  try {
    await fetch(`/api/tasks/${currentTaskId}/stop`, { method: "POST" });
    if (pollTimer) clearInterval(pollTimer);
    progressStatus.innerText = "Stopped";
    progressCard.style.display = "none";
    btnStart.disabled = false;
    currentTaskId = null;
  } catch (err) {
    console.error("Failed to stop:", err);
  }
});

async function pollTaskStatus(taskId) {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === "paused") {
        progressStatus.innerText = "Paused";
        btnPause.style.display = "none";
        btnResume.style.display = "inline-block";
      } else {
        progressStatus.innerText = data.message || "Processing...";
      }

      progressFill.style.width = `${data.progress}%`;
      progressPercentage.innerText = `${Math.round(data.progress)}%`;

      if (data.status === "completed") {
        clearInterval(pollTimer);
        progressCard.style.display = "none";
        btnStart.disabled = false;
        onTranscriptionSuccess(data);
      } else if (data.status === "stopped") {
        clearInterval(pollTimer);
        progressCard.style.display = "none";
        btnStart.disabled = false;
      } else if (data.status === "failed") {
        clearInterval(pollTimer);
        alert("Transcription failed: " + data.message);
        btnStart.disabled = false;
        progressCard.style.display = "none";
      }
    } catch (e) {
      // Continue polling
    }
  }, 1000);
}


// --- 5. Dual-Track Audio Studio & Ingest Comparison ---
function onTranscriptionSuccess(data) {
  currentSegments = data.segments;
  initAudioPlayer(data.id);
  renderSpeakerFilters();
  renderTranscriptFeed();
  playerCard.style.display = "block";
  transcriptCard.style.display = "block";
}

function getWaveThemeColors() {
  const currentTheme = document.body.dataset.theme;
  if (currentTheme === "forest-sage") {
    return {
      origWave: '#1e2825', origProgress: '#527568',
      modelWave: '#162b1e', modelProgress: '#4a825b'
    };
  } else if (currentTheme === "nordic-slate") {
    return {
      origWave: '#1d232e', origProgress: '#4d6980',
      modelWave: '#1b2a26', modelProgress: '#4a7566'
    };
  } else if (currentTheme === "warm-umber") {
    return {
      origWave: '#2a221e', origProgress: '#70584b',
      modelWave: '#26241b', modelProgress: '#636647'
    };
  } else {
    // foggy-woodland
    return {
      origWave: '#1a232b', origProgress: '#46677d',
      modelWave: '#162219', modelProgress: '#4f7556'
    };
  }
}

function initAudioPlayer(taskId) {
  if (wavesurferOrig) wavesurferOrig.destroy();
  if (wavesurferModel) wavesurferModel.destroy();

  const colors = getWaveThemeColors();

  // Track 1: Original Audio
  wavesurferOrig = WaveSurfer.create({
    container: '#waveformOrig',
    waveColor: colors.origWave,
    progressColor: colors.origProgress,
    cursorColor: '#7a8c99',
    height: 52,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    url: `/api/audio/${taskId}`
  });

  // Track 2: Model Classification Audio
  wavesurferModel = WaveSurfer.create({
    container: '#waveformModel',
    waveColor: colors.modelWave,
    progressColor: colors.modelProgress,
    cursorColor: '#748c7c',
    height: 52,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    url: `/api/audio/${taskId}/processed`
  });

  // Lockstep seeking synchronization
  wavesurferOrig.on('seeking', (time) => {
    if (!isSeekingSync && wavesurferModel) {
      isSeekingSync = true;
      wavesurferModel.setTime(time);
      isSeekingSync = false;
    }
  });

  wavesurferModel.on('seeking', (time) => {
    if (!isSeekingSync && wavesurferOrig) {
      isSeekingSync = true;
      wavesurferOrig.setTime(time);
      isSeekingSync = false;
    }
  });

  // Timeupdate, playhead sync, and drift correction
  wavesurferOrig.on('timeupdate', (currentTime) => {
    updatePlaybackTime(currentTime, wavesurferOrig.getDuration());
    syncActiveSegment(currentTime);

    if (wavesurferModel && wavesurferOrig.isPlaying()) {
      const diff = Math.abs(currentTime - wavesurferModel.getCurrentTime());
      if (diff > 0.06) {
        wavesurferModel.setTime(currentTime);
      }
    }
  });

  // Play / Pause event handlers
  wavesurferOrig.on('play', () => {
    btnPlayPause.innerText = "⏸ Pause";
    if (wavesurferModel && !wavesurferModel.isPlaying()) {
      wavesurferModel.play();
    }
  });

  wavesurferOrig.on('pause', () => {
    btnPlayPause.innerText = "▶ Play Both";
    if (wavesurferModel && wavesurferModel.isPlaying()) {
      wavesurferModel.pause();
    }
  });

  wavesurferModel.on('play', () => {
    if (wavesurferOrig && !wavesurferOrig.isPlaying()) {
      wavesurferOrig.play();
    }
  });

  wavesurferModel.on('pause', () => {
    if (wavesurferOrig && wavesurferOrig.isPlaying()) {
      wavesurferOrig.pause();
    }
  });

  // Setup initial mix levels
  wavesurferOrig.on('ready', () => updateMixLevels());
  wavesurferModel.on('ready', () => updateMixLevels());
}

function updateWaveformTheme() {
  const colors = getWaveThemeColors();
  if (wavesurferOrig) {
    wavesurferOrig.setOptions({ waveColor: colors.origWave, progressColor: colors.origProgress });
  }
  if (wavesurferModel) {
    wavesurferModel.setOptions({ waveColor: colors.modelWave, progressColor: colors.modelProgress });
  }
}

// Master Transport Controls
btnPlayPause.addEventListener("click", () => {
  if (!wavesurferOrig) return;
  if (wavesurferOrig.isPlaying()) {
    wavesurferOrig.pause();
    if (wavesurferModel) wavesurferModel.pause();
  } else {
    wavesurferOrig.play();
    if (wavesurferModel) wavesurferModel.play();
  }
});

btnBack5.addEventListener("click", () => {
  if (!wavesurferOrig) return;
  const newTime = Math.max(0, wavesurferOrig.getCurrentTime() - 5);
  wavesurferOrig.setTime(newTime);
  if (wavesurferModel) wavesurferModel.setTime(newTime);
});

btnFwd5.addEventListener("click", () => {
  if (!wavesurferOrig) return;
  const newTime = Math.min(wavesurferOrig.getDuration(), wavesurferOrig.getCurrentTime() + 5);
  wavesurferOrig.setTime(newTime);
  if (wavesurferModel) wavesurferModel.setTime(newTime);
});

playbackSpeed.addEventListener("change", (e) => {
  const rate = parseFloat(e.target.value);
  if (wavesurferOrig) wavesurferOrig.setPlaybackRate(rate);
  if (wavesurferModel) wavesurferModel.setPlaybackRate(rate);
});

// Mix & Match Audio Engine
function updateMixLevels() {
  if (!wavesurferOrig || !wavesurferModel) return;

  const cross = parseFloat(audioCrossfader.value); // 0 (100% orig) to 100 (100% model)
  const origFactor = Math.cos((cross / 100) * (Math.PI / 2));
  const modelFactor = Math.sin((cross / 100) * (Math.PI / 2));

  let finalOrigVol = volumeState.orig * origFactor;
  let finalModelVol = volumeState.model * modelFactor;

  // Solo handling
  if (soloState.orig && !soloState.model) {
    finalOrigVol = volumeState.orig;
    finalModelVol = 0;
  } else if (soloState.model && !soloState.orig) {
    finalModelVol = volumeState.model;
    finalOrigVol = 0;
  }

  // Mute handling
  if (muteState.orig) finalOrigVol = 0;
  if (muteState.model) finalModelVol = 0;

  wavesurferOrig.setVolume(finalOrigVol);
  wavesurferModel.setVolume(finalModelVol);
}

function setCrossfade(val) {
  audioCrossfader.value = val;
  btnABOriginal.classList.toggle("active", val === 0);
  btnABMix.classList.toggle("active", val === 50);
  btnABModel.classList.toggle("active", val === 100);
  soloState.orig = false;
  soloState.model = false;
  soloOrig.classList.remove("active");
  soloModel.classList.remove("active");
  updateMixLevels();
}

btnABOriginal.addEventListener("click", () => setCrossfade(0));
btnABMix.addEventListener("click", () => setCrossfade(50));
btnABModel.addEventListener("click", () => setCrossfade(100));

audioCrossfader.addEventListener("input", () => {
  const val = parseInt(audioCrossfader.value, 10);
  btnABOriginal.classList.toggle("active", val <= 10);
  btnABMix.classList.toggle("active", val > 40 && val < 60);
  btnABModel.classList.toggle("active", val >= 90);
  updateMixLevels();
});

// Channel Strip Controls
soloOrig.addEventListener("click", () => {
  soloState.orig = !soloState.orig;
  if (soloState.orig) soloState.model = false;
  soloOrig.classList.toggle("active", soloState.orig);
  soloModel.classList.toggle("active", soloState.model);
  updateMixLevels();
});

muteOrig.addEventListener("click", () => {
  muteState.orig = !muteState.orig;
  muteOrig.classList.toggle("active", muteState.orig);
  updateMixLevels();
});

volOrig.addEventListener("input", (e) => {
  volumeState.orig = parseFloat(e.target.value);
  updateMixLevels();
});

soloModel.addEventListener("click", () => {
  soloState.model = !soloState.model;
  if (soloState.model) soloState.orig = false;
  soloModel.classList.toggle("active", soloState.model);
  soloOrig.classList.toggle("active", soloState.orig);
  updateMixLevels();
});

muteModel.addEventListener("click", () => {
  muteState.model = !muteState.model;
  muteModel.classList.toggle("active", muteState.model);
  updateMixLevels();
});

volModel.addEventListener("input", (e) => {
  volumeState.model = parseFloat(e.target.value);
  updateMixLevels();
});

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

    if (activeSpeakerFilter === "REVIEW") {
      if (!seg.needs_review) return;
    } else if (activeSpeakerFilter !== "ALL" && rawSpeaker !== activeSpeakerFilter) {
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

    let badgeExtras = "";
    if (seg.slowed_audio_used) {
      badgeExtras += `<span class="badge-slowed" title="Auto-slowed to 0.75x for acoustic clarification">🐢 0.75x</span>`;
    }
    if (seg.needs_review) {
      badgeExtras += `<span class="badge-review" title="High ambiguity persisted after slowdown. Review recommended.">⚠️ Needs Review</span>`;
    }

    // Audition button for chunk classification sample
    let auditionBtn = "";
    if (seg.slowed_audio_used) {
      auditionBtn = `<button class="btn-audition" data-slow="true" title="Audition exact 0.75x time-stretched audio sample evaluated by model">🐢 0.75x Sample</button>`;
    } else {
      auditionBtn = `<button class="btn-audition" data-slow="false" title="Audition this segment in Model Classification Ingest">🎧 Model Ingest</button>`;
    }

    block.innerHTML = `
      <div class="segment-header">
        <span class="speaker-badge ${spkClass}">${displayName}</span>
        <span class="timestamp-pill">[${formatSeconds(seg.start)} - ${formatSeconds(seg.end)}]</span>
        ${badgeExtras}
        ${auditionBtn}
      </div>
      <div class="segment-text" contenteditable="true" spellcheck="false">${seg.text}</div>
    `;

    // Audition chunk button logic
    const audBtn = block.querySelector(".btn-audition");
    if (audBtn) {
      audBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const isSlow = audBtn.dataset.slow === "true";
        if (isSlow) {
          if (chunkAuditionAudio) chunkAuditionAudio.pause();
          chunkAuditionAudio = new Audio(`/api/audio/${currentTaskId}/chunk/${index}`);
          audBtn.innerText = "🔊 Playing 0.75x...";
          chunkAuditionAudio.play().catch(err => console.warn(err));
          chunkAuditionAudio.onended = () => {
            audBtn.innerText = "🐢 0.75x Sample";
          };
        } else {
          // Switch to Model Ingested audio and play segment
          setCrossfade(100);
          if (wavesurferOrig) {
            wavesurferOrig.setTime(seg.start);
            if (wavesurferModel) wavesurferModel.setTime(seg.start);
            wavesurferOrig.play();
            if (wavesurferModel) wavesurferModel.play();
          }
        }
      });
    }

    // Click block or timestamp to jump both players
    block.addEventListener("click", (e) => {
      if (e.target.classList.contains("segment-text") || e.target.classList.contains("btn-audition")) return;
      if (wavesurferOrig) {
        wavesurferOrig.setTime(seg.start);
        if (wavesurferModel) wavesurferModel.setTime(seg.start);
        wavesurferOrig.play();
        if (wavesurferModel) wavesurferModel.play();
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

  // Review Filter Pill (Minimal Human Intervention Queue)
  const reviewCount = currentSegments.filter(s => s.needs_review).length;
  if (reviewCount > 0) {
    const reviewPill = document.createElement("div");
    reviewPill.className = `filter-pill filter-review ${activeSpeakerFilter === 'REVIEW' ? 'active' : ''}`;
    reviewPill.innerText = `⚠️ Needs Review (${reviewCount})`;
    reviewPill.addEventListener("click", () => {
      activeSpeakerFilter = "REVIEW";
      renderSpeakerFilters();
      renderTranscriptFeed();
    });
    speakerFilters.appendChild(reviewPill);
  }

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
