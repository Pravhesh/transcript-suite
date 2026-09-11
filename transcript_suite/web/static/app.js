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
const appRamText = document.getElementById("appRamText");

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
    
    // App RAM (Process RSS)
    if (appRamText && data.proc_ram_used_gb !== undefined) {
      appRamText.innerText = `${data.proc_ram_used_gb.toFixed(2)} GB`;
    }

    // System RAM
    if (data.sys_ram_total_gb > 0) {
      ramBarFill.style.width = `${data.sys_ram_percent}%`;
      ramText.innerText = `${data.sys_ram_used_gb} / ${data.sys_ram_total_gb} GB`;
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
  const councilCheckbox = document.getElementById("councilCheckbox");
  formData.append("enable_council", councilCheckbox ? councilCheckbox.checked : true);

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

      // Live online per-segment rendering as chunks complete
      if (data.segments && data.segments.length > 0) {
        if (currentSegments.length !== data.segments.length) {
          currentSegments = data.segments;
          transcriptCard.style.display = "block";
          renderSpeakerFilters();
          renderTranscriptFeed();
        }
      }

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

  // Lockstep seeking synchronization with async debounce guard
  wavesurferOrig.on('seeking', (time) => {
    if (!isSeekingSync && wavesurferModel) {
      isSeekingSync = true;
      wavesurferModel.setTime(time);
      setTimeout(() => { isSeekingSync = false; }, 50);
    }
  });

  wavesurferModel.on('seeking', (time) => {
    if (!isSeekingSync && wavesurferOrig) {
      isSeekingSync = true;
      wavesurferOrig.setTime(time);
      setTimeout(() => { isSeekingSync = false; }, 50);
    }
  });

  // Timeupdate, playhead sync, and drift correction
  wavesurferOrig.on('timeupdate', (currentTime) => {
    updatePlaybackTime(currentTime, wavesurferOrig.getDuration());
    syncActiveSegment(currentTime);

    // Only re-align if significant drift occurs (>0.35s) to avoid buffer stutter
    if (wavesurferModel && wavesurferOrig.isPlaying() && !isSeekingSync) {
      const diff = Math.abs(currentTime - wavesurferModel.getCurrentTime());
      if (diff > 0.35) {
        isSeekingSync = true;
        wavesurferModel.setTime(currentTime);
        setTimeout(() => { isSeekingSync = false; }, 60);
      }
    }
  });

  // Play / Pause event handlers with initial lockstep alignment
  wavesurferOrig.on('play', () => {
    btnPlayPause.innerText = "⏸ Pause";
    if (wavesurferModel) {
      const t = wavesurferOrig.getCurrentTime();
      if (Math.abs(wavesurferModel.getCurrentTime() - t) > 0.06 && !isSeekingSync) {
        isSeekingSync = true;
        wavesurferModel.setTime(t);
        setTimeout(() => { isSeekingSync = false; }, 40);
      }
      if (!wavesurferModel.isPlaying()) {
        wavesurferModel.play();
      }
    }
  });

  wavesurferOrig.on('pause', () => {
    btnPlayPause.innerText = "▶ Play Both";
    if (wavesurferModel && wavesurferModel.isPlaying()) {
      wavesurferModel.pause();
    }
  });

  wavesurferModel.on('play', () => {
    if (wavesurferOrig) {
      const t = wavesurferModel.getCurrentTime();
      if (Math.abs(wavesurferOrig.getCurrentTime() - t) > 0.06 && !isSeekingSync) {
        isSeekingSync = true;
        wavesurferOrig.setTime(t);
        setTimeout(() => { isSeekingSync = false; }, 40);
      }
      if (!wavesurferOrig.isPlaying()) {
        wavesurferOrig.play();
      }
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

const expandedCouncilSet = new Set();

function renderTranscriptFeed() {
  transcriptFeed.innerHTML = "";
  const query = searchInput.value.toLowerCase();

  currentSegments.forEach((seg, index) => {
    const rawSpeaker = seg.speaker || "Speaker 0";
    const displayName = speakerAliases[rawSpeaker] || rawSpeaker;

    if (activeSpeakerFilter === "REVIEW") {
      if (!seg.needs_review) return;
    } else if (activeSpeakerFilter === "DISPUTED") {
      if (!seg.council || seg.council.agreement_type !== "SPLIT_DECISION") return;
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

    // Council deliberation badge & drawer
    let councilBadge = "";
    let councilToggleBtn = "";
    let councilDrawerHtml = "";

    if (seg.council) {
      const agreeType = seg.council.agreement_type || "MAJORITY";
      const scorePct = Math.round((seg.council.consensus_score || 0.85) * 100);
      const isExpanded = expandedCouncilSet.has(index);

      if (agreeType === "UNANIMOUS") {
        councilBadge = `<span class="badge-council badge-council-unanimous" title="${seg.council.deliberation_notes || 'All models agreed'}">⚖️ 100% Unanimous</span>`;
      } else if (agreeType === "CTC_ANCHORED") {
        councilBadge = `<span class="badge-council badge-council-ctc" title="${seg.council.deliberation_notes || 'Non-speech verified by CTC'}">⚓ CTC Anchored</span>`;
      } else if (agreeType === "MAJORITY") {
        councilBadge = `<span class="badge-council badge-council-majority" title="${seg.council.deliberation_notes || 'Majority consensus'}">⚖️ Majority (${scorePct}%)</span>`;
      } else {
        councilBadge = `<span class="badge-council badge-council-split" title="${seg.council.deliberation_notes || 'Split decision across jurors'}">⚖️ Split Decision</span>`;
      }

      councilToggleBtn = `<button class="btn-council-toggle ${isExpanded ? 'active' : ''}" data-index="${index}">🏛️ Jury (${seg.council.votes ? seg.council.votes.length : 3}) ${isExpanded ? '▴' : '▾'}</button>`;

      if (isExpanded) {
        let votesHtml = "";
        (seg.council.votes || []).forEach((v) => {
          let roleIcon = "🤖";
          if (v.role && v.role.includes("Lead")) roleIcon = "🏛️";
          else if (v.role && v.role.includes("Anchor")) roleIcon = "⚓";
          else if (v.role && v.role.includes("Auditor")) roleIcon = "🐢";

          const confPct = Math.round((v.confidence || 0.8) * 100);
          votesHtml += `
            <div class="council-vote-row">
              <div class="council-vote-meta">
                <span>${roleIcon}</span>
                <span class="council-vote-member">${v.member}</span>
                <span class="council-vote-role">(${v.role})</span>
                <span class="badge-stage" style="font-size: 10px;">${confPct}%</span>
              </div>
              <div class="council-vote-text" title="${v.hypothesis}">"${v.hypothesis || '— [silence] —'}"</div>
              ${v.hypothesis ? `<button class="btn-adopt-hyp" data-seg="${index}" data-text="${encodeURIComponent(v.hypothesis)}" title="Adopt this juror's hypothesis for this segment">Adopt</button>` : ''}
            </div>
          `;
        });

        let disputedHtml = "";
        if (seg.council.disputed_tokens && seg.council.disputed_tokens.length > 0) {
          const chips = seg.council.disputed_tokens.map(t => `<span class="council-token-chip">${t}</span>`).join(" ");
          disputedHtml = `
            <div class="council-disputed-bar">
              <span class="council-disputed-label">Disputed Words:</span>
              ${chips}
            </div>
          `;
        }

        councilDrawerHtml = `
          <div class="council-drawer">
            <div class="council-drawer-header">
              <span>🏛️ Council Deliberation (${agreeType})</span>
              <span style="font-size: 11px; color: var(--text-muted);">${seg.council.consensus_score ? `Consensus: ${scorePct}%` : ''}</span>
            </div>
            <div class="council-votes-list">
              ${votesHtml}
            </div>
            ${disputedHtml}
            <div class="council-notes-text">📝 ${seg.council.deliberation_notes || ''}</div>
          </div>
        `;
      }
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
        ${councilBadge}
        ${councilToggleBtn}
        ${auditionBtn}
      </div>
      <div class="segment-text" contenteditable="true" spellcheck="false">${seg.text}</div>
      ${councilDrawerHtml}
    `;

    // Toggle Council Drawer button
    const cToggle = block.querySelector(".btn-council-toggle");
    if (cToggle) {
      cToggle.addEventListener("click", (e) => {
        e.stopPropagation();
        const segIdx = parseInt(cToggle.dataset.index, 10);
        if (expandedCouncilSet.has(segIdx)) {
          expandedCouncilSet.delete(segIdx);
        } else {
          expandedCouncilSet.add(segIdx);
        }
        renderTranscriptFeed();
      });
    }

    // Adopt Juror Hypothesis buttons
    block.querySelectorAll(".btn-adopt-hyp").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const segIdx = parseInt(btn.dataset.seg, 10);
        const adoptedText = decodeURIComponent(btn.dataset.text);
        if (currentSegments[segIdx]) {
          currentSegments[segIdx].text = adoptedText;
          renderTranscriptFeed();
        }
      });
    });

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
      if (e.target.classList.contains("segment-text") || e.target.classList.contains("btn-audition") || e.target.classList.contains("btn-council-toggle") || e.target.classList.contains("btn-adopt-hyp")) return;
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

  // Council Disputed Filter Pill
  const disputedCount = currentSegments.filter(s => s.council && s.council.agreement_type === 'SPLIT_DECISION').length;
  if (disputedCount > 0) {
    const dispPill = document.createElement("div");
    dispPill.className = `filter-pill filter-disputed ${activeSpeakerFilter === 'DISPUTED' ? 'active' : ''}`;
    dispPill.innerText = `⚖️ Council Disputed (${disputedCount})`;
    dispPill.addEventListener("click", () => {
      activeSpeakerFilter = "DISPUTED";
      renderSpeakerFilters();
      renderTranscriptFeed();
    });
    speakerFilters.appendChild(dispPill);
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

// Helper: Escape HTML
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ==========================================================================
// 10. Live Memory Trace & Execution Log Console
// ==========================================================================

let telemetryCadenceSeconds = 2;
let telemetryTimer = null;
let activeLogFilter = "ALL";
let cachedTraceSamples = [];
let cachedLogEntries = [];

// DOM Elements
const tabBtnTrace = document.getElementById("tabBtnTrace");
const tabBtnLogs = document.getElementById("tabBtnLogs");
const paneTrace = document.getElementById("paneTrace");
const paneLogs = document.getElementById("paneLogs");
const cadenceBtnGroup = document.getElementById("cadenceBtnGroup");
const btnExportTraceCsv = document.getElementById("btnExportTraceCsv");
const btnExportLogsCsv = document.getElementById("btnExportLogsCsv");
const btnExportLogsTxt = document.getElementById("btnExportLogsTxt");
const btnClearTelemetry = document.getElementById("btnClearTelemetry");

const traceActiveTask = document.getElementById("traceActiveTask");
const traceCurrentStage = document.getElementById("traceCurrentStage");
const traceLiveAppRam = document.getElementById("traceLiveAppRam");
const traceLiveRam = document.getElementById("traceLiveRam");
const traceLiveVram = document.getElementById("traceLiveVram");
const tracePeakMem = document.getElementById("tracePeakMem");
const traceTableBody = document.getElementById("traceTableBody");
const traceTableContainer = document.getElementById("traceTableContainer");
const traceAutoScroll = document.getElementById("traceAutoScroll");
const traceCountText = document.getElementById("traceCountText");

const logLevelFilters = document.getElementById("logLevelFilters");
const logSearchInput = document.getElementById("logSearchInput");
const terminalBody = document.getElementById("terminalBody");
const terminalContainer = document.getElementById("terminalContainer");
const logsAutoScroll = document.getElementById("logsAutoScroll");
const logCountText = document.getElementById("logCountText");

// Tab Switching
if (tabBtnTrace && tabBtnLogs) {
  tabBtnTrace.addEventListener("click", () => {
    tabBtnTrace.classList.add("active");
    tabBtnLogs.classList.remove("active");
    paneTrace.classList.add("active");
    paneLogs.classList.remove("active");
  });

  tabBtnLogs.addEventListener("click", () => {
    tabBtnLogs.classList.add("active");
    tabBtnTrace.classList.remove("active");
    paneLogs.classList.add("active");
    paneTrace.classList.remove("active");
  });
}

// Cadence Selection: 2s, 5s, 10s
if (cadenceBtnGroup) {
  cadenceBtnGroup.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-interval");
    if (!btn) return;
    cadenceBtnGroup.querySelectorAll(".btn-interval").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    telemetryCadenceSeconds = parseInt(btn.dataset.interval, 10) || 2;
    startTelemetryPolling();
    fetchTelemetryData();
  });
}

// Log Level Filter Buttons
if (logLevelFilters) {
  logLevelFilters.addEventListener("click", (e) => {
    const btn = e.target.closest(".log-filter-btn");
    if (!btn) return;
    logLevelFilters.querySelectorAll(".log-filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeLogFilter = btn.dataset.level || "ALL";
    renderLogs();
  });
}

if (logSearchInput) {
  logSearchInput.addEventListener("input", () => {
    renderLogs();
  });
}

// Polling Loop
function startTelemetryPolling() {
  if (telemetryTimer) clearInterval(telemetryTimer);
  telemetryTimer = setInterval(fetchTelemetryData, telemetryCadenceSeconds * 1000);
}

async function fetchTelemetryData() {
  try {
    const taskIdParam = currentTaskId ? `&task_id=${currentTaskId}` : "";

    // Fetch Trace
    const traceRes = await fetch(`/api/telemetry/trace?interval=${telemetryCadenceSeconds}${taskIdParam}`);
    if (traceRes.ok) {
      const data = await traceRes.json();
      cachedTraceSamples = data.samples || [];
      renderTrace(data);
    }

    // Fetch Logs
    const logsRes = await fetch(`/api/telemetry/logs?limit=400${taskIdParam}`);
    if (logsRes.ok) {
      const data = await logsRes.json();
      cachedLogEntries = data.logs || [];
      renderLogs();
    }
  } catch (err) {
    // Silent fail
  }
}

function renderTrace(data) {
  const current = data.current || {};
  const activeTask = data.active_task;

  // Update Summary Bar
  if (traceActiveTask) {
    if (activeTask) {
      traceActiveTask.innerHTML = `<strong>${escapeHtml(activeTask.filename || activeTask.id.slice(0, 8))}</strong> (${escapeHtml(activeTask.status)})`;
    } else {
      traceActiveTask.innerText = "System Idle";
    }
  }

  if (traceCurrentStage) {
    traceCurrentStage.innerText = activeTask ? (activeTask.stage || "In progress") : "Ready";
  }

  if (traceLiveAppRam) {
    traceLiveAppRam.innerText = `${(current.proc_ram_used_gb || 0).toFixed(2)} GB`;
  }
  if (traceLiveRam && current.sys_ram_total_gb > 0) {
    traceLiveRam.innerText = `${current.sys_ram_used_gb} / ${current.sys_ram_total_gb} GB (${current.sys_ram_percent}%)`;
  }
  if (traceLiveVram) {
    if (current.available) {
      traceLiveVram.innerText = `${current.allocated_gb || 0} alloc / ${current.reserved_gb} res / ${current.total_gb} GB`;
    } else {
      traceLiveVram.innerText = "CPU Mode";
    }
  }

  // Calculate Peaks
  let peakAppRam = 0;
  let peakRam = 0;
  let peakVram = 0;
  cachedTraceSamples.forEach(s => {
    if ((s.proc_ram_used_gb || 0) > peakAppRam) peakAppRam = s.proc_ram_used_gb;
    if (s.ram_used_gb > peakRam) peakRam = s.ram_used_gb;
    if (s.vram_alloc_gb > peakVram) peakVram = s.vram_alloc_gb;
    if (s.vram_reserved_gb > peakVram) peakVram = s.vram_reserved_gb;
  });
  if (tracePeakMem) {
    tracePeakMem.innerText = `${peakVram.toFixed(2)} GB VRAM / ${peakAppRam.toFixed(2)} GB App (${peakRam.toFixed(1)} GB Sys)`;
  }

  // Render Table Rows
  if (traceCountText) {
    traceCountText.innerText = `${data.total_recorded || cachedTraceSamples.length} samples recorded (${telemetryCadenceSeconds}s cadence)`;
  }

  if (traceTableBody) {
    traceTableBody.innerHTML = cachedTraceSamples.map(s => {
      let statusClass = "status-idle";
      const st = (s.status || "").toLowerCase();
      if (st === "processing") statusClass = "status-processing";
      else if (st === "completed") statusClass = "status-completed";
      else if (st === "paused") statusClass = "status-paused";
      else if (st === "stopped") statusClass = "status-stopped";

      const elapsedFmt = s.elapsed_s ? `${s.elapsed_s.toFixed(1)}s` : "--";
      const taskDisplay = s.task_name && s.task_name !== "System Idle" ? s.task_name : (s.task_id && s.task_id !== "idle" ? s.task_id.slice(0, 8) : "Idle");

      return `
        <tr>
          <td style="color: var(--text-muted);">${s.time_str || (s.timestamp ? s.timestamp.slice(11, 19) : '')}</td>
          <td style="color: var(--text-secondary); font-weight: 500;">${elapsedFmt}</td>
          <td title="${s.task_id || ''}" style="max-width: 160px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(taskDisplay)}</td>
          <td style="max-width: 220px; overflow: hidden; text-overflow: ellipsis; color: var(--text-primary);">${escapeHtml(s.stage || '')}</td>
          <td style="color: var(--accent-light); font-weight: 600;">${(s.proc_ram_used_gb || 0).toFixed(2)} GB</td>
          <td><strong>${s.ram_used_gb.toFixed(2)}</strong> / ${s.ram_total_gb.toFixed(1)} GB</td>
          <td><strong>${s.vram_alloc_gb.toFixed(2)}</strong> / ${s.vram_total_gb.toFixed(1)} GB</td>
          <td><span class="trace-status-pill ${statusClass}">${escapeHtml(s.status || 'idle')}</span></td>
        </tr>
      `;
    }).join("");

    if (traceAutoScroll && traceAutoScroll.checked && traceTableContainer) {
      traceTableContainer.scrollTop = traceTableContainer.scrollHeight;
    }
  }
}

function renderLogs() {
  if (!terminalBody) return;
  const query = (logSearchInput ? logSearchInput.value : "").trim().toLowerCase();

  const filtered = cachedLogEntries.filter(l => {
    if (activeLogFilter !== "ALL" && (l.level || "").toUpperCase() !== activeLogFilter) {
      return false;
    }
    if (query) {
      const matchMsg = (l.message || "").toLowerCase().includes(query);
      const matchLevel = (l.level || "").toLowerCase().includes(query);
      const matchTask = (l.task_id || "").toLowerCase().includes(query);
      return matchMsg || matchLevel || matchTask;
    }
    return true;
  });

  if (logCountText) {
    logCountText.innerText = `${filtered.length} of ${cachedLogEntries.length} log entries`;
  }

  terminalBody.innerHTML = filtered.map(l => {
    const lvl = (l.level || "INFO").toUpperCase();
    let badgeClass = "badge-info";
    if (lvl === "STAGE") badgeClass = "badge-stage";
    else if (lvl === "CHUNK") badgeClass = "badge-chunk";
    else if (lvl === "MEM") badgeClass = "badge-mem";
    else if (lvl === "SUCCESS") badgeClass = "badge-success";
    else if (lvl === "WARN") badgeClass = "badge-warn";
    else if (lvl === "ERROR") badgeClass = "badge-error";

    const memFmt = (l.ram_used_gb > 0) ? `RAM: ${l.ram_used_gb.toFixed(2)}G | VRAM: ${l.vram_alloc_gb.toFixed(2)}G` : "";

    return `
      <div class="log-entry">
        <span class="log-time">[${l.time_str || (l.timestamp ? l.timestamp.slice(11, 19) : '')}]</span>
        <span class="log-badge ${badgeClass}">${escapeHtml(lvl)}</span>
        <span class="log-msg">${escapeHtml(l.message || '')}</span>
        ${memFmt ? `<span class="log-mem-info">${memFmt}</span>` : ''}
      </div>
    `;
  }).join("");

  if (logsAutoScroll && logsAutoScroll.checked && terminalContainer) {
    terminalContainer.scrollTop = terminalContainer.scrollHeight;
  }
}

// Client-side helper for download triggers
function downloadBlob(content, filename, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Export Trace as CSV
if (btnExportTraceCsv) {
  btnExportTraceCsv.addEventListener("click", async () => {
    try {
      const taskIdParam = currentTaskId ? `?task_id=${currentTaskId}` : "";
      const res = await fetch(`/api/telemetry/export/trace.csv${taskIdParam}`);
      if (res.ok) {
        const csvText = await res.text();
        const filename = `memory_trace_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "_")}.csv`;
        downloadBlob(csvText, filename, "text/csv");
        return;
      }
    } catch (err) {
      // Fallback
    }

    // Fallback client generation
    const headers = "Timestamp,Time,Elapsed_Sec,Task_ID,File_Name,Status,Stage,RAM_Used_GB,RAM_Total_GB,RAM_Percent,VRAM_Alloc_GB,VRAM_Reserved_GB,VRAM_Total_GB,VRAM_Percent";
    const rows = cachedTraceSamples.map(s => [
      `"${s.timestamp || ''}"`,
      `"${s.time_str || ''}"`,
      s.elapsed_s || 0,
      `"${s.task_id || ''}"`,
      `"${(s.task_name || '').replace(/"/g, '""')}"`,
      `"${s.status || ''}"`,
      `"${(s.stage || '').replace(/"/g, '""')}"`,
      s.ram_used_gb || 0,
      s.ram_total_gb || 0,
      s.ram_pct || 0,
      s.vram_alloc_gb || 0,
      s.vram_reserved_gb || 0,
      s.vram_total_gb || 0,
      s.vram_pct || 0
    ].join(","));
    downloadBlob([headers, ...rows].join("\n"), `memory_trace_${Date.now()}.csv`, "text/csv");
  });
}

// Export Logs as CSV
if (btnExportLogsCsv) {
  btnExportLogsCsv.addEventListener("click", async () => {
    try {
      const taskIdParam = currentTaskId ? `?task_id=${currentTaskId}` : "";
      const res = await fetch(`/api/telemetry/export/logs.csv${taskIdParam}`);
      if (res.ok) {
        const csvText = await res.text();
        const filename = `execution_logs_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "_")}.csv`;
        downloadBlob(csvText, filename, "text/csv");
        return;
      }
    } catch (err) {}

    // Fallback client generation
    const headers = "Timestamp,Time,Level,Task_ID,Message,RAM_Used_GB,RAM_Total_GB,RAM_Percent,VRAM_Alloc_GB,VRAM_Reserved_GB,VRAM_Total_GB,VRAM_Percent";
    const rows = cachedLogEntries.map(l => [
      `"${l.timestamp || ''}"`,
      `"${l.time_str || ''}"`,
      `"${l.level || ''}"`,
      `"${l.task_id || ''}"`,
      `"${(l.message || '').replace(/"/g, '""')}"`,
      l.ram_used_gb || 0,
      l.ram_total_gb || 0,
      l.ram_pct || 0,
      l.vram_alloc_gb || 0,
      l.vram_reserved_gb || 0,
      l.vram_total_gb || 0,
      l.vram_pct || 0
    ].join(","));
    downloadBlob([headers, ...rows].join("\n"), `execution_logs_${Date.now()}.csv`, "text/csv");
  });
}

// Export Logs as TXT
if (btnExportLogsTxt) {
  btnExportLogsTxt.addEventListener("click", async () => {
    try {
      const taskIdParam = currentTaskId ? `?task_id=${currentTaskId}` : "";
      const res = await fetch(`/api/telemetry/export/logs.txt${taskIdParam}`);
      if (res.ok) {
        const txt = await res.text();
        const filename = `execution_logs_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "_")}.txt`;
        downloadBlob(txt, filename, "text/plain");
        return;
      }
    } catch (err) {}

    // Fallback client generation
    const lines = cachedLogEntries.map(l => `[${l.time_str || ''}] [${(l.level || 'INFO').padEnd(7)}] ${l.message} | RAM: ${l.ram_used_gb}GB, VRAM: ${l.vram_alloc_gb}GB`);
    downloadBlob(lines.join("\n"), `execution_logs_${Date.now()}.txt`, "text/plain");
  });
}

// Clear Telemetry
if (btnClearTelemetry) {
  btnClearTelemetry.addEventListener("click", async () => {
    if (!confirm("Clear live memory trace and execution logs?")) return;
    try {
      await fetch("/api/telemetry/clear", { method: "POST" });
    } catch (err) {}
    cachedTraceSamples = [];
    cachedLogEntries = [];
    if (traceTableBody) traceTableBody.innerHTML = "";
    if (terminalBody) terminalBody.innerHTML = "";
    if (traceCountText) traceCountText.innerText = "0 trace samples recorded";
    if (logCountText) logCountText.innerText = "0 log entries";
    fetchTelemetryData();
  });
}

// Proactive RAM & GPU Memory Cache Trimming
async function triggerMemoryClear(btnElement) {
  if (!btnElement) return;
  const origText = btnElement.innerText;
  btnElement.innerText = "🧹 Trimming...";
  btnElement.disabled = true;
  try {
    const res = await fetch("/api/memory/clear", { method: "POST" });
    if (res.ok) {
      btnElement.innerText = "✨ Memory Cleaned!";
      setTimeout(() => {
        btnElement.innerText = origText;
        btnElement.disabled = false;
      }, 1500);
      updateVRAM();
      fetchTelemetryData();
    } else {
      btnElement.innerText = "⚠️ Trim Failed";
      setTimeout(() => {
        btnElement.innerText = origText;
        btnElement.disabled = false;
      }, 1500);
    }
  } catch (e) {
    btnElement.innerText = "⚠️ Error";
    setTimeout(() => {
      btnElement.innerText = origText;
      btnElement.disabled = false;
    }, 1500);
  }
}

const btnClearMemHeader = document.getElementById("btnClearMemHeader");
if (btnClearMemHeader) {
  btnClearMemHeader.addEventListener("click", () => triggerMemoryClear(btnClearMemHeader));
}

const btnClearMemTelemetry = document.getElementById("btnClearMemTelemetry");
if (btnClearMemTelemetry) {
  btnClearMemTelemetry.addEventListener("click", () => triggerMemoryClear(btnClearMemTelemetry));
}

// Start Telemetry on Load
startTelemetryPolling();
fetchTelemetryData();

initTheme();

