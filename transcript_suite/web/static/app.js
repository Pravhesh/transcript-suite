/* ==========================================================================
   Transcript Suite - Client Application Logic (Supreme Council Studio)
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

// DOM Elements: Header & Metrics
const themeSelect = document.getElementById("themeSelect");
const vramMeter = document.getElementById("vramMeter");
const vramBarFill = document.getElementById("vramBarFill");
const vramText = document.getElementById("vramText");
const ramBarFill = document.getElementById("ramBarFill");
const ramText = document.getElementById("ramText");
const appRamText = document.getElementById("appRamText");
const sysWithoutSuiteText = document.getElementById("sysWithoutSuiteText");

// Primary Tab Navigation
const tabBtnStudio = document.getElementById("tabBtnStudio");
const tabBtnTranscript = document.getElementById("tabBtnTranscript");
const tabBtnTelemetry = document.getElementById("tabBtnTelemetry");
const tabTranscriptBadge = document.getElementById("tabTranscriptBadge");

const paneStudio = document.getElementById("paneStudio");
const paneTranscript = document.getElementById("paneTranscript");
const paneTelemetry = document.getElementById("paneTelemetry");

// Cache & Storage Dropdown Elements
const btnCacheDropdownToggle = document.getElementById("btnCacheDropdownToggle");
const cacheDropdownMenu = document.getElementById("cacheDropdownMenu");
const cacheVramVal = document.getElementById("cacheVramVal");
const cacheRamVal = document.getElementById("cacheRamVal");
const cacheTempAudioVal = document.getElementById("cacheTempAudioVal");
const cacheHfVal = document.getElementById("cacheHfVal");
const cacheNemoVal = document.getElementById("cacheNemoVal");
const cacheTotalDiskVal = document.getElementById("cacheTotalDiskVal");
const btnClearVramOnly = document.getElementById("btnClearVramOnly");
const btnClearRamOnly = document.getElementById("btnClearRamOnly");
const btnClearTempAudio = document.getElementById("btnClearTempAudio");
const btnFlushAllMemory = document.getElementById("btnFlushAllMemory");

// Upload & Controls
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const dropzoneText = document.getElementById("dropzoneText");
const btnStart = document.getElementById("btnStart");
const speakerLabelsCheckbox = document.getElementById("speakerLabelsCheckbox");
const voiceEnhancerCheckbox = document.getElementById("voiceEnhancerCheckbox");
const ambiguityCheckbox = document.getElementById("ambiguityCheckbox");
const councilCheckbox = document.getElementById("councilCheckbox");
const councilModeSelect = document.getElementById("councilModeSelect");
const diarizerSelect = document.getElementById("diarizerSelect");

// Pre-Flight Audio Health Badge Elements (1.1.A & 1.1.B)
const audioHealthBadgeBar = document.getElementById("audioHealthBadgeBar");
const healthBadgeGrade = document.getElementById("healthBadgeGrade");
const btnDismissHealthBadge = document.getElementById("btnDismissHealthBadge");
const healthSnrVal = document.getElementById("healthSnrVal");
const healthSnrStatus = document.getElementById("healthSnrStatus");
const healthClippingVal = document.getElementById("healthClippingVal");
const healthClippingStatus = document.getElementById("healthClippingStatus");
const healthDcVal = document.getElementById("healthDcVal");
const healthDcStatus = document.getElementById("healthDcStatus");
const healthLevelsVal = document.getElementById("healthLevelsVal");
const healthLevelsStatus = document.getElementById("healthLevelsStatus");
const healthLufsVal = document.getElementById("healthLufsVal");
const healthLufsStatus = document.getElementById("healthLufsStatus");
const healthPhaseVal = document.getElementById("healthPhaseVal");
const healthPhaseStatus = document.getElementById("healthPhaseStatus");
const healthRemedyBanner = document.getElementById("healthRemedyBanner");
const healthRemedyText = document.getElementById("healthRemedyText");
const healthRecsContainer = document.getElementById("healthRecsContainer");
const healthRecsList = document.getElementById("healthRecsList");
const lufsNormCheckbox = document.getElementById("lufsNormCheckbox");
const chunkOverlapCheckbox = document.getElementById("chunkOverlapCheckbox");

// Progress Card
const progressCard = document.getElementById("progressCard");
const progressStatus = document.getElementById("progressStatus");
const progressPercentage = document.getElementById("progressPercentage");
const progressFill = document.getElementById("progressFill");
const btnPause = document.getElementById("btnPause");
const btnResume = document.getElementById("btnResume");
const btnStop = document.getElementById("btnStop");

let pollTimer = null;

// Dual-Track Player Elements
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

// Visual Telemetry Elements (Sub-Phase 3.1: Heatmap, Gantt, Spectrogram)
const trackHeatmapRow = document.getElementById("trackHeatmapRow");
const heatmapRibbonWrapper = document.getElementById("heatmapRibbonWrapper");
const confidenceHeatmap = document.getElementById("confidenceHeatmap");
const heatmapPlayhead = document.getElementById("heatmapPlayhead");

const trackGanttRow = document.getElementById("trackGanttRow");
const ganttRibbonWrapper = document.getElementById("ganttRibbonWrapper");
const speakerGantt = document.getElementById("speakerGantt");
const ganttPlayhead = document.getElementById("ganttPlayhead");
const ganttSoloBar = document.getElementById("ganttSoloBar");
const btnGanttSoloAll = document.getElementById("btnGanttSoloAll");

const trackSpectrogramRow = document.getElementById("trackSpectrogramRow");
const spectrogramWrapper = document.getElementById("spectrogramWrapper");
const spectrogramCanvas = document.getElementById("spectrogramCanvas");
const spectrogramPlayhead = document.getElementById("spectrogramPlayhead");
const btnToggleSpectrogram = document.getElementById("btnToggleSpectrogram");

const timelineTooltip = document.getElementById("timelineTooltip");
let activeSoloSpeaker = null;

// Transcript Workspace & Left Sidebar Elements
const speakerSidebar = document.getElementById("speakerSidebar");
const speakerCountBadge = document.getElementById("speakerCountBadge");
const speakerSearchInput = document.getElementById("speakerSearchInput");
const speakerListCompact = document.getElementById("speakerListCompact");
const pillFilterAll = document.getElementById("pillFilterAll");
const pillFilterReview = document.getElementById("pillFilterReview");
const pillFilterDisputed = document.getElementById("pillFilterDisputed");
const filterCountAll = document.getElementById("filterCountAll");
const filterCountReview = document.getElementById("filterCountReview");
const filterCountDisputed = document.getElementById("filterCountDisputed");

const transcriptWorkspace = document.getElementById("transcriptWorkspace");
const transcriptFeed = document.getElementById("transcriptFeed");
const searchInput = document.getElementById("searchInput");
const btnExportTxt = document.getElementById("btnExportTxt");

// Floating Player Dock Elements
const floatingPlayer = document.getElementById("floatingPlayer");
const btnFloatingPlayPause = document.getElementById("btnFloatingPlayPause");
const floatingTime = document.getElementById("floatingTime");
const floatingSpeaker = document.getElementById("floatingSpeaker");
const floatingSnippet = document.getElementById("floatingSnippet");
const btnFloatingBack5 = document.getElementById("btnFloatingBack5");
const btnFloatingFwd5 = document.getElementById("btnFloatingFwd5");
const floatingPlaybackSpeed = document.getElementById("floatingPlaybackSpeed");
const btnFloatingJump = document.getElementById("btnFloatingJump");

// Rename Speakers Modal
const renameModal = document.getElementById("renameModal");
const btnRenameModal = document.getElementById("btnRenameModal");
const btnCancelRename = document.getElementById("btnCancelRename");
const btnSaveAliases = document.getElementById("btnSaveAliases");
const aliasInputsContainer = document.getElementById("aliasInputsContainer");

// --- 1. Theme Management ---
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.body.setAttribute("data-theme", theme);
  document.body.dataset.theme = theme;
  if (themeSelect) themeSelect.value = theme;
  localStorage.setItem("ts_theme", theme);
  updateWaveformTheme();
}

function initTheme() {
  const saved = localStorage.getItem("ts_theme") || "foggy-woodland";
  applyTheme(saved);
}

if (themeSelect) {
  themeSelect.addEventListener("change", (e) => {
    applyTheme(e.target.value);
  });
}

// --- 2. Primary Tab Navigation ---
const tabBtnModels = document.getElementById("tabBtnModels");
const paneModels = document.getElementById("paneModels");
const tabBtnSettings = document.getElementById("tabBtnSettings");
const paneSettings = document.getElementById("paneSettings");

function switchTab(tabId) {
  const allTabs = [
    { btn: tabBtnStudio, pane: paneStudio, id: "paneStudio" },
    { btn: tabBtnTranscript, pane: paneTranscript, id: "paneTranscript" },
    { btn: tabBtnModels, pane: paneModels, id: "paneModels" },
    { btn: tabBtnTelemetry, pane: paneTelemetry, id: "paneTelemetry" },
    { btn: tabBtnSettings, pane: paneSettings, id: "paneSettings" }
  ];

  allTabs.forEach(item => {
    if (item.btn) item.btn.classList.toggle("active", item.id === tabId);
    if (item.pane) item.pane.classList.toggle("active", item.id === tabId);
  });

  if (tabId === "paneTelemetry") {
    fetchSupervisorData();
    fetchJournalData();
    setTimeout(() => {
      if (cachedTraceSamples && cachedTraceSamples.length > 0) renderTraceGraph(cachedTraceSamples);
    }, 60);
  } else if (tabId === "paneModels") {
    fetchModelData();
    fetchPyAnnoteStatus();
  } else if (tabId === "paneSettings") {
    fetchSettingsData();
  } else if (tabId === "paneStudio") {
    setTimeout(() => {
      try { wavesurferOrig?.drawBuffer(); } catch(e){}
      try { wavesurferModel?.drawBuffer(); } catch(e){}
    }, 60);
  }
}

if (tabBtnStudio) tabBtnStudio.addEventListener("click", () => switchTab("paneStudio"));
if (tabBtnTranscript) tabBtnTranscript.addEventListener("click", () => switchTab("paneTranscript"));
if (tabBtnModels) tabBtnModels.addEventListener("click", () => switchTab("paneModels"));
if (tabBtnTelemetry) tabBtnTelemetry.addEventListener("click", () => switchTab("paneTelemetry"));
if (tabBtnSettings) tabBtnSettings.addEventListener("click", () => switchTab("paneSettings"));

// --- 3. Cache & Storage Dropdown Management ---
function initCacheDropdown() {
  if (!btnCacheDropdownToggle || !cacheDropdownMenu) return;

  btnCacheDropdownToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    cacheDropdownMenu.classList.toggle("show");
    if (cacheDropdownMenu.classList.contains("show")) {
      fetchCacheBreakdown();
    }
  });

  document.addEventListener("click", (e) => {
    if (!cacheDropdownMenu.contains(e.target) && e.target !== btnCacheDropdownToggle) {
      cacheDropdownMenu.classList.remove("show");
    }
  });

  const wireClearBtn = (btn, target) => {
    if (!btn) return;
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const origText = btn.innerText;
      btn.innerText = "Cleaning...";
      btn.disabled = true;
      try {
        const res = await fetch("/api/cache/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status !== "error") {
          if (target === "ram" && (data.breakdown?.memory?.app_ram_rss_mb || 0) <= 750) {
            btn.innerText = "✓ At Baseline";
          } else {
            btn.innerText = "✓ Cleared";
          }
          setTimeout(() => {
            btn.innerText = origText;
            btn.disabled = false;
          }, 1400);
          fetchCacheBreakdown();
          fetchMemoryStats();
          fetchTelemetryData();
        } else {
          console.warn("Cache clear failed:", res.status, data);
          btn.innerText = "⚠️ Failed";
          setTimeout(() => {
            btn.innerText = origText;
            btn.disabled = false;
          }, 1500);
        }
      } catch (err) {
        console.error("Cache clear error:", err);
        btn.innerText = "⚠️ Error";
        setTimeout(() => {
          btn.innerText = origText;
          btn.disabled = false;
        }, 1500);
      }
    });
  };

  wireClearBtn(btnClearVramOnly, "vram");
  wireClearBtn(btnClearRamOnly, "ram");
  wireClearBtn(btnClearTempAudio, "temp_audio");
  wireClearBtn(btnFlushAllMemory, "all");
}

async function fetchCacheBreakdown() {
  try {
    const res = await fetch("/api/cache/stats");
    if (!res.ok) return;
    const data = await res.json();
    const mem = data.memory || {};
    const storage = data.storage || {};

    if (cacheVramVal) cacheVramVal.innerText = `${mem.vram_reserved_mb || 0} MB reserved`;
    const rssMb = Math.round(mem.app_ram_rss_mb || 0);
    if (cacheRamVal) {
      if (rssMb <= 750) {
        cacheRamVal.innerText = `${rssMb} MB (Base Engine)`;
      } else {
        cacheRamVal.innerText = `${rssMb} MB (+${rssMb - 650} MB Heap)`;
      }
    }
    if (cacheTempAudioVal) cacheTempAudioVal.innerText = `${storage.temp_audio_mb || 0} MB`;
    if (cacheHfVal) cacheHfVal.innerText = `${storage.hf_total_gb || 0} GB`;
    if (cacheNemoVal) cacheNemoVal.innerText = `${storage.nemo_total_gb || 0} GB`;
    if (cacheTotalDiskVal) cacheTotalDiskVal.innerText = `${storage.total_disk_gb || 0} GB`;
  } catch (err) {
    console.warn("Failed to fetch cache breakdown:", err);
  }
}

// --- 4. Live Memory Telemetry ---
async function fetchMemoryStats() {
  try {
    const res = await fetch("/api/vram");
    if (!res.ok) return;
    const data = await res.json();

    const appRam = data.proc_ram_used_gb ?? data.app_ram_rss_gb ?? 0;
    const sysUsed = data.sys_ram_used_gb ?? 0;
    const sysTotal = data.sys_ram_total_gb ?? 15.3;
    const withoutSuite = data.sys_ram_without_suite_gb ?? Math.max(0, sysUsed - appRam);

    if (appRamText && appRam !== undefined && appRam !== null) {
      appRamText.innerText = `${appRam.toFixed(2)} GB`;
    }
    if (sysWithoutSuiteText) {
      sysWithoutSuiteText.innerText = `${withoutSuite.toFixed(2)} GB`;
    }

    if (ramText && data.sys_ram_used_gb !== undefined && data.sys_ram_total_gb !== undefined) {
      ramText.innerText = `${sysUsed.toFixed(1)} / ${sysTotal.toFixed(1)} GB`;
    }
    if (ramBarFill && data.sys_ram_percent !== undefined) {
      ramBarFill.style.width = `${Math.min(100, Math.max(0, data.sys_ram_percent))}%`;
      if (data.sys_ram_percent > 88) {
        ramBarFill.style.backgroundColor = "#ef4444";
      } else if (data.sys_ram_percent > 75) {
        ramBarFill.style.backgroundColor = "#f59e0b";
      } else {
        ramBarFill.style.backgroundColor = "var(--accent-bark)";
      }
    }

    const alloc = data.allocated_gb || 0;
    const reserved = data.reserved_gb || alloc;
    const total = data.total_gb || 7.6;
    const pct = data.percent_used || 0;

    vramText.innerText = `${alloc.toFixed(2)}G (${reserved.toFixed(1)}G res) / ${total.toFixed(1)} GB`;
    vramText.title = `Active Tensors: ${alloc.toFixed(2)} GB | Reserved by PyTorch: ${reserved.toFixed(2)} GB | Total: ${total.toFixed(1)} GB`;
    vramBarFill.style.width = `${Math.min(100, Math.max(0, pct))}%`;

    if (pct > 85) {
      vramBarFill.style.backgroundColor = "#ef4444";
    } else if (pct > 65) {
      vramBarFill.style.backgroundColor = "#f59e0b";
    } else {
      vramBarFill.style.backgroundColor = "var(--accent-light)";
    }
  } catch (e) {
    console.warn("Failed to fetch VRAM stats:", e);
  }
}

setInterval(fetchMemoryStats, 3000);
fetchMemoryStats();

// --- 5. File Upload & Start Transcription ---
dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.style.borderColor = "var(--accent-light)";
});

dropzone.addEventListener("dragleave", () => {
  dropzone.style.borderColor = "var(--border-dim)";
});

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.style.borderColor = "var(--border-dim)";
  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
    handleFile(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files && e.target.files[0]) {
    handleFile(e.target.files[0]);
  }
});

if (btnDismissHealthBadge) {
  btnDismissHealthBadge.addEventListener("click", () => {
    if (audioHealthBadgeBar) audioHealthBadgeBar.style.display = "none";
  });
}

function updateAudioHealthUI(health) {
  if (!health || !audioHealthBadgeBar) return;
  audioHealthBadgeBar.style.display = "block";

  // Grade Chip
  const grade = (health.health_grade || "GOOD").toUpperCase();
  healthBadgeGrade.className = `health-grade-chip grade-${grade.toLowerCase()}`;
  healthBadgeGrade.innerText = grade;

  // SNR (Signal-to-Noise Ratio)
  const snr = Number(health.snr_db ?? 0);
  healthSnrVal.innerText = `${snr.toFixed(1)} dB`;
  if (snr >= 20.0) {
    healthSnrStatus.className = "metric-status status-ok";
    healthSnrStatus.innerText = "Clean & Clear";
  } else if (snr >= 10.0) {
    healthSnrStatus.className = "metric-status status-warn";
    healthSnrStatus.innerText = "Moderate Noise";
  } else {
    healthSnrStatus.className = "metric-status status-alert";
    healthSnrStatus.innerText = "Heavy Noise";
  }

  // Clipping %
  const clip = Number(health.clipping_pct ?? 0);
  healthClippingVal.innerText = `${clip.toFixed(2)} %`;
  if (clip <= 0.05) {
    healthClippingStatus.className = "metric-status status-ok";
    healthClippingStatus.innerText = "Zero Distortion";
  } else if (clip <= 0.5) {
    healthClippingStatus.className = "metric-status status-warn";
    healthClippingStatus.innerText = "Minor Distortion";
  } else {
    healthClippingStatus.className = "metric-status status-alert";
    healthClippingStatus.innerText = "Severe Clipping";
  }

  // DC Offset
  const dc = Math.abs(Number(health.dc_offset ?? 0));
  healthDcVal.innerText = `${dc.toFixed(4)}`;
  if (dc <= 0.005) {
    healthDcStatus.className = "metric-status status-ok";
    healthDcStatus.innerText = "Calibrated";
  } else {
    healthDcStatus.className = "metric-status status-warn";
    healthDcStatus.innerText = "Offset Subtracted";
  }

  // Peak / RMS
  const peak = Number(health.peak_dbfs ?? -100);
  const rms = Number(health.rms_dbfs ?? -100);
  healthLevelsVal.innerText = `${peak.toFixed(1)} / ${rms.toFixed(1)} dBFS`;
  if (peak > -0.1) {
    healthLevelsStatus.className = "metric-status status-alert";
    healthLevelsStatus.innerText = "Near Ceiling";
  } else if (peak < -30.0) {
    healthLevelsStatus.className = "metric-status status-warn";
    healthLevelsStatus.innerText = "Quiet (Will Boost)";
  } else {
    healthLevelsStatus.className = "metric-status status-ok";
    healthLevelsStatus.innerText = "Optimal Headroom";
  }

  // EBU R128 Loudness (LUFS) (1.2.A)
  if (healthLufsVal && healthLufsStatus) {
    const lufs = (health.lufs !== undefined && health.lufs !== null) ? Number(health.lufs) : null;
    if (lufs !== null && !isNaN(lufs)) {
      healthLufsVal.innerText = `${lufs.toFixed(1)} LUFS`;
      if (lufs >= -20.0 && lufs <= -14.0) {
        healthLufsStatus.className = "metric-status status-ok";
        healthLufsStatus.innerText = "Target Broadcast";
      } else if (lufs < -28.0) {
        healthLufsStatus.className = "metric-status status-warn";
        healthLufsStatus.innerText = "Very Quiet (Will Boost)";
      } else if (lufs > -10.0) {
        healthLufsStatus.className = "metric-status status-alert";
        healthLufsStatus.innerText = "Very Loud";
      } else {
        healthLufsStatus.className = "metric-status status-ok";
        healthLufsStatus.innerText = "Standard Range";
      }
    } else {
      healthLufsVal.innerText = "-- LUFS";
      healthLufsStatus.className = "metric-status";
      healthLufsStatus.innerText = "--";
    }
  }

  // Channel Phase Correlation & Geometry (1.1.B)
  const channels = health.channels_original || 1;
  const phaseCorr = Number(health.phase_correlation ?? 1.0);
  if (channels === 1) {
    healthPhaseVal.innerText = "Mono (1-Ch)";
    healthPhaseStatus.className = "metric-status status-ok";
    healthPhaseStatus.innerText = "Direct Pass";
  } else {
    healthPhaseVal.innerText = `Stereo (ρ = ${phaseCorr.toFixed(2)})`;
    if (health.phase_inverted) {
      healthPhaseStatus.className = "metric-status status-alert";
      healthPhaseStatus.innerText = "Out-of-Phase (Inverted)";
    } else if (health.dead_channel_detected) {
      healthPhaseStatus.className = "metric-status status-warn";
      healthPhaseStatus.innerText = "Dead Mic Bypassed";
    } else {
      healthPhaseStatus.className = "metric-status status-ok";
      healthPhaseStatus.innerText = "Coherent Mixdown";
    }
  }

  // Feature 1.1.B Remediation Banner
  if (healthRemedyBanner) {
    if (health.phase_inverted) {
      healthRemedyBanner.style.display = "flex";
      healthRemedyText.innerHTML = `<strong>Acoustic Cancellation Fixed:</strong> Left/Right channels had inverted polarity (ρ = ${phaseCorr.toFixed(2)}). Polarity was automatically flipped to restore full vocal punch.`;
    } else if (health.dead_channel_detected) {
      healthRemedyBanner.style.display = "flex";
      healthRemedyText.innerHTML = `<strong>Dead Microphone Bypassed:</strong> One stereo channel was silent/disconnected. The active vocal channel was extracted cleanly.`;
    } else {
      healthRemedyBanner.style.display = "none";
    }
  }

  // Actionable recommendations
  if (healthRecsContainer && healthRecsList) {
    const recs = health.recommendations || [];
    if (recs.length > 0) {
      healthRecsContainer.style.display = "block";
      healthRecsList.innerHTML = recs.map(r => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      healthRecsContainer.style.display = "none";
    }
  }
}

async function runPreflightHealthCheck(file) {
  if (!audioHealthBadgeBar) return;
  audioHealthBadgeBar.style.display = "block";
  healthBadgeGrade.className = "health-grade-chip grade-checking";
  healthBadgeGrade.innerText = "DIAGNOSING...";
  healthSnrVal.innerText = "-- dB";
  healthSnrStatus.className = "metric-status";
  healthSnrStatus.innerText = "Measuring...";
  healthClippingVal.innerText = "-- %";
  healthClippingStatus.className = "metric-status";
  healthClippingStatus.innerText = "Checking...";
  healthDcVal.innerText = "--";
  healthDcStatus.className = "metric-status";
  healthDcStatus.innerText = "Filtering...";
  healthLevelsVal.innerText = "-- / -- dBFS";
  healthLevelsStatus.className = "metric-status";
  healthLevelsStatus.innerText = "Sampling...";
  if (healthLufsVal) healthLufsVal.innerText = "-- LUFS";
  if (healthLufsStatus) {
    healthLufsStatus.className = "metric-status";
    healthLufsStatus.innerText = "Analyzing...";
  }
  healthPhaseVal.innerText = "--";
  healthPhaseStatus.className = "metric-status";
  healthPhaseStatus.innerText = "Correlating...";
  if (healthRemedyBanner) healthRemedyBanner.style.display = "none";
  if (healthRecsContainer) healthRecsContainer.style.display = "none";

  const formData = new FormData();
  formData.append("audio", file);

  try {
    const res = await fetch("/api/audio/diagnostics", {
      method: "POST",
      body: formData
    });
    if (res.ok) {
      const data = await res.json();
      if (data.status === "ok" && data.audio_health) {
        updateAudioHealthUI(data.audio_health);
      }
    }
  } catch (err) {
    console.warn("Pre-flight audio health check failed:", err);
  }
}

function handleFile(file) {
  selectedFile = file;
  dropzoneText.innerHTML = `<strong>Selected:</strong> ${escapeHtml(file.name)} <span style="font-size: 12px; color: var(--text-muted);">(${formatFileSize(file.size)})</span>`;
  btnStart.disabled = false;
  runPreflightHealthCheck(file);
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  else return (bytes / 1048576).toFixed(1) + ' MB';
}

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
  formData.append("enable_council", councilCheckbox ? councilCheckbox.checked : true);
  formData.append("council_mode", councilModeSelect ? councilModeSelect.value : "sequential");
  formData.append("enable_lufs", lufsNormCheckbox ? lufsNormCheckbox.checked : true);
  formData.append("target_lufs", -16.0);
  formData.append("chunk_overlap", (chunkOverlapCheckbox && !chunkOverlapCheckbox.checked) ? 0.0 : 0.5);
  formData.append("enable_dedup", chunkOverlapCheckbox ? chunkOverlapCheckbox.checked : true);
  const vocalBoostSelect = document.getElementById("vocalBoostSelect");
  if (vocalBoostSelect) {
    formData.append("vocal_boost_level", vocalBoostSelect.value);
  }
  if (currentActiveGlossary && currentActiveGlossary.length > 0) {
    formData.append("glossary", JSON.stringify(currentActiveGlossary));
  }
  if (currentAttentionBackend) {
    formData.append("attention_backend", currentAttentionBackend);
  }

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

      // Live stream segments online into the editor tab
      if (data.segments && data.segments.length > 0) {
        if (currentSegments.length !== data.segments.length) {
          currentSegments = data.segments;
          if (tabTranscriptBadge) tabTranscriptBadge.innerText = currentSegments.length;
          renderSpeakerSidebar();
          renderTranscriptFeed();
          if (wavesurferOrig && wavesurferOrig.getDuration() > 0) {
            const dur = wavesurferOrig.getDuration();
            renderConfidenceHeatmap(currentSegments, dur);
            renderSpeakerGantt(currentSegments, dur);
          }
        }
      }

      if (data.audio_health) {
        updateAudioHealthUI(data.audio_health);
      }

      if (data.status === "completed") {
        clearInterval(pollTimer);
        progressCard.style.display = "none";
        btnStart.disabled = false;
        onTranscriptionSuccess(data);
        fetchSupervisorData();
        fetchJournalData();
      } else if (data.status === "stopped") {
        clearInterval(pollTimer);
        progressCard.style.display = "none";
        btnStart.disabled = false;
        fetchSupervisorData();
        fetchJournalData();
      } else if (data.status === "failed") {
        clearInterval(pollTimer);
        alert("Transcription failed: " + data.message);
        btnStart.disabled = false;
        progressCard.style.display = "none";
        fetchSupervisorData();
        fetchJournalData();
      }
    } catch (e) {
      // Continue polling
    }
  }, 1000);
}

function onTranscriptionSuccess(data) {
  if (data.audio_health) {
    updateAudioHealthUI(data.audio_health);
  }
  currentSegments = data.segments;
  if (tabTranscriptBadge) tabTranscriptBadge.innerText = currentSegments.length;
  initAudioPlayer(data.id);
  renderSpeakerSidebar();
  renderTranscriptFeed();
  playerCard.style.display = "block";
  loadSpectrogram(data.id);
  if (wavesurferOrig && wavesurferOrig.getDuration() > 0) {
    const dur = wavesurferOrig.getDuration();
    renderConfidenceHeatmap(currentSegments, dur);
    renderSpeakerGantt(currentSegments, dur);
  }
  // Seamlessly transition user to the Transcript & Editor tab
  switchTab("paneTranscript");
}

// --- 6. Dual-Track Audio Studio Waveforms ---
function getWaveThemeColors() {
  const currentTheme = document.body.dataset.theme;
  if (currentTheme === "forest-sage") {
    return {
      origWave: '#252e22', origProgress: '#6c945d',
      modelWave: '#1c231a', modelProgress: '#9fd18c'
    };
  } else if (currentTheme === "nordic-slate") {
    return {
      origWave: '#1e293a', origProgress: '#488ac7',
      modelWave: '#161e2b', modelProgress: '#72b6f4'
    };
  } else if (currentTheme === "warm-umber") {
    return {
      origWave: '#30241c', origProgress: '#b86d44',
      modelWave: '#231a14', modelProgress: '#ea9866'
    };
  } else {
    // Foggy woodland default
    return {
      origWave: '#1a2b24', origProgress: '#3d8b63',
      modelWave: '#131f1a', modelProgress: '#5ec793'
    };
  }
}

function initAudioPlayer(taskId) {
  if (wavesurferOrig) { wavesurferOrig.destroy(); wavesurferOrig = null; }
  if (wavesurferModel) { wavesurferModel.destroy(); wavesurferModel = null; }

  const colors = getWaveThemeColors();

  wavesurferOrig = WaveSurfer.create({
    container: '#waveformOrig',
    waveColor: colors.origWave,
    progressColor: colors.origProgress,
    cursorColor: '#a87855',
    cursorWidth: 2,
    height: 64,
    normalize: true,
    url: `/api/audio/${taskId}`
  });

  wavesurferModel = WaveSurfer.create({
    container: '#waveformModel',
    waveColor: colors.modelWave,
    progressColor: colors.modelProgress,
    cursorColor: '#558762',
    cursorWidth: 2,
    height: 64,
    normalize: true,
    url: `/api/audio/${taskId}/processed`
  });

  // Lockstep seeking synchronization
  wavesurferOrig.on('seeking', (time) => {
    const totalDuration = wavesurferOrig.getDuration();
    updateTimelinePlayheads(time, totalDuration);
    if (!isSeekingSync && wavesurferModel) {
      isSeekingSync = true;
      wavesurferModel.setTime(time);
      setTimeout(() => { isSeekingSync = false; }, 50);
    }
  });

  wavesurferModel.on('seeking', (time) => {
    const totalDuration = wavesurferOrig ? wavesurferOrig.getDuration() : wavesurferModel.getDuration();
    updateTimelinePlayheads(time, totalDuration);
    if (!isSeekingSync && wavesurferOrig) {
      isSeekingSync = true;
      wavesurferOrig.setTime(time);
      setTimeout(() => { isSeekingSync = false; }, 50);
    }
  });

  // Timeupdate, playhead sync, and drift correction
  wavesurferOrig.on('timeupdate', (currentTime) => {
    const totalDuration = wavesurferOrig.getDuration();
    updatePlaybackTime(currentTime, totalDuration);
    syncActiveSegment(currentTime);
    updateFloatingPlayerTime(currentTime, totalDuration);
    updateTimelinePlayheads(currentTime, totalDuration);

    if (wavesurferModel && wavesurferOrig.isPlaying() && !isSeekingSync) {
      const diff = Math.abs(currentTime - wavesurferModel.getCurrentTime());
      if (diff > 0.35) {
        isSeekingSync = true;
        wavesurferModel.setTime(currentTime);
        setTimeout(() => { isSeekingSync = false; }, 60);
      }
    }
  });

  // Play / Pause event handlers
  wavesurferOrig.on('play', () => {
    btnPlayPause.innerText = "⏸ Pause";
    if (btnFloatingPlayPause) btnFloatingPlayPause.innerText = "⏸";
    if (wavesurferModel) {
      const t = wavesurferOrig.getCurrentTime();
      if (Math.abs(wavesurferModel.getCurrentTime() - t) > 0.06 && !isSeekingSync) {
        isSeekingSync = true;
        wavesurferModel.setTime(t);
        setTimeout(() => { isSeekingSync = false; }, 40);
      }
      if (!wavesurferModel.isPlaying()) wavesurferModel.play();
    }
  });

  wavesurferOrig.on('pause', () => {
    btnPlayPause.innerText = "▶ Play Both";
    if (btnFloatingPlayPause) btnFloatingPlayPause.innerText = "▶";
    if (wavesurferModel && wavesurferModel.isPlaying()) wavesurferModel.pause();
  });

  wavesurferModel.on('play', () => {
    if (wavesurferOrig) {
      const t = wavesurferModel.getCurrentTime();
      if (Math.abs(wavesurferOrig.getCurrentTime() - t) > 0.06 && !isSeekingSync) {
        isSeekingSync = true;
        wavesurferOrig.setTime(t);
        setTimeout(() => { isSeekingSync = false; }, 40);
      }
      if (!wavesurferOrig.isPlaying()) wavesurferOrig.play();
    }
  });

  wavesurferModel.on('pause', () => {
    if (wavesurferOrig && wavesurferOrig.isPlaying()) wavesurferOrig.pause();
  });

  wavesurferOrig.on('ready', () => {
    updateMixLevels();
    const duration = wavesurferOrig.getDuration();
    if (currentSegments && currentSegments.length > 0) {
      renderConfidenceHeatmap(currentSegments, duration);
      renderSpeakerGantt(currentSegments, duration);
    }
  });
  wavesurferModel.on('ready', () => updateMixLevels());

  loadSpectrogram(taskId);
  updateTimelinePlayheads(0, 1);

  const wfContainer = document.getElementById("waveformOrig");
  if (wfContainer && window.ResizeObserver) {
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          try { wavesurferOrig?.drawBuffer(); } catch (e) {}
          try { wavesurferModel?.drawBuffer(); } catch (e) {}
        }
      }
    });
    ro.observe(wfContainer);
  }
}

function updateWaveformTheme() {
  const colors = getWaveThemeColors();
  if (wavesurferOrig) wavesurferOrig.setOptions({ waveColor: colors.origWave, progressColor: colors.origProgress });
  if (wavesurferModel) wavesurferModel.setOptions({ waveColor: colors.modelWave, progressColor: colors.modelProgress });
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
  const t = Math.max(0, wavesurferOrig.getCurrentTime() - 5);
  wavesurferOrig.setTime(t);
  if (wavesurferModel) wavesurferModel.setTime(t);
});

btnFwd5.addEventListener("click", () => {
  if (!wavesurferOrig) return;
  const t = Math.min(wavesurferOrig.getDuration(), wavesurferOrig.getCurrentTime() + 5);
  wavesurferOrig.setTime(t);
  if (wavesurferModel) wavesurferModel.setTime(t);
});

playbackSpeed.addEventListener("change", (e) => {
  const rate = parseFloat(e.target.value);
  if (wavesurferOrig) wavesurferOrig.setPlaybackRate(rate);
  if (wavesurferModel) wavesurferModel.setPlaybackRate(rate);
  if (floatingPlaybackSpeed) floatingPlaybackSpeed.value = e.target.value;
});

// A/B & Crossfader Controls
function setCrossfade(percent) {
  audioCrossfader.value = percent;
  const p = percent / 100.0;
  let vOrig = 1.0;
  let vModel = 1.0;

  if (p < 0.5) {
    vOrig = 1.0;
    vModel = p * 2.0;
  } else {
    vOrig = (1.0 - p) * 2.0;
    vModel = 1.0;
  }

  volumeState.orig = vOrig;
  volumeState.model = vModel;
  volOrig.value = vOrig;
  volModel.value = vModel;

  btnABOriginal.classList.toggle("active", percent <= 10);
  btnABMix.classList.toggle("active", percent > 40 && percent < 60);
  btnABModel.classList.toggle("active", percent >= 90);

  updateMixLevels();
}

function updateMixLevels() {
  if (!wavesurferOrig || !wavesurferModel) return;

  let finalVolOrig = volumeState.orig;
  let finalVolModel = volumeState.model;

  if (soloState.orig) finalVolModel = 0;
  if (soloState.model) finalVolOrig = 0;
  if (muteState.orig) finalVolOrig = 0;
  if (muteState.model) finalVolModel = 0;

  wavesurferOrig.setVolume(finalVolOrig);
  wavesurferModel.setVolume(finalVolModel);
}

btnABOriginal.addEventListener("click", () => setCrossfade(0));
btnABMix.addEventListener("click", () => setCrossfade(50));
btnABModel.addEventListener("click", () => setCrossfade(100));

audioCrossfader.addEventListener("input", (e) => {
  const val = parseFloat(e.target.value);
  const p = val / 100.0;
  volumeState.orig = p < 0.5 ? 1.0 : (1.0 - p) * 2.0;
  volumeState.model = p < 0.5 ? p * 2.0 : 1.0;
  volOrig.value = volumeState.orig;
  volModel.value = volumeState.model;
  btnABOriginal.classList.toggle("active", val <= 10);
  btnABMix.classList.toggle("active", val > 40 && val < 60);
  btnABModel.classList.toggle("active", val >= 90);
  updateMixLevels();
});

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
  if (playerTime) {
    playerTime.innerText = `${formatSeconds(curr)} / ${formatSeconds(total || 0)}`;
  }
}

// --- 6.B Dual-Track Visual Telemetry (Sub-Phase 3.1: Heatmap, Gantt, Spectrogram) ---

const SPEAKER_PALETTE = [
  { bg: 'rgba(59, 130, 246, 0.85)', border: '#3b82f6', text: '#ffffff' },   // Blue
  { bg: 'rgba(16, 185, 129, 0.85)', border: '#10b981', text: '#ffffff' },   // Emerald
  { bg: 'rgba(245, 158, 11, 0.85)', border: '#f59e0b', text: '#ffffff' },   // Amber
  { bg: 'rgba(168, 85, 247, 0.85)', border: '#a855f7', text: '#ffffff' },   // Purple
  { bg: 'rgba(236, 72, 153, 0.85)', border: '#ec4899', text: '#ffffff' },   // Pink
  { bg: 'rgba(14, 165, 233, 0.85)', border: '#0ea5e9', text: '#ffffff' },   // Sky
  { bg: 'rgba(249, 115, 22, 0.85)', border: '#f97316', text: '#ffffff' },   // Orange
  { bg: 'rgba(132, 204, 22, 0.85)', border: '#84cc16', text: '#ffffff' }    // Lime
];

function getSpeakerColor(speaker, index = 0) {
  const match = speaker ? speaker.match(/\d+/) : null;
  const num = match ? parseInt(match[0], 10) : index;
  return SPEAKER_PALETTE[Math.abs(num) % SPEAKER_PALETTE.length];
}

function seekAudioTo(targetTime) {
  if (!wavesurferOrig) return;
  const dur = wavesurferOrig.getDuration();
  const clamped = Math.max(0, Math.min(dur || targetTime, targetTime));
  isSeekingSync = true;
  wavesurferOrig.setTime(clamped);
  if (wavesurferModel) {
    wavesurferModel.setTime(clamped);
  }
  updateTimelinePlayheads(clamped, dur);
  syncActiveSegment(clamped, true);
  setTimeout(() => { isSeekingSync = false; }, 60);
}

function updateTimelinePlayheads(currentTime, duration) {
  if (!duration || duration <= 0) return;
  const pct = Math.max(0, Math.min(100, (currentTime / duration) * 100));
  const pctStr = `${pct}%`;
  if (heatmapPlayhead) heatmapPlayhead.style.left = pctStr;
  if (ganttPlayhead) ganttPlayhead.style.left = pctStr;
  if (spectrogramPlayhead) spectrogramPlayhead.style.left = pctStr;
}

function renderConfidenceHeatmap(segments, duration) {
  if (!confidenceHeatmap) return;
  confidenceHeatmap.innerHTML = "";

  const effectiveDuration = (duration && duration > 0)
    ? duration
    : (segments && segments.length > 0 ? Math.max(...segments.map(s => s.end || 0)) : 0);

  if (!segments || segments.length === 0 || effectiveDuration <= 0) return;

  segments.forEach((seg, index) => {
    const start = Math.max(0, seg.start);
    const end = Math.min(effectiveDuration, seg.end);
    if (end <= start) return;

    const leftPercent = (start / effectiveDuration) * 100;
    const widthPercent = Math.max(0.15, ((end - start) / effectiveDuration) * 100);

    const isBreaker = !!(seg.loop_circuit_breaker_tripped || seg.telemetry?.loop_circuit_breaker_tripped || seg.council?.loop_circuit_breaker_tripped);
    let score = 1.0;
    if (seg.consensus_score !== undefined && seg.consensus_score !== null) {
      score = Number(seg.consensus_score);
    } else if (seg.council && seg.council.consensus_score !== undefined && seg.council.consensus_score !== null) {
      score = Number(seg.council.consensus_score);
    } else if (seg.needs_review) {
      score = 0.55;
    }

    let colorClass = "heatmap-green";
    if (isBreaker) {
      colorClass = "heatmap-tripped";
    } else if (score >= 0.85) {
      colorClass = "heatmap-green";
    } else if (score >= 0.65) {
      colorClass = "heatmap-yellow";
    } else {
      colorClass = "heatmap-red";
    }

    const block = document.createElement("div");
    block.className = `heatmap-block ${colorClass}`;
    block.dataset.index = index;
    block.dataset.start = start;
    block.dataset.end = end;
    block.dataset.score = score.toFixed(2);
    block.style.left = `${leftPercent}%`;
    block.style.width = `${widthPercent}%`;

    block.addEventListener("mousemove", (e) => {
      if (!timelineTooltip) return;
      timelineTooltip.style.display = "flex";
      timelineTooltip.style.left = `${e.clientX}px`;
      timelineTooltip.style.top = `${e.clientY - 12}px`;

      const pct = Math.round(score * 100);
      const agreementLabel = score >= 0.85 ? "Unanimous" : (score >= 0.65 ? "Majority" : "Disputed");
      const breakerNotice = isBreaker ? `<div class="tooltip-score" style="color: #f472b6; font-weight: 700;">⚡ Hallucination Breaker Tripped!</div>` : '';
      const snrInfo = seg.snr_db !== undefined ? `<div class="tooltip-snr">Segment SNR: ${Number(seg.snr_db).toFixed(1)} dB</div>` : '';

      timelineTooltip.innerHTML = `
        <div class="tooltip-time">⏱ [${formatSeconds(start)} – ${formatSeconds(end)}]</div>
        <div class="tooltip-score">Consensus: ${pct}% (${agreementLabel})</div>
        ${breakerNotice}
        ${snrInfo}
        <div class="tooltip-text">"${escapeHtml(seg.text || '')}"</div>
        <div class="tooltip-snr" style="font-size: 9px; margin-top: 2px; color: #94a3b8;">Click to seek audio</div>
      `;
    });

    block.addEventListener("mouseleave", () => {
      if (timelineTooltip) timelineTooltip.style.display = "none";
    });

    block.addEventListener("click", (e) => {
      e.stopPropagation();
      seekAudioTo(start);
      syncActiveSegment(start, true);
    });

    confidenceHeatmap.appendChild(block);
  });
}

function renderSpeakerGantt(segments, duration) {
  if (!speakerGantt) return;
  speakerGantt.innerHTML = "";

  const effectiveDuration = (duration && duration > 0)
    ? duration
    : (segments && segments.length > 0 ? Math.max(...segments.map(s => s.end || 0)) : 0);

  if (!segments || segments.length === 0 || effectiveDuration <= 0) return;

  const distinctSpeakers = Array.from(new Set(segments.map(s => s.speaker || "Speaker 0"))).sort();

  if (ganttSoloBar) {
    ganttSoloBar.innerHTML = "";

    const btnAll = document.createElement("button");
    btnAll.className = `btn-gantt-solo ${activeSoloSpeaker === null ? 'active' : ''}`;
    btnAll.dataset.speaker = "ALL";
    btnAll.textContent = "🎙️ All";
    btnAll.addEventListener("click", () => setSoloSpeaker(null));
    ganttSoloBar.appendChild(btnAll);

    distinctSpeakers.forEach((spk, idx) => {
      const col = getSpeakerColor(spk, idx);
      const btn = document.createElement("button");
      btn.className = `btn-gantt-solo ${activeSoloSpeaker === spk ? 'active' : ''}`;
      btn.dataset.speaker = spk;
      const alias = speakerAliases[spk] || spk;
      btn.innerHTML = `<span style="display:inline-block; width:7px; height:7px; border-radius:50%; background:${col.border}; margin-right:4px;"></span>${escapeHtml(alias)}`;
      btn.addEventListener("click", () => setSoloSpeaker(spk));
      ganttSoloBar.appendChild(btn);
    });
  }

  segments.forEach((seg, index) => {
    const start = Math.max(0, seg.start);
    const end = Math.min(effectiveDuration, seg.end);
    if (end <= start) return;

    const spk = seg.speaker || "Speaker 0";
    const spkIdx = distinctSpeakers.indexOf(spk);
    const col = getSpeakerColor(spk, spkIdx >= 0 ? spkIdx : 0);

    const leftPercent = (start / effectiveDuration) * 100;
    const widthPercent = Math.max(0.2, ((end - start) / effectiveDuration) * 100);

    const block = document.createElement("div");
    block.className = `gantt-block ${activeSoloSpeaker && activeSoloSpeaker !== spk ? 'dimmed' : ''}`;
    block.dataset.speaker = spk;
    block.dataset.index = index;
    block.dataset.start = start;
    block.dataset.end = end;
    block.style.left = `${leftPercent}%`;
    block.style.width = `${widthPercent}%`;
    block.style.backgroundColor = col.bg;
    block.style.borderColor = col.border;
    block.style.color = col.text;

    const durationSec = end - start;
    const alias = speakerAliases[spk] || spk;
    if (durationSec > 2.0) {
      block.textContent = alias;
    }

    block.addEventListener("mousemove", (e) => {
      if (!timelineTooltip) return;
      timelineTooltip.style.display = "flex";
      timelineTooltip.style.left = `${e.clientX}px`;
      timelineTooltip.style.top = `${e.clientY - 12}px`;
      timelineTooltip.innerHTML = `
        <div class="tooltip-time" style="color: ${col.border};">🎙️ ${escapeHtml(alias)} [${formatSeconds(start)} – ${formatSeconds(end)}]</div>
        <div class="tooltip-text">"${escapeHtml(seg.text || '')}"</div>
        <div class="tooltip-snr" style="font-size: 9px; margin-top: 2px; color: #94a3b8;">Click to seek audio</div>
      `;
    });

    block.addEventListener("mouseleave", () => {
      if (timelineTooltip) timelineTooltip.style.display = "none";
    });

    block.addEventListener("click", (e) => {
      e.stopPropagation();
      seekAudioTo(start);
      syncActiveSegment(start, true);
    });

    speakerGantt.appendChild(block);
  });
}

function updateGanttSoloUI() {
  if (ganttSoloBar) {
    const soloBtns = ganttSoloBar.querySelectorAll(".btn-gantt-solo");
    soloBtns.forEach(btn => {
      const targetSpk = btn.dataset.speaker;
      if (activeSoloSpeaker === null) {
        btn.classList.toggle("active", targetSpk === "ALL");
      } else {
        btn.classList.toggle("active", targetSpk === activeSoloSpeaker);
      }
    });
  }

  if (speakerGantt) {
    const blocks = speakerGantt.querySelectorAll(".gantt-block");
    blocks.forEach(b => {
      if (!activeSoloSpeaker) {
        b.classList.remove("dimmed");
      } else {
        b.classList.toggle("dimmed", b.dataset.speaker !== activeSoloSpeaker);
      }
    });
  }
}

function setSoloSpeaker(spk) {
  if (spk === "ALL" || spk === null) {
    activeSoloSpeaker = null;
    activeSpeakerFilter = "ALL";
  } else {
    activeSoloSpeaker = (activeSoloSpeaker === spk) ? null : spk;
    activeSpeakerFilter = activeSoloSpeaker ? activeSoloSpeaker : "ALL";
  }
  updateGanttSoloUI();
  if (pillFilterAll) pillFilterAll.classList.toggle("active", activeSpeakerFilter === "ALL");
  if (speakerListCompact) {
    speakerListCompact.querySelectorAll(".speaker-card-compact").forEach(c => {
      c.classList.toggle("active", c.dataset.speaker === activeSpeakerFilter);
    });
  }
  renderTranscriptFeed();
}

function loadSpectrogram(taskId) {
  if (!spectrogramCanvas || !taskId) return;
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = `/api/audio/${taskId}/spectrogram?processed=false&width=1200&height=96&_t=${Date.now()}`;
  img.onload = () => {
    spectrogramCanvas.width = img.naturalWidth || 1200;
    spectrogramCanvas.height = img.naturalHeight || 96;
    const ctx = spectrogramCanvas.getContext("2d");
    if (ctx) {
      ctx.clearRect(0, 0, spectrogramCanvas.width, spectrogramCanvas.height);
      ctx.drawImage(img, 0, 0, spectrogramCanvas.width, spectrogramCanvas.height);
    }
  };
  img.onerror = () => {
    console.warn("Could not load spectrogram for task", taskId);
  };
}

// Visual Telemetry Ribbon Event Listeners
if (heatmapRibbonWrapper) {
  heatmapRibbonWrapper.addEventListener("click", (e) => {
    if (e.target.classList.contains("heatmap-block")) return;
    if (!wavesurferOrig) return;
    const dur = wavesurferOrig.getDuration();
    if (dur <= 0) return;
    const rect = heatmapRibbonWrapper.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekAudioTo(ratio * dur);
  });
}

if (ganttRibbonWrapper) {
  ganttRibbonWrapper.addEventListener("click", (e) => {
    if (e.target.classList.contains("gantt-block")) return;
    if (!wavesurferOrig) return;
    const dur = wavesurferOrig.getDuration();
    if (dur <= 0) return;
    const rect = ganttRibbonWrapper.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekAudioTo(ratio * dur);
  });
}

if (spectrogramWrapper) {
  spectrogramWrapper.addEventListener("click", (e) => {
    if (!wavesurferOrig) return;
    const dur = wavesurferOrig.getDuration();
    if (dur <= 0) return;
    const rect = spectrogramWrapper.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekAudioTo(ratio * dur);
  });

  if (timelineTooltip) {
    spectrogramWrapper.addEventListener("mousemove", (e) => {
      if (!wavesurferOrig) return;
      const dur = wavesurferOrig.getDuration();
      if (dur <= 0) return;
      const rect = spectrogramWrapper.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const t = ratio * dur;
      const yRatio = 1 - Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
      const freqHz = Math.round(yRatio * 8000);
      const freqLabel = freqHz < 1000 ? `${freqHz} Hz` : `${(freqHz / 1000).toFixed(1)} kHz`;

      timelineTooltip.style.display = "flex";
      timelineTooltip.style.left = `${e.clientX}px`;
      timelineTooltip.style.top = `${e.clientY - 12}px`;
      timelineTooltip.innerHTML = `
        <div class="tooltip-time">⏱ ${formatSeconds(t)}</div>
        <div class="tooltip-snr">Frequency: ~${freqLabel} (Formant Band)</div>
        <div class="tooltip-snr" style="font-size: 9px; margin-top: 2px; color: #94a3b8;">Click to seek audio</div>
      `;
    });

    spectrogramWrapper.addEventListener("mouseleave", () => {
      timelineTooltip.style.display = "none";
    });
  }
}

if (btnToggleSpectrogram && spectrogramWrapper) {
  btnToggleSpectrogram.addEventListener("click", () => {
    const isExpanded = spectrogramWrapper.classList.toggle("expanded");
    btnToggleSpectrogram.textContent = isExpanded ? "⤡" : "⤢";
    btnToggleSpectrogram.title = isExpanded ? "Collapse Spectrogram" : "Expand Spectrogram";
  });
}

// --- 7. Floating Audio Player Dock Synchronization ---
function initFloatingPlayer() {
  if (!btnFloatingPlayPause) return;

  btnFloatingPlayPause.addEventListener("click", () => {
    btnPlayPause.click();
  });

  btnFloatingBack5.addEventListener("click", () => {
    btnBack5.click();
  });

  btnFloatingFwd5.addEventListener("click", () => {
    btnFwd5.click();
  });

  floatingPlaybackSpeed.addEventListener("change", (e) => {
    playbackSpeed.value = e.target.value;
    playbackSpeed.dispatchEvent(new Event("change"));
  });

  btnFloatingJump.addEventListener("click", () => {
    if (!wavesurferOrig) return;
    const t = wavesurferOrig.getCurrentTime();
    syncActiveSegment(t, true);
  });
}

function updateFloatingPlayerTime(curr, total) {
  if (floatingTime) {
    floatingTime.innerText = `${formatSeconds(curr)} / ${formatSeconds(total || 0)}`;
  }
}

// --- 8. Left Sidebar Speaker Hub & Compact List ---
function getSpeakerClass(speaker) {
  const match = speaker.match(/\d+/);
  const num = match ? parseInt(match[0], 10) % 4 : 0;
  return `spk-${num}`;
}

function renderSpeakerSidebar() {
  if (!speakerListCompact) return;
  speakerListCompact.innerHTML = "";

  // 1. Gather all speaker stats
  const speakerStats = {};
  currentSegments.forEach(seg => {
    const spk = seg.speaker || "Speaker 0";
    if (!speakerStats[spk]) {
      speakerStats[spk] = { count: 0, duration: 0 };
    }
    speakerStats[spk].count += 1;
    speakerStats[spk].duration += (seg.end - seg.start);
  });

  const distinctSpeakers = Object.keys(speakerStats).sort();
  if (speakerCountBadge) speakerCountBadge.innerText = distinctSpeakers.length;

  // 2. Global filter chips counts
  const reviewCount = currentSegments.filter(s => s.needs_review).length;
  const disputedCount = currentSegments.filter(s => s.council && s.council.agreement_type === 'SPLIT_DECISION').length;

  if (filterCountAll) filterCountAll.innerText = currentSegments.length;
  if (filterCountReview) filterCountReview.innerText = reviewCount;
  if (filterCountDisputed) filterCountDisputed.innerText = disputedCount;

  if (pillFilterReview) pillFilterReview.style.display = reviewCount > 0 ? "flex" : "none";
  if (pillFilterDisputed) pillFilterDisputed.style.display = disputedCount > 0 ? "flex" : "none";

  // Wire filter pill clicks
  const setFilter = (filt) => {
    activeSpeakerFilter = filt;
    activeSoloSpeaker = (filt === "ALL" || filt === "REVIEW" || filt === "DISPUTED") ? null : filt;
    updateGanttSoloUI();
    pillFilterAll.classList.toggle("active", filt === "ALL");
    pillFilterReview.classList.toggle("active", filt === "REVIEW");
    pillFilterDisputed.classList.toggle("active", filt === "DISPUTED");
    renderSpeakerSidebar();
    renderTranscriptFeed();
  };

  pillFilterAll.onclick = () => setFilter("ALL");
  pillFilterReview.onclick = () => setFilter("REVIEW");
  pillFilterDisputed.onclick = () => setFilter("DISPUTED");

  // 3. Render compact cards
  const query = (speakerSearchInput ? speakerSearchInput.value : "").toLowerCase();

  distinctSpeakers.forEach(spk => {
    const displayName = speakerAliases[spk] || spk;
    if (query && !displayName.toLowerCase().includes(query) && !spk.toLowerCase().includes(query)) {
      return;
    }

    const stats = speakerStats[spk];
    const card = document.createElement("div");
    card.className = `speaker-card-compact ${activeSpeakerFilter === spk ? 'active' : ''}`;
    card.dataset.speaker = spk;

    const spkClass = getSpeakerClass(spk);

    card.innerHTML = `
      <div class="speaker-card-left">
        <span class="speaker-avatar-dot ${spkClass}"></span>
        <div class="speaker-info-col">
          <span class="speaker-alias-name" title="${spk}">${displayName}</span>
          <span class="speaker-talk-time">${stats.count} chunks • ${formatSeconds(stats.duration)}</span>
        </div>
      </div>
      <button class="btn-inline-rename" data-spk="${spk}" title="Rename alias">✏️</button>
    `;

    card.addEventListener("click", (e) => {
      if (e.target.classList.contains("btn-inline-rename")) return;
      activeSpeakerFilter = (activeSpeakerFilter === spk) ? "ALL" : spk;
      activeSoloSpeaker = (activeSpeakerFilter === "ALL") ? null : activeSpeakerFilter;
      pillFilterAll.classList.toggle("active", activeSpeakerFilter === "ALL");
      updateGanttSoloUI();
      renderSpeakerSidebar();
      renderTranscriptFeed();
    });

    const renameBtn = card.querySelector(".btn-inline-rename");
    renameBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const currentName = speakerAliases[spk] || spk;
      const newName = prompt(`Rename ${spk}:`, currentName);
      if (newName !== null && newName.trim() !== "") {
        speakerAliases[spk] = newName.trim();
        renderSpeakerSidebar();
        renderTranscriptFeed();
        if (wavesurferOrig && wavesurferOrig.getDuration() > 0) {
          renderSpeakerGantt(currentSegments, wavesurferOrig.getDuration());
        }
      }
    });

    speakerListCompact.appendChild(card);
  });
}

if (speakerSearchInput) {
  speakerSearchInput.addEventListener("input", () => renderSpeakerSidebar());
}

// --- 9. Transcript Workspace & Editor Feed ---
const expandedCouncilSet = new Set();

function renderTranscriptFeed() {
  if (!transcriptFeed) return;
  transcriptFeed.innerHTML = "";
  const query = searchInput ? searchInput.value.toLowerCase() : "";

  if (currentSegments.length === 0) {
    transcriptFeed.innerHTML = `
      <div class="empty-state-notice">
        <div style="font-size: 36px; margin-bottom: 8px;">🎙️</div>
        <div style="font-weight: 600;">No transcript segments yet</div>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
          Upload an audio file in the <strong>Studio & Ingest</strong> tab to begin transcription.
        </div>
      </div>
    `;
    return;
  }

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
      badgeExtras += `<span class="badge-review" title="High ambiguity persisted. Review recommended.">⚠️ Needs Review</span>`;
    }
    if (seg.edited) {
      badgeExtras += `<span class="badge-edited" title="Manually edited">Edited</span>`;
    }

    // Council deliberation badge & drawer
    let councilBadge = "";
    let councilToggleBtn = "";
    let councilDrawerHtml = "";
    let decompBadges = "";

    if (seg.council) {
      const agreeType = seg.council.agreement_type || "MAJORITY";
      const scorePct = Math.round((seg.council.consensus_score || 0.85) * 100);
      const isExpanded = expandedCouncilSet.has(index);

      // Feature 2.2.C: Circuit-Breaker Tripped Badge
      const isTripped = seg.loop_circuit_breaker_tripped || (seg.council && seg.council.loop_circuit_breaker_tripped);
      if (isTripped) {
        councilBadge = `<span class="badge-council badge-council-tripped" title="${seg.council.deliberation_notes || 'Autoregressive loop detected; defaulted to Acoustic Anchor'}">⚡ Loop Breaker</span>`;
      } else if (agreeType === "UNANIMOUS") {
        councilBadge = `<span class="badge-council badge-council-unanimous" title="${seg.council.deliberation_notes || 'All models agreed'}">⚖️ 100% Unanimous</span>`;
      } else if (agreeType === "CTC_ANCHORED") {
        councilBadge = `<span class="badge-council badge-council-ctc" title="${seg.council.deliberation_notes || 'Non-speech verified by CTC'}">⚓ CTC Anchored</span>`;
      } else if (agreeType === "MAJORITY") {
        councilBadge = `<span class="badge-council badge-council-majority" title="${seg.council.deliberation_notes || 'Majority consensus'}">⚖️ Majority (${scorePct}%)</span>`;
      } else {
        councilBadge = `<span class="badge-council badge-council-split" title="${seg.council.deliberation_notes || 'Split decision across jurors'}">⚖️ Split Decision</span>`;
      }

      // Feature 2.3.A: Orthogonal Confidence Decomposition badges
      const decomp = seg.confidence_decomposition || (seg.council && seg.council.confidence_decomposition);
      let decompMatrixHtml = "";
      if (decomp) {
        const physPct = Math.round((decomp.acoustic_score != null ? decomp.acoustic_score : 0.85) * 100);
        const semPct = Math.round((decomp.semantic_score != null ? decomp.semantic_score : 0.85) * 100);
        const physClass = (decomp.acoustic_score >= 0.70) ? 'badge-decomp-good' : ((decomp.acoustic_score >= 0.50) ? 'badge-decomp-warn' : 'badge-decomp-danger');
        const semClass = (decomp.semantic_score >= 0.72) ? 'badge-decomp-good' : ((decomp.semantic_score >= 0.52) ? 'badge-decomp-warn' : 'badge-decomp-danger');
        decompBadges = `
          <span class="badge-decomp ${physClass}" title="Physical Audio Quality: ${decomp.acoustic_grade} (SNR: ${decomp.snr_db} dB, Clip: ${decomp.clipping_pct}%)">🎙️ ${physPct}%</span>
          <span class="badge-decomp ${semClass}" title="Linguistic Consensus: ${decomp.semantic_grade} (${decomp.quadrant_label || ''})">⚖️ ${semPct}%</span>
        `;
        decompMatrixHtml = `
          <div class="council-decomp-matrix">
            <div class="decomp-col">
              <div class="decomp-header">🎙️ Physical Acoustic Clarity (${decomp.acoustic_grade})</div>
              <div class="decomp-val">${physPct}% Clarity · SNR: ${decomp.snr_db} dB · Clip: ${decomp.clipping_pct}%</div>
            </div>
            <div class="decomp-col">
              <div class="decomp-header">⚖️ Linguistic Consensus (${decomp.semantic_grade})</div>
              <div class="decomp-val">${semPct}% Agreement · <span class="decomp-quadrant-tag">${decomp.quadrant || 'MATRIX'}</span></div>
            </div>
          </div>
        `;
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
            ${decompMatrixHtml}
            <div class="council-votes-list">${votesHtml}</div>
            ${disputedHtml}
            <div class="council-notes-text">📝 ${seg.council.deliberation_notes || ''}</div>
          </div>
        `;
      }
    }

    // Audition sample button
    let auditionBtn = "";
    if (seg.slowed_audio_used) {
      auditionBtn = `<button class="btn-audition" data-slow="true" title="Audition exact 0.75x time-stretched audio sample">🐢 0.75x</button>`;
    } else {
      auditionBtn = `<button class="btn-audition" data-slow="false" title="Audition this segment in Model Classification Ingest">🎧 Model</button>`;
    }

    // Delete chunk button
    const deleteBtn = `<button class="btn-delete-segment" data-index="${index}" title="Delete this chunk from transcript">🗑️</button>`;

    block.innerHTML = `
      <div class="segment-header">
        <span class="speaker-badge ${spkClass}">${displayName}</span>
        <span class="timestamp-pill">[${formatSeconds(seg.start)} - ${formatSeconds(seg.end)}]</span>
        ${badgeExtras}
        ${decompBadges}
        ${councilBadge}
        ${councilToggleBtn}
        ${auditionBtn}
        ${deleteBtn}
      </div>
      <div class="segment-text" contenteditable="true" spellcheck="false" data-index="${index}">${seg.text}</div>
      ${councilDrawerHtml}
    `;

    // Toggle Council Drawer
    const cToggle = block.querySelector(".btn-council-toggle");
    if (cToggle) {
      cToggle.addEventListener("click", (e) => {
        e.stopPropagation();
        const segIdx = parseInt(cToggle.dataset.index, 10);
        if (expandedCouncilSet.has(segIdx)) expandedCouncilSet.delete(segIdx);
        else expandedCouncilSet.add(segIdx);
        renderTranscriptFeed();
      });
    }

    // Adopt Juror Hypothesis
    block.querySelectorAll(".btn-adopt-hyp").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const segIdx = parseInt(btn.dataset.seg, 10);
        const adoptedText = decodeURIComponent(btn.dataset.text);
        if (currentSegments[segIdx]) {
          currentSegments[segIdx].text = adoptedText;
          currentSegments[segIdx].edited = true;
          renderTranscriptFeed();
          if (currentTaskId) {
            fetch(`/api/tasks/${currentTaskId}/segments/${segIdx}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text: adoptedText })
            }).catch(console.warn);
          }
        }
      });
    });

    // Delete Chunk button
    const delBtn = block.querySelector(".btn-delete-segment");
    if (delBtn) {
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const segIdx = parseInt(delBtn.dataset.index, 10);
        if (confirm(`Delete chunk [${formatSeconds(seg.start)} - ${formatSeconds(seg.end)}]?`)) {
          currentSegments.splice(segIdx, 1);
          if (tabTranscriptBadge) tabTranscriptBadge.innerText = currentSegments.length;
          renderSpeakerSidebar();
          renderTranscriptFeed();
          if (currentTaskId) {
            fetch(`/api/tasks/${currentTaskId}/segments/${segIdx}`, { method: "DELETE" }).catch(console.warn);
          }
        }
      });
    }

    // Audition chunk button logic
    const audBtn = block.querySelector(".btn-audition");
    if (audBtn) {
      audBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const isSlow = audBtn.dataset.slow === "true";
        if (isSlow) {
          if (chunkAuditionAudio) chunkAuditionAudio.pause();
          chunkAuditionAudio = new Audio(`/api/audio/${currentTaskId}/chunk/${index}`);
          audBtn.innerText = "🔊 Playing...";
          chunkAuditionAudio.play().catch(err => console.warn(err));
          chunkAuditionAudio.onended = () => { audBtn.innerText = "🐢 0.75x"; };
        } else {
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

    // Click block to jump both players
    block.addEventListener("click", (e) => {
      if (e.target.classList.contains("segment-text") || e.target.classList.contains("btn-audition") || e.target.classList.contains("btn-council-toggle") || e.target.classList.contains("btn-adopt-hyp") || e.target.classList.contains("btn-delete-segment")) return;
      if (wavesurferOrig) {
        wavesurferOrig.setTime(seg.start);
        if (wavesurferModel) wavesurferModel.setTime(seg.start);
        wavesurferOrig.play();
        if (wavesurferModel) wavesurferModel.play();
      }
    });

    // Contenteditable events & keyboard shortcuts
    const textEl = block.querySelector(".segment-text");
    textEl.addEventListener("blur", () => {
      const newTxt = textEl.innerText.trim();
      if (newTxt !== seg.text) {
        seg.text = newTxt;
        seg.edited = true;
        if (currentTaskId) {
          fetch(`/api/tasks/${currentTaskId}/segments/${index}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: newTxt })
          }).catch(console.warn);
        }
      }
    });

    textEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        textEl.blur();
        // Focus next segment text if available
        const nextBlock = transcriptFeed.querySelector(`.segment-block[data-index="${index + 1}"]`);
        if (nextBlock) {
          const nextText = nextBlock.querySelector(".segment-text");
          if (nextText) nextText.focus();
        }
      }
    });

    transcriptFeed.appendChild(block);
  });
}

function syncActiveSegment(currentTime, forceScroll = false) {
  const blocks = transcriptFeed.querySelectorAll(".segment-block");
  blocks.forEach((b) => {
    const start = parseFloat(b.dataset.start);
    const end = parseFloat(b.dataset.end);
    if (currentTime >= start && currentTime <= end) {
      if (!b.classList.contains("active") || forceScroll) {
        b.classList.add("active");
        b.scrollIntoView({ behavior: "smooth", block: "nearest" });

        // Update floating snippet
        const idx = parseInt(b.dataset.index, 10);
        if (currentSegments[idx]) {
          const spk = currentSegments[idx].speaker || "Speaker --";
          if (floatingSpeaker) floatingSpeaker.innerText = speakerAliases[spk] || spk;
          if (floatingSnippet) floatingSnippet.innerText = `"${currentSegments[idx].text}"`;
        }
      }
    } else {
      b.classList.remove("active");
    }
  });
}

if (searchInput) searchInput.addEventListener("input", () => renderTranscriptFeed());

// --- 10. Speaker Aliases Modal Management ---
if (btnRenameModal) {
  btnRenameModal.addEventListener("click", () => {
    const speakers = Array.from(new Set(currentSegments.map(s => s.speaker || "Speaker 0"))).sort();
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
}

if (btnCancelRename) btnCancelRename.addEventListener("click", () => renameModal.style.display = "none");

if (btnSaveAliases) {
  btnSaveAliases.addEventListener("click", () => {
    const inputs = aliasInputsContainer.querySelectorAll(".alias-input");
    inputs.forEach(inp => {
      const original = inp.dataset.original;
      const val = inp.value.trim();
      if (val) speakerAliases[original] = val;
      else delete speakerAliases[original];
    });
    renameModal.style.display = "none";
    renderSpeakerSidebar();
    renderTranscriptFeed();
  });
}

// --- 11. Export .TXT ---
if (btnExportTxt) {
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
}

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
// 12. Live Memory Trace & Execution Log Console
// ==========================================================================
let telemetryCadenceSeconds = 2;
let telemetryTimer = null;
let activeLogFilter = "ALL";
let cachedTraceSamples = [];
let cachedLogEntries = [];

const tabBtnTrace = document.getElementById("tabBtnTrace");
const tabBtnLogs = document.getElementById("tabBtnLogs");
const paneTrace = document.getElementById("paneTrace");
const paneLogs = document.getElementById("paneLogs");
const telemetryPulse = document.getElementById("telemetryPulse");

const traceActiveTask = document.getElementById("traceActiveTask");
const traceCurrentStage = document.getElementById("traceCurrentStage");
const traceLiveAppRam = document.getElementById("traceLiveAppRam");
const traceLiveWithoutSuiteRam = document.getElementById("traceLiveWithoutSuiteRam");
const traceLiveRam = document.getElementById("traceLiveRam");
const traceLiveVram = document.getElementById("traceLiveVram");
const tracePeakMem = document.getElementById("tracePeakMem");
const traceTableBody = document.getElementById("traceTableBody");
const traceAutoScroll = document.getElementById("traceAutoScroll");
const traceCountText = document.getElementById("traceCountText");
const vramTraceCanvas = document.getElementById("vramTraceCanvas");
const ramTraceCanvas = document.getElementById("ramTraceCanvas");

const terminalBody = document.getElementById("terminalBody");
const logsAutoScroll = document.getElementById("logsAutoScroll");
const logCountText = document.getElementById("logCountText");
const logLevelFilters = document.getElementById("logLevelFilters");
const logSearchInput = document.getElementById("logSearchInput");

const cadenceBtnGroup = document.getElementById("cadenceBtnGroup");
const btnExportTraceCsv = document.getElementById("btnExportTraceCsv");
const btnExportLogsCsv = document.getElementById("btnExportLogsCsv");
const btnExportLogsTxt = document.getElementById("btnExportLogsTxt");
const btnClearTelemetry = document.getElementById("btnClearTelemetry");
const btnClearMemTelemetry = document.getElementById("btnClearMemTelemetry");

const tabBtnDeepMem = document.getElementById("tabBtnDeepMem");
const paneDeepMem = document.getElementById("paneDeepMem");
const tabBtnSupervisor = document.getElementById("tabBtnSupervisor");
const paneSupervisor = document.getElementById("paneSupervisor");

function initTelemetryTabs() {
  if (!tabBtnTrace || !tabBtnLogs) return;

  const setTelemetrySubTab = (activeBtn, activePane) => {
    [tabBtnTrace, tabBtnLogs, tabBtnDeepMem, tabBtnSupervisor].forEach(btn => {
      if (btn) btn.classList.toggle("active", btn === activeBtn);
    });
    [paneTrace, paneLogs, paneDeepMem, paneSupervisor].forEach(pane => {
      if (pane) pane.classList.toggle("active", pane === activePane);
    });
  };

  tabBtnTrace.addEventListener("click", () => {
    setTelemetrySubTab(tabBtnTrace, paneTrace);
    setTimeout(() => {
      if (cachedTraceSamples && cachedTraceSamples.length > 0) renderTraceGraph(cachedTraceSamples);
    }, 50);
  });

  tabBtnLogs.addEventListener("click", () => {
    setTelemetrySubTab(tabBtnLogs, paneLogs);
  });

  if (tabBtnDeepMem) {
    tabBtnDeepMem.addEventListener("click", () => {
      setTelemetrySubTab(tabBtnDeepMem, paneDeepMem);
      fetchDeepMemoryTrace();
    });
  }

  if (tabBtnSupervisor) {
    tabBtnSupervisor.addEventListener("click", () => {
      setTelemetrySubTab(tabBtnSupervisor, paneSupervisor);
      fetchSupervisorData();
      fetchStorageBreakdown();
      fetchJournalData();
    });
  }
}

function initCadenceSelector() {
  if (!cadenceBtnGroup) return;
  const btns = cadenceBtnGroup.querySelectorAll(".btn-interval");
  btns.forEach((b) => {
    b.addEventListener("click", () => {
      btns.forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      telemetryCadenceSeconds = parseInt(b.dataset.interval, 10) || 2;
      startTelemetryPolling();
    });
  });
}

function startTelemetryPolling() {
  if (telemetryTimer) clearInterval(telemetryTimer);
  telemetryTimer = setInterval(fetchTelemetryData, telemetryCadenceSeconds * 1000);
}

let supervisorTelemetryTicks = 0;

async function fetchTelemetryData() {
  try {
    const tidParam = currentTaskId ? `&task_id=${currentTaskId}` : "";
    const [resTrace, resLogs] = await Promise.all([
      fetch(`/api/telemetry/trace?interval=${telemetryCadenceSeconds}${tidParam}`),
      fetch(`/api/telemetry/logs?level=${activeLogFilter}${tidParam}`)
    ]);

    if (resTrace.ok) {
      const traceData = await resTrace.json();
      renderTraceSummary(traceData.active_task, traceData.current, traceData.peak);
      if (traceData.samples && (cachedTraceSamples.length === 0 || traceData.samples.length !== cachedTraceSamples.length)) {
        cachedTraceSamples = traceData.samples;
        renderTraceTable(cachedTraceSamples);
        renderTraceGraph(cachedTraceSamples);
      }
    }

    if (resLogs.ok) {
      const logsData = await resLogs.json();
      if (logsData.logs && (cachedLogEntries.length === 0 || logsData.logs.length !== cachedLogEntries.length)) {
        cachedLogEntries = logsData.logs;
        renderTerminalLogs(cachedLogEntries);
      }
    }

    // Always fetch supervisor data so cards, metrics, and governor stay in real-time sync
    await fetchSupervisorData();

    supervisorTelemetryTicks++;
    if (paneSupervisor && paneSupervisor.classList.contains("active")) {
      // If user is watching the supervisor tab, update journal every 3s and storage every 10s
      if (supervisorTelemetryTicks % 2 === 0) {
        fetchJournalData();
      }
      if (supervisorTelemetryTicks % 5 === 0) {
        fetchStorageBreakdown();
      }
    } else if (paneDeepMem && paneDeepMem.classList.contains("active")) {
      if (supervisorTelemetryTicks % 2 === 0) {
        fetchDeepMemoryTrace();
      }
    }
  } catch (err) {
    console.warn("Failed to fetch telemetry data:", err);
  }
}

function renderTraceSummary(activeTask, curr, peak) {
  if (traceActiveTask) {
    if (activeTask && activeTask.filename) {
      const fn = activeTask.filename.length > 20 ? activeTask.filename.substring(0, 17) + "..." : activeTask.filename;
      traceActiveTask.innerText = fn;
      traceActiveTask.title = `${activeTask.filename} (${activeTask.id || ''})`;
    } else {
      traceActiveTask.innerText = "System Idle";
      traceActiveTask.title = "No active transcription";
    }
  }

  if (traceCurrentStage) {
    traceCurrentStage.innerText = (activeTask && activeTask.stage) ? activeTask.stage : "Idle";
  }

  if (curr) {
    const appRam = curr.proc_ram_used_gb ?? curr.app_ram_gb ?? 0;
    const sysUsed = curr.sys_ram_used_gb ?? curr.ram_used_gb ?? 0;
    const sysTotal = curr.sys_ram_total_gb ?? curr.ram_total_gb ?? 15.3;
    const withoutSuite = curr.sys_ram_without_suite_gb ?? Math.max(0, sysUsed - appRam);
    const vramAlloc = curr.allocated_gb ?? curr.vram_alloc_gb ?? 0;
    const vramRes = curr.reserved_gb ?? curr.vram_reserved_gb ?? vramAlloc;
    const vramTotal = curr.total_gb ?? curr.vram_total_gb ?? 7.6;

    if (traceLiveAppRam) traceLiveAppRam.innerText = `${appRam.toFixed(2)} GB`;
    if (traceLiveWithoutSuiteRam) traceLiveWithoutSuiteRam.innerText = `${withoutSuite.toFixed(2)} GB`;
    if (traceLiveRam) traceLiveRam.innerText = `${sysUsed.toFixed(1)} / ${sysTotal.toFixed(1)} GB`;
    if (traceLiveVram) traceLiveVram.innerText = `${vramAlloc.toFixed(2)}G (${vramRes.toFixed(1)}G res) / ${vramTotal.toFixed(1)} GB`;
  }

  if (tracePeakMem) {
    if (peak && peak.peak_vram_gb !== undefined && peak.peak_ram_gb !== undefined) {
      tracePeakMem.innerText = `${peak.peak_vram_gb.toFixed(2)}G / ${peak.peak_ram_gb.toFixed(1)}G`;
    } else if (curr) {
      const v = curr.reserved_gb ?? curr.vram_reserved_gb ?? 0;
      const r = curr.sys_ram_used_gb ?? curr.ram_used_gb ?? 0;
      tracePeakMem.innerText = `${v.toFixed(2)}G / ${r.toFixed(1)}G`;
    }
  }
}

function renderTraceTable(samples) {
  if (!traceTableBody || !samples) return;
  traceTableBody.innerHTML = "";
  samples.forEach((s) => {
    const row = document.createElement("tr");
    let statusClass = "status-idle";
    let statusLabel = (s.status || "IDLE").toUpperCase();
    if (statusLabel === "PROCESSING") statusClass = "status-processing";
    else if (statusLabel === "COMPLETED") statusClass = "status-completed";
    else if (statusLabel === "FAILED") statusClass = "status-failed";

    const elapsedStr = s.elapsed_str || (s.elapsed_s !== undefined ? `${Math.round(s.elapsed_s)}s` : "--");
    const taskIdDisplay = s.task_name || (s.task_id && s.task_id !== "idle" && s.task_id !== "system" ? s.task_id.substring(0, 8) + "..." : "system");
    const appRam = s.proc_ram_used_gb ?? s.app_ram_gb ?? 0;
    const sysRam = s.sys_ram_used_gb ?? s.ram_used_gb ?? 0;
    const sysPct = s.sys_ram_pct ?? s.ram_pct ?? 0;
    const withoutSuite = s.sys_ram_without_suite_gb ?? Math.max(0, sysRam - appRam);
    const vramAlloc = s.allocated_gb ?? s.vram_alloc_gb ?? 0;
    const vramPct = s.percent_used ?? s.vram_pct ?? 0;

    row.innerHTML = `
      <td style="font-family: monospace; font-size: 11px;">${escapeHtml(s.time_str || "--")}</td>
      <td style="font-family: monospace; font-size: 11px;">${escapeHtml(elapsedStr)}</td>
      <td style="max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(s.task_name || s.task_id || '')}">
        ${escapeHtml(taskIdDisplay)}
      </td>
      <td style="color: var(--text-primary); font-weight: 500;">${escapeHtml(s.stage || "--")}</td>
      <td style="font-weight: 600; color: #10b981;">${appRam.toFixed(2)} GB</td>
      <td style="font-weight: 600; color: #38bdf8;">${withoutSuite.toFixed(2)} GB</td>
      <td>${sysRam.toFixed(1)} GB (${Math.round(sysPct)}%)</td>
      <td style="color: var(--accent-light); font-weight: 600;">${vramAlloc.toFixed(2)} GB (${Math.round(vramPct)}%)</td>
      <td><span class="status-pill ${statusClass}">${escapeHtml(statusLabel)}</span></td>
    `;
    traceTableBody.appendChild(row);
  });

  if (traceCountText) traceCountText.innerText = `${samples.length} trace samples recorded`;
  if (traceAutoScroll && traceAutoScroll.checked) {
    const container = document.getElementById("traceTableContainer");
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function renderVramGraph(samples) {
  const canvas = vramTraceCanvas;
  if (!canvas || !samples || samples.length === 0) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const parent = canvas.parentElement;
  if (!parent) return;
  const rect = parent.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = rect.width || 450;
  const h = rect.height || 150;
  if (w <= 0 || h <= 0) return;

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  // Background
  ctx.fillStyle = "#0a0f0d";
  ctx.fillRect(0, 0, w, h);

  const padLeft = 38;
  const padRight = 14;
  const padTop = 14;
  const padBottom = 22;
  const plotW = w - padLeft - padRight;
  const plotH = h - padTop - padBottom;
  if (plotW <= 0 || plotH <= 0) { ctx.restore(); return; }

  // VRAM ceiling is 8.0 GB or max reserved observed
  let maxGB = 8.0;
  samples.forEach((s) => {
    const vres = s.vram_reserved_gb ?? 0;
    const alloc = s.allocated_gb ?? s.vram_alloc_gb ?? 0;
    if (vres > maxGB) maxGB = Math.ceil(vres * 1.1);
    if (alloc > maxGB) maxGB = Math.ceil(alloc * 1.1);
  });

  // Grid lines
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const ySteps = 4;
  for (let i = 0; i <= ySteps; i++) {
    const yVal = (maxGB / ySteps) * i;
    const yPos = padTop + plotH - (i / ySteps) * plotH;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, yPos);
    ctx.lineTo(w - padRight, yPos);
    ctx.stroke();

    ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
    ctx.fillText(`${yVal.toFixed(1)}G`, padLeft - 4, yPos);
  }

  // 5.5 GB Governor Emergency Ceiling Line
  if (maxGB >= 5.5) {
    const yCeil = padTop + plotH - (5.5 / maxGB) * plotH;
    ctx.strokeStyle = "rgba(239, 68, 68, 0.75)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padLeft, yCeil);
    ctx.lineTo(w - padRight, yCeil);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "rgba(239, 68, 68, 0.85)";
    ctx.textAlign = "right";
    ctx.fillText("5.5G Ceiling", w - padRight - 2, Math.max(padTop + 8, yCeil - 4));
  }

  const count = samples.length;
  function drawSeries(getValue, strokeColor, fillColor = null, lineWidth = 2) {
    if (count < 1) return;
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.lineJoin = "round";
    ctx.beginPath();

    for (let i = 0; i < count; i++) {
      const s = samples[i];
      const val = Math.max(0, Math.min(maxGB, getValue(s)));
      const x = padLeft + (count === 1 ? plotW / 2 : (i / (count - 1)) * plotW);
      const y = padTop + plotH - (val / maxGB) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (fillColor && count > 1) {
      ctx.lineTo(padLeft + plotW, padTop + plotH);
      ctx.lineTo(padLeft, padTop + plotH);
      ctx.closePath();
      ctx.fillStyle = fillColor;
      ctx.fill();
    }

    const last = samples[count - 1];
    const lastVal = Math.max(0, Math.min(maxGB, getValue(last)));
    const lx = padLeft + (count === 1 ? plotW / 2 : plotW);
    const ly = padTop + plotH - (lastVal / maxGB) * plotH;
    ctx.fillStyle = strokeColor;
    ctx.beginPath();
    ctx.arc(lx, ly, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // 1. VRAM Reserved Pool (Sky Blue)
  drawSeries((s) => s.vram_reserved_gb ?? 0, "#38bdf8", "rgba(56, 189, 248, 0.10)", 1.5);

  // 2. VRAM Allocated (Violet)
  drawSeries((s) => s.allocated_gb ?? s.vram_alloc_gb ?? 0, "#a855f7", "rgba(168, 85, 247, 0.22)", 2.5);

  // Time labels on X-axis
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
  if (count > 0) {
    ctx.textAlign = "left";
    ctx.fillText(samples[0].time_str || "", padLeft, padTop + plotH + 5);
    if (count > 12) {
      ctx.textAlign = "center";
      const mid = samples[Math.floor(count / 2)];
      ctx.fillText(mid.time_str || "", padLeft + plotW / 2, padTop + plotH + 5);
    }
    ctx.textAlign = "right";
    ctx.fillText(samples[count - 1].time_str || "", padLeft + plotW, padTop + plotH + 5);
  }

  ctx.restore();
}

function renderRamGraph(samples) {
  const canvas = ramTraceCanvas;
  if (!canvas || !samples || samples.length === 0) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const parent = canvas.parentElement;
  if (!parent) return;
  const rect = parent.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = rect.width || 450;
  const h = rect.height || 150;
  if (w <= 0 || h <= 0) return;

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  // Background
  ctx.fillStyle = "#0a0f0d";
  ctx.fillRect(0, 0, w, h);

  const padLeft = 38;
  const padRight = 14;
  const padTop = 14;
  const padBottom = 22;
  const plotW = w - padLeft - padRight;
  const plotH = h - padTop - padBottom;
  if (plotW <= 0 || plotH <= 0) { ctx.restore(); return; }

  // Scale: max of system RAM (min 16 GB)
  let maxGB = 16.0;
  samples.forEach((s) => {
    const sys = s.sys_ram_used_gb ?? s.ram_used_gb ?? 0;
    if (sys > maxGB) maxGB = Math.ceil(sys * 1.05);
  });

  // Grid lines
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const ySteps = 4;
  for (let i = 0; i <= ySteps; i++) {
    const yVal = (maxGB / ySteps) * i;
    const yPos = padTop + plotH - (i / ySteps) * plotH;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, yPos);
    ctx.lineTo(w - padRight, yPos);
    ctx.stroke();

    ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
    ctx.fillText(`${yVal.toFixed(0)}G`, padLeft - 4, yPos);
  }

  const count = samples.length;
  function drawSeries(getValue, strokeColor, fillColor = null, lineWidth = 2) {
    if (count < 1) return;
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.lineJoin = "round";
    ctx.beginPath();

    for (let i = 0; i < count; i++) {
      const s = samples[i];
      const val = Math.max(0, Math.min(maxGB, getValue(s)));
      const x = padLeft + (count === 1 ? plotW / 2 : (i / (count - 1)) * plotW);
      const y = padTop + plotH - (val / maxGB) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (fillColor && count > 1) {
      ctx.lineTo(padLeft + plotW, padTop + plotH);
      ctx.lineTo(padLeft, padTop + plotH);
      ctx.closePath();
      ctx.fillStyle = fillColor;
      ctx.fill();
    }

    const last = samples[count - 1];
    const lastVal = Math.max(0, Math.min(maxGB, getValue(last)));
    const lx = padLeft + (count === 1 ? plotW / 2 : plotW);
    const ly = padTop + plotH - (lastVal / maxGB) * plotH;
    ctx.fillStyle = strokeColor;
    ctx.beginPath();
    ctx.arc(lx, ly, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // 1. Total System RAM (Amber)
  drawSeries((s) => s.sys_ram_used_gb ?? s.ram_used_gb ?? 0, "#f59e0b", "rgba(245, 158, 11, 0.05)", 2);

  // 2. RAM Without Suite (Sky Blue)
  drawSeries((s) => {
    const total = s.sys_ram_used_gb ?? s.ram_used_gb ?? 0;
    const app = s.proc_ram_used_gb ?? s.app_ram_gb ?? 0;
    return s.sys_ram_without_suite_gb ?? Math.max(0, total - app);
  }, "#38bdf8", "rgba(56, 189, 248, 0.08)", 2);

  // 3. Suite Memory Only (Emerald)
  drawSeries((s) => s.proc_ram_used_gb ?? s.app_ram_gb ?? 0, "#10b981", "rgba(16, 185, 129, 0.20)", 2.5);

  // Time labels on X-axis
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
  if (count > 0) {
    ctx.textAlign = "left";
    ctx.fillText(samples[0].time_str || "", padLeft, padTop + plotH + 5);
    if (count > 12) {
      ctx.textAlign = "center";
      const mid = samples[Math.floor(count / 2)];
      ctx.fillText(mid.time_str || "", padLeft + plotW / 2, padTop + plotH + 5);
    }
    ctx.textAlign = "right";
    ctx.fillText(samples[count - 1].time_str || "", padLeft + plotW, padTop + plotH + 5);
  }

  ctx.restore();
}

function renderTraceGraph(samples) {
  if (!samples || samples.length === 0) return;
  renderVramGraph(samples);
  renderRamGraph(samples);
}

window.addEventListener("resize", () => {
  if (cachedTraceSamples && cachedTraceSamples.length > 0) {
    renderTraceGraph(cachedTraceSamples);
  }
});

function renderTerminalLogs(entries) {
  if (!terminalBody || !entries) return;
  terminalBody.innerHTML = "";
  const query = logSearchInput ? logSearchInput.value.toLowerCase() : "";

  entries.forEach((e) => {
    if (query && !e.message.toLowerCase().includes(query) && !e.level.toLowerCase().includes(query)) {
      return;
    }

    const row = document.createElement("div");
    row.className = "log-entry";

    let badgeClass = "badge-info";
    if (e.level === "STAGE") badgeClass = "badge-stage";
    else if (e.level === "CHUNK") badgeClass = "badge-chunk";
    else if (e.level === "MEM") badgeClass = "badge-mem";
    else if (e.level === "WARN") badgeClass = "badge-warn";
    else if (e.level === "ERROR") badgeClass = "badge-error";
    else if (e.level === "SUCCESS") badgeClass = "badge-success";

    const ramUsed = e.ram_used_gb ?? e.sys_ram_used_gb ?? 0;
    const vramAlloc = e.vram_alloc_gb ?? e.allocated_gb ?? 0;

    row.innerHTML = `
      <span class="log-time">${escapeHtml(e.time_str || "--")}</span>
      <span class="log-level-badge ${badgeClass}">${escapeHtml(e.level || "INFO")}</span>
      <span class="log-msg">${escapeHtml(e.message || "")}</span>
      <span class="log-mem-tag">RAM: ${ramUsed.toFixed(2)}G | VRAM: ${vramAlloc.toFixed(2)}G</span>
    `;
    terminalBody.appendChild(row);
  });

  if (logCountText) logCountText.innerText = `${entries.length} log entries`;
  if (logsAutoScroll && logsAutoScroll.checked) {
    const container = document.getElementById("terminalContainer");
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function initLogLevelFilters() {
  if (!logLevelFilters) return;
  const btns = logLevelFilters.querySelectorAll(".log-filter-btn");
  btns.forEach((b) => {
    b.addEventListener("click", () => {
      btns.forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      activeLogFilter = b.dataset.level || "ALL";
      fetchTelemetryData();
    });
  });
  if (logSearchInput) {
    logSearchInput.addEventListener("input", () => renderTerminalLogs(cachedLogEntries));
  }
}

function initTelemetryExports() {
  if (btnExportTraceCsv) {
    btnExportTraceCsv.addEventListener("click", () => {
      const tid = currentTaskId ? `?task_id=${currentTaskId}` : "";
      window.open(`/api/telemetry/export/trace.csv${tid}`, "_blank");
    });
  }
  if (btnExportLogsCsv) {
    btnExportLogsCsv.addEventListener("click", () => {
      const tid = currentTaskId ? `?task_id=${currentTaskId}` : "";
      window.open(`/api/telemetry/export/logs.csv${tid}`, "_blank");
    });
  }
  if (btnExportLogsTxt) {
    btnExportLogsTxt.addEventListener("click", () => {
      const tid = currentTaskId ? `?task_id=${currentTaskId}` : "";
      window.open(`/api/telemetry/export/logs.txt${tid}`, "_blank");
    });
  }
  if (btnClearTelemetry) {
    btnClearTelemetry.addEventListener("click", async () => {
      try { await fetch("/api/telemetry/clear", { method: "POST" }); } catch (err) {}
      cachedTraceSamples = [];
      cachedLogEntries = [];
      if (traceTableBody) traceTableBody.innerHTML = "";
      if (terminalBody) terminalBody.innerHTML = "";
      fetchTelemetryData();
    });
  }
  if (btnClearMemTelemetry) {
    btnClearMemTelemetry.addEventListener("click", async () => {
      const orig = btnClearMemTelemetry.innerText;
      btnClearMemTelemetry.innerText = "🧹 Trimming...";
      try {
        const res = await fetch("/api/cache/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: "all" })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status !== "error") {
          btnClearMemTelemetry.innerText = "✨ Cleaned!";
          setTimeout(() => { btnClearMemTelemetry.innerText = orig; }, 1200);
          fetchMemoryStats();
          fetchTelemetryData();
          fetchCacheBreakdown();
        } else {
          console.warn("Telemetry clear failed:", res.status, data);
          btnClearMemTelemetry.innerText = "⚠️ Failed";
          setTimeout(() => { btnClearMemTelemetry.innerText = orig; }, 1500);
        }
      } catch (e) {
        console.error("Telemetry clear error:", e);
        btnClearMemTelemetry.innerText = "⚠️ Error";
        setTimeout(() => { btnClearMemTelemetry.innerText = orig; }, 1500);
      }
    });
  }

  const btnDropPageCacheNav = document.getElementById("btnDropPageCacheNav");
  if (btnDropPageCacheNav) {
    btnDropPageCacheNav.addEventListener("click", (e) => triggerPageCachePurge(e.currentTarget));
  }
  const btnDropPageCacheCard = document.getElementById("btnDropPageCacheCard");
  if (btnDropPageCacheCard) {
    btnDropPageCacheCard.addEventListener("click", (e) => triggerPageCachePurge(e.currentTarget));
  }
}

// ==========================================================================
// 8. Deep Memory Telemetry & Process Inspection
// ==========================================================================
async function fetchDeepMemoryTrace() {
  try {
    const res = await fetch("/api/telemetry/deep-memory");
    if (!res.ok) return;
    const data = await res.json();

    // 1. Suite process memory
    const proc = data.process || {};
    const deepRssVal = document.getElementById("deepRssVal");
    const deepVmsVal = document.getElementById("deepVmsVal");
    const deepSharedVal = document.getElementById("deepSharedVal");
    const deepDataVal = document.getElementById("deepDataVal");
    if (deepRssVal) deepRssVal.innerText = `${proc.rss_mb || 0} MB (${proc.rss_gb || 0} GB)`;
    if (deepVmsVal) deepVmsVal.innerText = `${proc.vms_mb || 0} MB`;
    if (deepSharedVal) deepSharedVal.innerText = `${proc.shared_mb || 0} MB`;
    if (deepDataVal) deepDataVal.innerText = `${proc.data_mb || 0} MB`;

    // 2. GPU VRAM Breakdown
    const gpu = data.gpu || {};
    const deepGpuDevice = document.getElementById("deepGpuDevice");
    const deepGpuAlloc = document.getElementById("deepGpuAlloc");
    const deepGpuReserved = document.getElementById("deepGpuReserved");
    const deepGpuExternal = document.getElementById("deepGpuExternal");
    const deepGpuFree = document.getElementById("deepGpuFree");
    if (deepGpuDevice) deepGpuDevice.innerText = gpu.device_name || "CPU";
    if (deepGpuAlloc) deepGpuAlloc.innerText = `${gpu.allocated_gb || 0} GB`;
    if (deepGpuReserved) deepGpuReserved.innerText = `${gpu.reserved_gb || 0} GB`;
    if (deepGpuExternal) deepGpuExternal.innerText = `${gpu.external_os_gb || 0} GB`;
    if (deepGpuFree) deepGpuFree.innerText = `${gpu.free_gb || 0} GB / ${gpu.total_gb || 0} GB (${gpu.utilization_percent || 0}%)`;

    // 3. Host System RAM
    const sys = data.system_ram || {};
    const deepSysTotal = document.getElementById("deepSysTotal");
    const deepSysUsed = document.getElementById("deepSysUsed");
    const deepSysAppRss = document.getElementById("deepSysAppRss");
    const deepSysWithoutSuite = document.getElementById("deepSysWithoutSuite") || document.getElementById("deepSysOtherRss");
    const deepSysKernelZswap = document.getElementById("deepSysKernelZswap");
    const deepSysFree = document.getElementById("deepSysFree");
    const deepSysPct = document.getElementById("deepSysPct");
    const deepProcSummary = document.getElementById("deepProcSummary");

    if (deepSysTotal) deepSysTotal.innerText = `${sys.total_gb || 0} GB`;
    if (deepSysUsed) deepSysUsed.innerText = `${sys.used_gb || 0} GB`;
    if (deepSysAppRss) deepSysAppRss.innerText = `${sys.app_rss_gb || proc.rss_gb || 0} GB (${proc.rss_mb || 0} MB)`;
    const withoutSuiteGb = sys.sys_ram_without_suite_gb ?? ((sys.used_gb !== undefined && sys.app_rss_gb !== undefined) ? Math.max(0, sys.used_gb - sys.app_rss_gb).toFixed(2) : (sys.other_procs_gb || 0));
    if (deepSysWithoutSuite) deepSysWithoutSuite.innerText = `${withoutSuiteGb} GB (external OS & apps)`;
    if (deepSysKernelZswap) {
      const parts = [];
      if (sys.zswap_gb) parts.push(`zswap: ${sys.zswap_gb} GB`);
      if (sys.shared_gb) parts.push(`shm: ${sys.shared_gb} GB`);
      deepSysKernelZswap.innerText = parts.length > 0 ? parts.join(" | ") : `~${Math.max(0, (sys.used_gb - (sys.all_procs_gb || 0)).toFixed(2))} GB`;
    }
    const deepSysCached = document.getElementById("deepSysCached");
    if (deepSysCached) deepSysCached.innerText = `${sys.cached_gb || 0} GB`;
    if (deepSysFree) deepSysFree.innerText = `${sys.free_gb || 0} GB`;
    if (deepSysPct) deepSysPct.innerText = `${sys.percent || 0}%`;

    // 4. Top 10 System Processes
    const tbody = document.getElementById("deepProcessesBody");
    if (tbody && data.top_processes) {
      tbody.innerHTML = "";
      let top10SumMb = 0;
      data.top_processes.forEach((p) => {
        top10SumMb += (p.rss_mb || 0);
        const tr = document.createElement("tr");
        const isSuite = (p.pid === proc.pid) || (p.name && p.name.includes("transcript"));
        if (isSuite) {
          tr.style.backgroundColor = "rgba(105, 167, 121, 0.12)";
          tr.style.fontWeight = "600";
        }
        tr.innerHTML = `
          <td><code>${p.pid}</code></td>
          <td>${escapeHtml(p.name)} ${isSuite ? '<span class="status-badge-active" style="font-size:9px; margin-left:6px;">This Suite</span>' : ''}</td>
          <td>${escapeHtml(p.user)}</td>
          <td>${p.rss_mb} MB</td>
          <td>${p.rss_gb} GB</td>
          <td>${p.percent}%</td>
        `;
        tbody.appendChild(tr);
      });

      if (deepProcSummary) {
        const top10Gb = (top10SumMb / 1024).toFixed(2);
        const allGb = sys.all_procs_gb ? `${sys.all_procs_gb} GB` : `${top10Gb} GB`;
        const totalCount = sys.total_procs_count || data.top_processes.length;
        deepProcSummary.innerText = `Top 10 processes: ${top10Gb} GB | All ${totalCount} active processes: ${allGb}`;
      }
    }
  } catch (err) {
    console.warn("Failed to fetch deep memory trace:", err);
  }
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      console.warn("navigator.clipboard failed, falling back to textarea execCommand:", e);
    }
  }
  try {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.top = "-9999px";
    textArea.style.left = "-9999px";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    const success = document.execCommand("copy");
    document.body.removeChild(textArea);
    return success;
  } catch (err) {
    console.error("execCommand copy failed:", err);
    return false;
  }
}

async function copyProcessAndMemoryToClipboard(triggerBtn) {
  const btn = triggerBtn || document.getElementById("btnCopyProcessesMem") || document.getElementById("btnCopyMemoryAudit");
  const origText = btn ? btn.innerHTML : "📋 Copy All Processes & RAM Info";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Generating Audit...";
  }

  try {
    const res = await fetch("/api/telemetry/deep-memory/export?format=txt");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();

    const ok = await copyTextToClipboard(text);
    if (ok) {
      if (btn) {
        btn.innerHTML = "✅ Copied to Clipboard!";
        btn.style.borderColor = "#10b981";
        btn.style.color = "#10b981";
      }
      addNotification("Audit Copied", "Entire process list and memory usage copied to clipboard.", "success");
      setTimeout(() => {
        if (btn) {
          btn.innerHTML = origText;
          btn.disabled = false;
          btn.style.borderColor = "";
          btn.style.color = "";
        }
      }, 2500);
    } else {
      throw new Error("Clipboard copy permission denied");
    }
  } catch (err) {
    console.error("Failed to copy memory audit:", err);
    if (btn) {
      btn.innerHTML = "❌ Copy Failed";
      btn.disabled = false;
      setTimeout(() => {
        btn.innerHTML = origText;
      }, 2000);
    }
    addNotification("Copy Error", "Could not copy process list to clipboard.", "error");
  }
}

async function triggerPageCachePurge(triggerBtn) {
  const navBtn = document.getElementById("btnDropPageCacheNav");
  const cardBtn = document.getElementById("btnDropPageCacheCard");
  const buttons = [navBtn, cardBtn].filter(Boolean);

  buttons.forEach((b) => {
    b.disabled = true;
    b.setAttribute("data-orig-text", b.innerHTML);
    b.innerHTML = "⏳ Purging OS Cache...";
  });

  try {
    const res = await fetch("/api/memory/drop-cache", { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const freedMb = data.freed_cached_mb || 0;
    const freedGb = data.freed_cached_gb || (freedMb / 1024).toFixed(2);
    const filesCount = data.files_purged || 0;

    buttons.forEach((b) => {
      if (freedMb > 0) {
        b.innerHTML = `✅ Purged ${freedMb} MB Cache!`;
        b.style.borderColor = "#10b981";
        b.style.color = "#10b981";
      } else {
        b.innerHTML = "✅ Cache Already Clean!";
      }
    });

    if (freedMb > 0) {
      addNotification("Page Cache Purged", `Evicted ${freedMb} MB (${freedGb} GB) across ${filesCount} checkpoint & audio files.`, "success");
    } else {
      addNotification("Page Cache Clean", `All ${filesCount} model & audio cache files are already purged from RAM.`, "info");
    }

    if (typeof fetchMemoryStats === "function") fetchMemoryStats();
    if (typeof fetchDeepMemoryTrace === "function") fetchDeepMemoryTrace();

    setTimeout(() => {
      buttons.forEach((b) => {
        b.innerHTML = b.getAttribute("data-orig-text") || "⚡ Purge Page Cache";
        b.disabled = false;
        b.style.borderColor = "";
        b.style.color = "";
      });
    }, 2500);
  } catch (err) {
    console.error("Failed to purge page cache:", err);
    buttons.forEach((b) => {
      b.innerHTML = "❌ Purge Failed";
      b.disabled = false;
      setTimeout(() => {
        b.innerHTML = b.getAttribute("data-orig-text") || "⚡ Purge Page Cache";
      }, 2000);
    });
    addNotification("Purge Error", "Could not purge page cache.", "error");
  }
}

// ==========================================================================
// 9. Model Manager & Checkpoints Client
// ==========================================================================
let modelCatalogData = null;
let downloadStatusTimer = null;

async function initModelManager() {
  const btnSaveRoster = document.getElementById("btnSaveRoster");
  const btnInstallCustomModel = document.getElementById("btnInstallCustomModel");
  const btnGoToModels = document.getElementById("btnGoToModels");
  const btnRefreshDeepMem = document.getElementById("btnRefreshDeepMem");
  const btnCopyProcessesMem = document.getElementById("btnCopyProcessesMem");
  const btnCopyMemoryAudit = document.getElementById("btnCopyMemoryAudit");

  if (btnGoToModels) {
    btnGoToModels.addEventListener("click", () => switchTab("paneModels"));
  }

  if (btnSaveRoster) {
    btnSaveRoster.addEventListener("click", saveActiveRoster);
  }

  if (btnInstallCustomModel) {
    btnInstallCustomModel.addEventListener("click", async () => {
      const customId = document.getElementById("customModelId")?.value.trim();
      const customFw = document.getElementById("customModelFramework")?.value;
      const customRole = document.getElementById("customModelRole")?.value;
      if (!customId) {
        alert("Please enter a Hugging Face Repo ID or NeMo model name.");
        return;
      }
      await startModelInstall(customId, customFw, customRole);
    });
  }

  if (btnRefreshDeepMem) {
    btnRefreshDeepMem.addEventListener("click", fetchDeepMemoryTrace);
  }

  if (btnCopyProcessesMem) {
    btnCopyProcessesMem.addEventListener("click", (e) => copyProcessAndMemoryToClipboard(e.currentTarget));
  }

  if (btnCopyMemoryAudit) {
    btnCopyMemoryAudit.addEventListener("click", (e) => copyProcessAndMemoryToClipboard(e.currentTarget));
  }
}

async function fetchModelData() {
  try {
    const res = await fetch("/api/models");
    if (!res.ok) return;
    const data = await res.json();
    modelCatalogData = data;

    renderModelRoster(data.roster, data.presets, data.checkpoints);
    renderPresetCatalog(data.presets);
    renderCheckpointsTable(data.checkpoints, data.roster);

    const totalBadge = document.getElementById("checkpointTotalDisk");
    if (totalBadge) {
      totalBadge.innerText = `Total: ${data.total_checkpoint_gb || 0} GB`;
    }

    if (data.install_status && (data.install_status.is_downloading || (data.install_status.queue && data.install_status.queue.length > 0))) {
      showDownloadProgress(data.install_status);
      updatePresetButtonStates(data.install_status);
      startDownloadPolling();
    }
  } catch (err) {
    console.warn("Failed to fetch models data:", err);
  }
}

function renderModelRoster(roster, presets, checkpoints) {
  if (!roster) return;

  const selectCanary = document.getElementById("rosterSelectCanary");
  const selectWhisper = document.getElementById("rosterSelectWhisper");
  const selectConformer = document.getElementById("rosterSelectConformer");
  const selectParakeet = document.getElementById("rosterSelectParakeet");
  const selectDiarizer = document.getElementById("rosterSelectDiarizer");
  const selectBoost = document.getElementById("rosterSelectBoost");

  const buildOptions = (role, activeVal) => {
    const rolePresets = (presets || []).filter(p => p.role === role);
    let html = "";
    rolePresets.forEach(p => {
      const isSel = (p.id === activeVal);
      const tag = p.is_installed ? "✓ Installed" : "Not Cached";
      html += `<option value="${p.id}" ${isSel ? "selected" : ""}>${p.name} [${p.parameters}] (${tag})</option>`;
    });
    if (activeVal && !rolePresets.some(p => p.id === activeVal)) {
      html += `<option value="${activeVal}" selected>${activeVal} (Active Custom)</option>`;
    }
    return html;
  };

  if (selectCanary) selectCanary.innerHTML = buildOptions("speech_llm", roster.model_name);
  if (selectWhisper) selectWhisper.innerHTML = buildOptions("whisper", roster.whisper_model);
  if (selectConformer) selectConformer.innerHTML = buildOptions("conformer", roster.conformer_model);
  if (selectParakeet) selectParakeet.innerHTML = buildOptions("parakeet", roster.parakeet_model);
  if (selectDiarizer && roster.default_diarizer) selectDiarizer.value = roster.default_diarizer;
  if (selectBoost && roster.vocal_boost_level) selectBoost.value = roster.vocal_boost_level;

  const selectAudex = document.getElementById("rosterSelectAudex");
  if (selectAudex) {
    if (roster.enable_audex_adjudicator) {
      selectAudex.value = roster.audex_model_id || "nvidia/Nemotron-Labs-Audex-2B";
    } else {
      selectAudex.value = "disabled";
    }
  }

  // Sync Studio Vocal Boost selector
  const studioBoost = document.getElementById("vocalBoostSelect");
  if (studioBoost && roster.vocal_boost_level) {
    studioBoost.value = roster.vocal_boost_level;
  }

  // Update Studio Council Roster Pills
  const pillLead = document.getElementById("pillLeadJustice");
  const pillCross = document.getElementById("pillCrossExaminer");
  const pillAnchor = document.getElementById("pillAnchor");
  const pillTrans = document.getElementById("pillTransducer");

  if (pillLead) pillLead.innerText = `Pass 1: ${roster.model_name ? roster.model_name.split("/").pop() : "Canary-Qwen-2.5B"}`;
  if (pillCross) pillCross.innerText = `Pass 2: ${roster.whisper_model ? roster.whisper_model.split("/").pop() : "Whisper-Large-v3"}`;
  if (pillAnchor) pillAnchor.innerText = `Pass 3A: ${roster.conformer_model ? roster.conformer_model.split("/").pop() : "Conformer-CTC"}`;
  if (pillTrans) pillTrans.innerText = `Pass 3B: ${roster.parakeet_model ? roster.parakeet_model.split("/").pop() : "Parakeet-TDT"}`;
}

function renderPresetCatalog(presets) {
  const container = document.getElementById("presetsGrid");
  if (!container || !presets) return;

  container.innerHTML = "";
  presets.forEach(p => {
    const card = document.createElement("div");
    card.className = "preset-card";

    let roleClass = "stage-lead";
    if (p.role === "whisper") roleClass = "stage-cross";
    else if (p.role === "conformer") roleClass = "stage-ctc";
    else if (p.role === "parakeet") roleClass = "stage-tdt";
    else if (p.role === "diarizer") roleClass = "stage-diar";

    card.innerHTML = `
      <div class="preset-header">
        <div class="preset-name">${escapeHtml(p.name)}</div>
        <span class="preset-badge ${roleClass}">${p.parameters}</span>
      </div>
      <div class="preset-desc">${escapeHtml(p.description)}</div>
      <div class="preset-meta">
        <span>~${p.size_gb} GB</span>
        ${p.is_installed ? '<span class="preset-installed-tag">✓ Installed</span>' : `<button class="btn-preset-install" data-id="${p.id}" data-fw="${p.framework}" data-role="${p.role}">⬇️ Install</button>`}
      </div>
    `;

    const btn = card.querySelector(".btn-preset-install");
    if (btn) {
      btn.addEventListener("click", () => {
        startModelInstall(p.id, p.framework, p.role);
      });
    }

    container.appendChild(card);
  });
  if (modelCatalogData && modelCatalogData.install_status) {
    updatePresetButtonStates(modelCatalogData.install_status);
  }
}

function renderCheckpointsTable(checkpoints, roster) {
  const tbody = document.getElementById("checkpointsTableBody");
  if (!tbody) return;

  tbody.innerHTML = "";
  if (!checkpoints || checkpoints.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 20px;">No model checkpoints found in local caches.</td></tr>`;
    return;
  }

  const activeSet = new Set(Object.values(roster || {}));

  checkpoints.forEach(cp => {
    const tr = document.createElement("tr");
    const isActive = cp.is_active || activeSet.has(cp.id) || activeSet.has(cp.raw_name);

    tr.innerHTML = `
      <td>
        <div style="font-weight: 600;">${escapeHtml(cp.name)}</div>
        <div style="font-size: 10px; color: var(--text-muted); font-family: monospace;">${escapeHtml(cp.id)}</div>
      </td>
      <td><span class="roster-stage-tag" style="background: rgba(255,255,255,0.06); color: var(--text-secondary);">${escapeHtml(cp.role_display || cp.role)}</span></td>
      <td><code style="font-size: 11px;">${cp.framework === "huggingface" ? "HF Hub" : "NeMo"}</code></td>
      <td><strong>${cp.size_gb} GB</strong></td>
      <td style="color: var(--text-muted); font-size: 11px;">${cp.last_modified}</td>
      <td>
        ${isActive ? '<span class="status-badge-active">Active In Pipeline</span>' : '<span class="status-badge-installed">Cached</span>'}
      </td>
      <td>
        <button class="btn-delete-checkpoint" data-id="${escapeHtml(cp.id)}" data-name="${escapeHtml(cp.name)}" title="Delete model checkpoint to reclaim disk space">🗑️ Delete</button>
      </td>
    `;

    const delBtn = tr.querySelector(".btn-delete-checkpoint");
    if (delBtn) {
      delBtn.addEventListener("click", () => {
        deleteCheckpoint(cp.id, cp.name);
      });
    }

    tbody.appendChild(tr);
  });
}

async function saveActiveRoster() {
  const btn = document.getElementById("btnSaveRoster");
  const origText = btn ? btn.innerText : "";
  if (btn) {
    btn.innerText = "Saving...";
    btn.disabled = true;
  }

  const audexVal = document.getElementById("rosterSelectAudex")?.value;
  const payload = {
    model_name: document.getElementById("rosterSelectCanary")?.value,
    whisper_model: document.getElementById("rosterSelectWhisper")?.value,
    conformer_model: document.getElementById("rosterSelectConformer")?.value,
    parakeet_model: document.getElementById("rosterSelectParakeet")?.value,
    default_diarizer: document.getElementById("rosterSelectDiarizer")?.value,
    vocal_boost_level: document.getElementById("rosterSelectBoost")?.value,
    enable_audex_adjudicator: audexVal && audexVal !== "disabled",
    audex_model_id: audexVal && audexVal !== "disabled" ? audexVal : "nvidia/Nemotron-Labs-Audex-2B"
  };

  try {
    const res = await fetch("/api/models/roster", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error("Failed to save roster");
    if (btn) {
      btn.innerText = "✓ Roster Saved!";
      setTimeout(() => {
        btn.innerText = origText;
        btn.disabled = false;
      }, 1500);
    }
    await fetchModelData();
  } catch (err) {
    alert("Error saving roster: " + err.message);
    if (btn) {
      btn.innerText = origText;
      btn.disabled = false;
    }
  }
}

async function startModelInstall(modelId, framework, role) {
  try {
    const res = await fetch("/api/models/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId, framework: framework || "huggingface", role: role })
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || "Failed to start install.");
      return;
    }

    if (data.status === "queued") {
      addNotification("Download Queued", `${modelId} queued for sequential download (position #${data.position}).`, "info");
    } else {
      showDownloadProgress({
        is_downloading: true,
        model_id: modelId,
        progress: 0.0,
        message: `Contacting registry for ${modelId}...`
      });
    }

    startDownloadPolling();
    fetchModelData();
  } catch (err) {
    alert("Install error: " + err.message);
  }
}

async function cancelModelInstall(modelId) {
  try {
    const res = await fetch("/api/models/install/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId })
    });
    const data = await res.json();
    addNotification("Download Cancelled", data.message || `Cancelled download for ${modelId || "model"}.`, "info");
    const statusRes = await fetch("/api/models/install/status");
    if (statusRes.ok) {
      const st = await statusRes.json();
      showDownloadProgress(st);
      updatePresetButtonStates(st);
    }
    fetchModelData();
  } catch (err) {
    console.warn("Cancel download error:", err);
  }
}

function updatePresetButtonStates(status) {
  if (!status) return;
  const buttons = document.querySelectorAll(".btn-preset-install");
  buttons.forEach(btn => {
    const mid = btn.getAttribute("data-id");
    if (!mid) return;
    const cleanMid = mid.replace("nvidia/", "");
    const activeClean = status.model_id ? status.model_id.replace("nvidia/", "") : "";

    if (status.is_downloading && (mid === status.model_id || cleanMid === activeClean)) {
      btn.className = "btn-preset-install btn-preset-downloading";
      btn.innerText = `⏳ Downloading (${Math.round(status.progress || 0)}%)`;
      btn.disabled = true;
    } else {
      const queuedItem = (status.queue || []).find(q => q.model_id === mid || q.model_id === cleanMid || (q.model_id && cleanMid.includes(q.model_id)));
      if (queuedItem) {
        btn.className = "btn-preset-install btn-preset-queued";
        btn.innerText = `🕒 Queued (#${queuedItem.position})`;
        btn.disabled = false;
        btn.onclick = (e) => {
          e.stopPropagation();
          cancelModelInstall(mid);
        };
      } else {
        btn.className = "btn-preset-install";
        btn.innerText = "⬇️ Install";
        btn.disabled = false;
        btn.onclick = () => {
          startModelInstall(mid, btn.getAttribute("data-fw"), btn.getAttribute("data-role"));
        };
      }
    }
  });
}

function showDownloadProgress(status) {
  const box = document.getElementById("downloadProgressBox");
  const title = document.getElementById("downloadProgressTitle");
  const pct = document.getElementById("downloadProgressPct");
  const fill = document.getElementById("downloadProgressFill");
  const msg = document.getElementById("downloadProgressMsg");
  const speed = document.getElementById("downloadProgressSpeed");
  const eta = document.getElementById("downloadProgressEta");
  const bytes = document.getElementById("downloadProgressBytes");
  const indicator = document.getElementById("downloadStatusIndicator");
  const btnCancel = document.getElementById("btnCancelDownload");
  const queueContainer = document.getElementById("downloadQueueContainer");
  const queueChips = document.getElementById("downloadQueueChips");

  if (!box) return;

  const hasActive = Boolean(status.is_downloading);
  const hasQueue = Boolean(status.queue && status.queue.length > 0);

  if (!hasActive && !hasQueue && status.status === "idle") {
    box.style.display = "none";
    return;
  }

  box.style.display = "block";

  if (hasActive) {
    if (indicator) indicator.innerText = "⏳";
    if (title) title.innerText = `Downloading ${status.model_id || "Checkpoint"}...`;
    const numPct = typeof status.progress === "number" ? status.progress : 0;
    if (pct) pct.innerText = `${numPct.toFixed(1)}%`;
    if (fill) fill.style.width = `${Math.min(100, Math.max(0, numPct))}%`;
    if (msg) msg.innerText = status.message || "Downloading...";

    if (speed) {
      if (status.speed_str) {
        speed.innerText = `⚡ ${status.speed_str}`;
        speed.style.display = "inline-block";
      } else {
        speed.style.display = "none";
      }
    }

    if (eta) {
      if (status.eta_str) {
        eta.innerText = `⏳ ETA: ${status.eta_str}`;
        eta.style.display = "inline-block";
      } else {
        eta.style.display = "none";
      }
    }

    if (bytes) {
      bytes.innerText = status.downloaded_str || "";
    }

    if (btnCancel) {
      btnCancel.style.display = "inline-block";
      btnCancel.onclick = () => cancelModelInstall(status.model_id);
    }
  } else if (status.status === "completed") {
    if (indicator) indicator.innerText = "✅";
    if (title) title.innerText = `Completed: ${status.model_id || "Checkpoint"}`;
    if (pct) pct.innerText = "100%";
    if (fill) fill.style.width = "100%";
    if (msg) msg.innerText = status.message || "Download complete!";
    if (speed) speed.style.display = "none";
    if (eta) eta.style.display = "none";
    if (btnCancel) btnCancel.style.display = "none";
  } else if (status.status === "failed") {
    if (indicator) indicator.innerText = "❌";
    if (title) title.innerText = `Failed: ${status.model_id || "Checkpoint"}`;
    if (msg) msg.innerText = status.error || status.message || "Download failed.";
    if (speed) speed.style.display = "none";
    if (eta) eta.style.display = "none";
    if (btnCancel) btnCancel.style.display = "none";
  } else if (status.status === "cancelled") {
    if (indicator) indicator.innerText = "⏹️";
    if (title) title.innerText = `Cancelled: ${status.model_id || "Checkpoint"}`;
    if (msg) msg.innerText = status.message || "Download cancelled.";
    if (speed) speed.style.display = "none";
    if (eta) eta.style.display = "none";
    if (btnCancel) btnCancel.style.display = "none";
  }

  // Render Queue Chips
  if (queueContainer && queueChips) {
    if (hasQueue) {
      queueContainer.style.display = "block";
      queueChips.innerHTML = "";
      status.queue.forEach(item => {
        const chip = document.createElement("div");
        chip.className = "queue-chip";
        chip.innerHTML = `
          <span class="queue-chip-pos">#${item.position}</span>
          <span>${escapeHtml(item.name || item.model_id)}</span>
          <span style="color: var(--text-muted); font-size: 10px;">(~${item.size_gb || 0} GB)</span>
          <span class="queue-chip-btn-remove" title="Remove from queue">✕</span>
        `;
        chip.querySelector(".queue-chip-btn-remove").onclick = (e) => {
          e.stopPropagation();
          cancelModelInstall(item.model_id);
        };
        queueChips.appendChild(chip);
      });
    } else {
      queueContainer.style.display = "none";
      queueChips.innerHTML = "";
    }
  }
}

function startDownloadPolling() {
  if (downloadStatusTimer) clearInterval(downloadStatusTimer);
  downloadStatusTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/models/install/status");
      if (!res.ok) return;
      const status = await res.json();

      showDownloadProgress(status);
      updatePresetButtonStates(status);

      const hasActive = Boolean(status.is_downloading);
      const hasQueue = Boolean(status.queue && status.queue.length > 0);

      if (!hasActive && !hasQueue) {
        clearInterval(downloadStatusTimer);
        downloadStatusTimer = null;
        if (status.status === "completed") {
          setTimeout(() => {
            const box = document.getElementById("downloadProgressBox");
            if (box) box.style.display = "none";
          }, 3500);
          fetchModelData();
          fetchCacheBreakdown();
        } else if (status.status === "failed" || status.status === "cancelled") {
          setTimeout(() => {
            const box = document.getElementById("downloadProgressBox");
            if (box) box.style.display = "none";
          }, 4500);
          fetchModelData();
        }
      }
    } catch (err) {
      console.warn("Poll error:", err);
    }
  }, 1000);
}

async function deleteCheckpoint(checkpointId, displayName) {
  const ok = confirm(`Are you sure you want to delete "${displayName || checkpointId}" from disk cache?\nThis will permanently remove the checkpoint files to reclaim disk space.`);
  if (!ok) return;

  try {
    const res = await fetch("/api/models/checkpoints", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: checkpointId })
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Deletion failed: ${data.detail || "Error"}`);
      return;
    }
    await fetchModelData();
    await fetchCacheBreakdown();
  } catch (err) {
    alert("Deletion error: " + err.message);
  }
}

// --- 11. Upgraded Notification & Emergency HUD Manager ---
const btnToggleNotifDrawer = document.getElementById("btnToggleNotifDrawer");
const notifCountBadge = document.getElementById("notifCountBadge");
const notifDrawer = document.getElementById("notifDrawer");
const notifDrawerOverlay = document.getElementById("notifDrawerOverlay");
const notifDrawerList = document.getElementById("notifDrawerList");
const drawerNotifCount = document.getElementById("drawerNotifCount");
const btnClearNotifHistory = document.getElementById("btnClearNotifHistory");
const btnCloseNotifDrawer = document.getElementById("btnCloseNotifDrawer");

const emergencyHudBanner = document.getElementById("emergencyHudBanner");
const emergencyHudTitle = document.getElementById("emergencyHudTitle");
const emergencyHudMessage = document.getElementById("emergencyHudMessage");
const btnEmergencyAbortBanner = document.getElementById("btnEmergencyAbortBanner");
const btnDismissEmergencyHud = document.getElementById("btnDismissEmergencyHud");

const NOTIFICATION_HISTORY = [];
let unreadNotifCount = 0;

function addNotification(title, message, severity = "info", details = null) {
  const notif = {
    id: "notif_" + Date.now() + "_" + Math.random().toString(36).substr(2, 4),
    title,
    message,
    severity: severity.toLowerCase(),
    details,
    time: new Date().toLocaleTimeString()
  };
  NOTIFICATION_HISTORY.unshift(notif);
  unreadNotifCount++;
  updateNotifBadge();
  renderNotificationList();

  if (severity.toLowerCase() === "emergency") {
    showEmergencyHud(title, message);
  }
}

function updateNotifBadge() {
  if (!notifCountBadge) return;
  notifCountBadge.textContent = unreadNotifCount;
  notifCountBadge.style.display = unreadNotifCount > 0 ? "inline-block" : "none";
  if (drawerNotifCount) drawerNotifCount.textContent = `${NOTIFICATION_HISTORY.length} events`;
}

function showEmergencyHud(title, message) {
  if (!emergencyHudBanner) return;
  if (emergencyHudTitle) emergencyHudTitle.textContent = title || "Emergency Intervention Triggered";
  if (emergencyHudMessage) emergencyHudMessage.textContent = message || "VRAM velocity or memory watermark triggered protective governor.";
  emergencyHudBanner.style.display = "block";
}

function dismissEmergencyHud() {
  if (emergencyHudBanner) emergencyHudBanner.style.display = "none";
}

function renderNotificationList() {
  if (!notifDrawerList) return;
  if (NOTIFICATION_HISTORY.length === 0) {
    notifDrawerList.innerHTML = `<div class="notif-empty">No notifications yet.</div>`;
    return;
  }
  notifDrawerList.innerHTML = NOTIFICATION_HISTORY.map(n => `
    <div class="notif-item notif-item-${n.severity}">
      <div class="notif-item-header">
        <span class="notif-item-title">${escapeHtml(n.title)}</span>
        <span class="notif-item-time">${escapeHtml(n.time)}</span>
      </div>
      <div class="notif-item-msg">${escapeHtml(n.message)}</div>
    </div>
  `).join("");
}

function initNotificationSystem() {
  if (btnToggleNotifDrawer && notifDrawer && notifDrawerOverlay) {
    btnToggleNotifDrawer.addEventListener("click", () => {
      notifDrawer.classList.toggle("active");
      notifDrawerOverlay.classList.toggle("active");
      unreadNotifCount = 0;
      updateNotifBadge();
    });
  }
  if (btnCloseNotifDrawer && notifDrawer && notifDrawerOverlay) {
    btnCloseNotifDrawer.addEventListener("click", () => {
      notifDrawer.classList.remove("active");
      notifDrawerOverlay.classList.remove("active");
    });
  }
  if (notifDrawerOverlay && notifDrawer) {
    notifDrawerOverlay.addEventListener("click", () => {
      notifDrawer.classList.remove("active");
      notifDrawerOverlay.classList.remove("active");
    });
  }
  if (btnClearNotifHistory) {
    btnClearNotifHistory.addEventListener("click", () => {
      NOTIFICATION_HISTORY.length = 0;
      unreadNotifCount = 0;
      updateNotifBadge();
      renderNotificationList();
    });
  }
  if (btnDismissEmergencyHud) {
    btnDismissEmergencyHud.addEventListener("click", dismissEmergencyHud);
  }
  if (btnEmergencyAbortBanner) {
    btnEmergencyAbortBanner.addEventListener("click", emergencyAbortTask);
  }
}

// --- 12. PyAnnote Audio 3.1 Setup & Verification ---
const pyannoteStatusPill = document.getElementById("pyannoteStatusPill");
const hfTokenInput = document.getElementById("hfTokenInput");
const btnToggleTokenVisibility = document.getElementById("btnToggleTokenVisibility");
const btnVerifyHfToken = document.getElementById("btnVerifyHfToken");
const btnClearHfToken = document.getElementById("btnClearHfToken");
const hfTokenPersistBadge = document.getElementById("hfTokenPersistBadge");
const pyannoteFeedbackText = document.getElementById("pyannoteFeedbackText");

async function fetchPyAnnoteStatus() {
  if (!pyannoteStatusPill) return;
  try {
    const res = await fetch("/api/pyannote/status");
    if (!res.ok) return;
    const data = await res.json();
    updatePyAnnoteUI(data);

    // Auto-populate token from backend settings or client storage
    const savedToken = data.token || localStorage.getItem("ts_hf_token") || "";
    if (hfTokenInput && savedToken && !hfTokenInput.value) {
      hfTokenInput.value = savedToken;
    }
    if (savedToken) {
      localStorage.setItem("ts_hf_token", savedToken);
    }
  } catch (err) {
    console.warn("PyAnnote status check error:", err);
  }
}

function updatePyAnnoteUI(data) {
  if (!pyannoteStatusPill) return;
  pyannoteStatusPill.className = "pyannote-status-pill";
  const hasToken = Boolean(data.token_provided || (data.token && data.token.length > 0));

  if (hfTokenPersistBadge) {
    hfTokenPersistBadge.style.display = hasToken ? "inline" : "none";
  }
  if (btnClearHfToken) {
    btnClearHfToken.style.display = hasToken ? "inline-flex" : "none";
  }

  if (data.ready) {
    pyannoteStatusPill.classList.add("ready");
    pyannoteStatusPill.textContent = `✅ Ready (@${data.username || "User"})`;
    if (pyannoteFeedbackText) {
      pyannoteFeedbackText.style.color = "#10b981";
      pyannoteFeedbackText.textContent = `Verified! PyAnnote Audio 3.1 is authenticated and ready for speaker diarization. Token is persisted in settings.json.`;
    }
  } else if (data.token_valid && (!data.diarization_access || !data.segmentation_access)) {
    pyannoteStatusPill.classList.add("warning");
    pyannoteStatusPill.textContent = "⚠️ Gated Agreement Required";
    if (pyannoteFeedbackText) {
      pyannoteFeedbackText.style.color = "#f59e0b";
      pyannoteFeedbackText.textContent = data.message || "Please accept user agreements on Hugging Face to unlock model weights.";
    }
  } else if (data.token_provided) {
    pyannoteStatusPill.classList.add("error");
    pyannoteStatusPill.textContent = "❌ Invalid Token";
    if (pyannoteFeedbackText) {
      pyannoteFeedbackText.style.color = "#ef4444";
      pyannoteFeedbackText.textContent = data.message || "Token verification failed. Check permissions.";
    }
  } else {
    pyannoteStatusPill.textContent = "⚠️ Token Not Set";
    if (pyannoteFeedbackText) {
      pyannoteFeedbackText.style.color = "var(--text-muted)";
      pyannoteFeedbackText.textContent = "Defaulting to NeMo TitaNet. Provide HF Token to enable PyAnnote.";
    }
  }
}

function initPyAnnoteVerifier() {
  // Pre-load from localStorage if available
  const localTok = localStorage.getItem("ts_hf_token");
  if (localTok && hfTokenInput && !hfTokenInput.value) {
    hfTokenInput.value = localTok;
  }

  if (btnToggleTokenVisibility && hfTokenInput) {
    btnToggleTokenVisibility.addEventListener("click", () => {
      hfTokenInput.type = hfTokenInput.type === "password" ? "text" : "password";
      btnToggleTokenVisibility.textContent = hfTokenInput.type === "password" ? "👁️" : "🙈";
    });
  }

  if (btnClearHfToken && hfTokenInput) {
    btnClearHfToken.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to remove the saved Hugging Face token?")) return;
      hfTokenInput.value = "";
      localStorage.removeItem("ts_hf_token");
      try {
        const res = await fetch("/api/pyannote/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: "" })
        });
        const data = await res.json();
        updatePyAnnoteUI(data);
        addNotification("Token Removed", "Hugging Face token cleared from settings.", "info");
      } catch (err) {
        console.warn("Failed to clear token:", err);
      }
    });
  }

  if (btnVerifyHfToken && hfTokenInput) {
    btnVerifyHfToken.addEventListener("click", async () => {
      const token = hfTokenInput.value.trim();
      btnVerifyHfToken.disabled = true;
      btnVerifyHfToken.textContent = "Verifying...";
      try {
        const res = await fetch("/api/pyannote/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token })
        });
        const data = await res.json();
        updatePyAnnoteUI(data);
        if (token && data.token_valid) {
          localStorage.setItem("ts_hf_token", token);
        } else if (!token) {
          localStorage.removeItem("ts_hf_token");
        }
        if (data.ready) {
          addNotification("PyAnnote Verified", `PyAnnote Audio 3.1 verified for @${data.username} and saved to settings.json`, "success");
        } else {
          addNotification("PyAnnote Notice", data.message, data.token_valid ? "warning" : "error");
        }
      } catch (err) {
        alert("Token verification failed: " + err.message);
      } finally {
        btnVerifyHfToken.disabled = false;
        btnVerifyHfToken.textContent = "🔍 Test & Save Token";
      }
    });
  }
}

// --- 13. Subsystem Supervisor & Predictive Emergency Governor ---
const govStatusPill = document.getElementById("govStatusPill");
const govCeilingVal = document.getElementById("govCeilingVal");
const govCeilingSlider = document.getElementById("govCeilingSlider");
const govVelocityVal = document.getElementById("govVelocityVal");
const govProjectedVal = document.getElementById("govProjectedVal");
const btnSupervisorEmergencyAbort = document.getElementById("btnSupervisorEmergencyAbort");
const btnSupervisorEjectAll = document.getElementById("btnSupervisorEjectAll");
const subsystemsGrid = document.getElementById("subsystemsGrid");

async function fetchSupervisorData() {
  try {
    const res = await fetch("/api/supervisor/subsystems");
    if (!res.ok) return;
    const data = await res.json();
    renderSupervisorUI(data);
  } catch (err) {
    console.warn("Supervisor fetch error:", err);
  }
}

function renderSupervisorUI(data) {
  if (!data) return;

  // 1. Governor Metrics
  const gov = data.governor || {};
  if (govStatusPill) {
    govStatusPill.className = "gov-status-pill";
    const st = (gov.status || "NORMAL").toUpperCase();
    govStatusPill.textContent = st;
    if (st === "EMERGENCY") govStatusPill.classList.add("emergency");
    else if (st === "WARNING") govStatusPill.classList.add("warning");
  }

  if (govCeilingVal) govCeilingVal.textContent = `${gov.ceiling_gb || 5.5} GB`;
  if (govCeilingSlider && !govCeilingSlider.matches(":focus")) {
    govCeilingSlider.value = gov.ceiling_gb || 5.5;
  }

  if (govVelocityVal) {
    const vel = gov.velocity_mb_s || 0;
    govVelocityVal.textContent = `${vel >= 0 ? "+" : ""}${vel} MB/s`;
    govVelocityVal.style.color = vel > 100 ? "#ef4444" : (vel > 30 ? "#f59e0b" : "var(--text-primary)");
  }

  if (govProjectedVal) {
    const projGb = (gov.projected_5s_mb || 0) / 1024;
    govProjectedVal.textContent = `${projGb.toFixed(2)} GB`;
    govProjectedVal.style.color = projGb > (gov.ceiling_gb || 5.5) ? "#ef4444" : "var(--text-primary)";
  }

  // 2. Alert Handling
  if (data.active_alert) {
    showEmergencyHud(data.active_alert.title, data.active_alert.message);
  }

  // 3. Subsystem Cards Grid
  if (subsystemsGrid && data.subsystems) {
    const existingCards = subsystemsGrid.querySelectorAll(".subsystem-card[data-subsystem-id]");
    if (existingCards.length === data.subsystems.length) {
      // In-place update to prevent DOM flicker and preserving click handlers
      data.subsystems.forEach(sub => {
        const card = subsystemsGrid.querySelector(`.subsystem-card[data-subsystem-id="${sub.id}"]`);
        if (!card) return;
        const state = (sub.state || "idle").toLowerCase();
        card.className = `subsystem-card ${state}`;
        const modelEl = card.querySelector(".subsystem-model");
        if (modelEl) modelEl.textContent = sub.active_model || "--";
        const badgeEl = card.querySelector(".subsystem-state-badge");
        if (badgeEl) {
          badgeEl.className = `subsystem-state-badge ${state}`;
          badgeEl.innerHTML = state === "running" ? '<span class="pulse-dot"></span> RUNNING' : state.toUpperCase();
        }
        const valAlloc = card.querySelector(".val-vram-alloc");
        if (valAlloc) valAlloc.textContent = `${(sub.vram_allocated_mb || 0).toFixed(1)} MB`;
        const valPeak = card.querySelector(".val-vram-peak");
        if (valPeak) valPeak.textContent = `${(sub.vram_peak_mb || 0).toFixed(1)} MB`;
        const valRam = card.querySelector(".val-proc-ram");
        if (valRam) valRam.textContent = `${(sub.ram_rss_mb || 0).toFixed(1)} MB`;
        const valRt = card.querySelector(".val-runtime-rtfx");
        if (valRt) {
          const rtfx = sub.rtfx ? `${sub.rtfx}x` : "--";
          const dur = sub.last_runtime_sec ? `${sub.last_runtime_sec}s` : "--";
          valRt.textContent = state === "running" ? `${dur} (live)` : `${dur} / ${rtfx}`;
        }
      });
    } else {
      subsystemsGrid.innerHTML = data.subsystems.map(sub => {
        const state = (sub.state || "idle").toLowerCase();
        const vramMb = sub.vram_allocated_mb || 0;
        const peakMb = sub.vram_peak_mb || 0;
        const ramMb = sub.ram_rss_mb || 0;
        const rtfx = sub.rtfx ? `${sub.rtfx}x` : "--";
        const dur = sub.last_runtime_sec ? `${sub.last_runtime_sec}s` : "--";
        const badgeHtml = state === "running" ? '<span class="pulse-dot"></span> RUNNING' : state.toUpperCase();
        const timeDisplay = state === "running" ? `${dur} (live)` : `${dur} / ${rtfx}`;

        return `
          <div class="subsystem-card ${state}" data-subsystem-id="${sub.id}">
            <div class="subsystem-header">
              <div class="subsystem-name-group">
                <span class="subsystem-name">${escapeHtml(sub.name)}</span>
                <span class="subsystem-model">${escapeHtml(sub.active_model || "--")}</span>
              </div>
              <span class="subsystem-state-badge ${state}">${badgeHtml}</span>
            </div>
            <div class="subsystem-metrics">
              <div class="subsystem-metric-row">
                <span>Active VRAM:</span>
                <span class="subsystem-metric-val val-vram-alloc" style="color: #a855f7;">${vramMb.toFixed(1)} MB</span>
              </div>
              <div class="subsystem-metric-row">
                <span>Peak VRAM:</span>
                <span class="subsystem-metric-val val-vram-peak">${peakMb.toFixed(1)} MB</span>
              </div>
              <div class="subsystem-metric-row">
                <span>Process RAM:</span>
                <span class="subsystem-metric-val val-proc-ram" style="color: #10b981;">${ramMb.toFixed(1)} MB</span>
              </div>
              <div class="subsystem-metric-row">
                <span>Last Runtime / RTFx:</span>
                <span class="subsystem-metric-val val-runtime-rtfx">${timeDisplay}</span>
              </div>
            </div>
            ${sub.can_eject ? `
              <button class="btn-subsystem-eject" onclick="forceEjectSubsystem('${sub.id}', '${escapeHtml(sub.name)}')">
                ⚡ Force Eject
              </button>
            ` : ''}
          </div>
        `;
      }).join("");
    }
  }
}

async function forceEjectSubsystem(stageId, name) {
  try {
    const res = await fetch("/api/supervisor/unload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: stageId })
    });
    const data = await res.json();
    addNotification("Model Ejected", `Force ejected ${name || stageId}. Free VRAM: ${data.free_vram_gb} GB`, "info");
    await fetchSupervisorData();
    await fetchTelemetryData();
  } catch (err) {
    alert("Eject failed: " + err.message);
  }
}

async function emergencyAbortTask() {
  const ok = confirm("🚨 Are you sure you want to EMERGENCY ABORT the running pipeline?\nThis will stop all processing immediately and free GPU memory.");
  if (!ok) return;

  try {
    const res = await fetch("/api/supervisor/abort", { method: "POST" });
    const data = await res.json();
    dismissEmergencyHud();
    addNotification("Emergency Abort", "Active transcription task was forcefully aborted.", "emergency");
    await fetchSupervisorData();
    await fetchTelemetryData();
    await fetchJournalData();
  } catch (err) {
    alert("Emergency abort error: " + err.message);
  }
}

function initSupervisorControls() {
  if (govCeilingSlider) {
    govCeilingSlider.addEventListener("change", async (e) => {
      const ceiling = parseFloat(e.target.value);
      try {
        await fetch("/api/supervisor/governor", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ceiling_gb: ceiling })
        });
        if (govCeilingVal) govCeilingVal.textContent = `${ceiling} GB`;
        addNotification("Governor Updated", `Safety ceiling set to ${ceiling} GB`, "info");
      } catch (err) {
        console.warn("Governor update failed:", err);
      }
    });
    govCeilingSlider.addEventListener("input", (e) => {
      if (govCeilingVal) govCeilingVal.textContent = `${parseFloat(e.target.value)} GB`;
    });
  }

  if (btnSupervisorEmergencyAbort) {
    btnSupervisorEmergencyAbort.addEventListener("click", emergencyAbortTask);
  }

  if (btnSupervisorEjectAll) {
    btnSupervisorEjectAll.addEventListener("click", () => forceEjectSubsystem("all", "All Pipeline Models"));
  }
}

// --- 14. Advanced Storage Breakdown & Granular Purge ---
const storageTotalManagedVal = document.getElementById("storageTotalManagedVal");
const storageTargetsGrid = document.getElementById("storageTargetsGrid");

async function fetchStorageBreakdown() {
  if (!storageTargetsGrid) return;
  try {
    const res = await fetch("/api/storage/detailed");
    if (!res.ok) return;
    const data = await res.json();
    if (storageTotalManagedVal) storageTotalManagedVal.textContent = `${data.total_gb || 0} GB`;
    renderStorageTargets(data.targets || []);
  } catch (err) {
    console.warn("Storage breakdown fetch error:", err);
  }
}

function renderStorageTargets(targets) {
  if (!storageTargetsGrid) return;
  storageTargetsGrid.innerHTML = targets.map(t => `
    <div class="storage-target-card">
      <div class="storage-card-header">
        <span class="storage-card-title">${escapeHtml(t.name)}</span>
        <span class="storage-badge-cat">${escapeHtml(t.category)}</span>
      </div>
      <div class="storage-card-hint">${escapeHtml(t.hint || t.path)}</div>
      <div class="storage-card-footer">
        <span class="storage-size-val">${t.size_mb >= 1024 ? t.size_gb + " GB" : t.size_mb + " MB"} (${t.file_count} files)</span>
        ${t.can_purge ? `
          <button class="btn-storage-purge" onclick="purgeStorageTarget('${t.id}', '${escapeHtml(t.name)}')">
            🗑️ Purge
          </button>
        ` : '<span style="font-size: 10px; color: var(--text-muted);">Protected</span>'}
      </div>
    </div>
  `).join("");
}

async function purgeStorageTarget(targetId, name) {
  const ok = confirm(`Purge "${name || targetId}" to free disk space?`);
  if (!ok) return;

  try {
    const res = await fetch("/api/storage/purge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: targetId })
    });
    const data = await res.json();
    addNotification("Storage Purged", data.message || `Cleaned ${targetId}`, "info");
    await fetchStorageBreakdown();
    await fetchCacheBreakdown();
  } catch (err) {
    alert("Purge error: " + err.message);
  }
}

// --- 15. Supervisor Audit Event Journal ---
let activeJournalSeverity = "ALL";
let journalSearchQuery = "";
const journalFilterGroup = document.getElementById("journalFilterGroup");
const journalSearchInput = document.getElementById("journalSearchInput");
const journalTableBody = document.getElementById("journalTableBody");
const btnExportJournalCsv = document.getElementById("btnExportJournalCsv");
const btnExportJournalJson = document.getElementById("btnExportJournalJson");
const btnClearJournal = document.getElementById("btnClearJournal");

async function fetchJournalData() {
  if (!journalTableBody) return;
  try {
    const params = new URLSearchParams();
    params.append("limit", "100");
    if (activeJournalSeverity && activeJournalSeverity !== "ALL") {
      params.append("severity", activeJournalSeverity);
    }
    if (journalSearchQuery) {
      params.append("search", journalSearchQuery);
    }

    const res = await fetch(`/api/supervisor/journal?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    renderJournalTable(data.events || []);
  } catch (err) {
    console.warn("Journal fetch error:", err);
  }
}

function renderJournalTable(events) {
  if (!journalTableBody) return;
  if (events.length === 0) {
    journalTableBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 20px;">No audit journal records found.</td></tr>`;
    return;
  }

  journalTableBody.innerHTML = events.map(e => {
    const sev = (e.severity || "INFO").toUpperCase();
    const sevClass = sev.toLowerCase();
    const timeStr = e.timestamp ? e.timestamp.replace("T", " ").substring(0, 19) : "--";

    return `
      <tr>
        <td style="font-family: var(--font-mono, monospace); font-size: 11px;">${escapeHtml(timeStr)}</td>
        <td><span class="journal-severity-badge ${sevClass}">${escapeHtml(sev)}</span></td>
        <td style="font-weight: 600;">${escapeHtml(e.subsystem || "--")}</td>
        <td style="font-family: var(--font-mono, monospace); font-size: 11px; color: var(--accent-light);">${escapeHtml(e.event_type || "--")}</td>
        <td>${escapeHtml(e.message || "")}</td>
      </tr>
    `;
  }).join("");
}

function initJournalControls() {
  if (journalFilterGroup) {
    const btns = journalFilterGroup.querySelectorAll(".journal-filter-btn");
    btns.forEach(btn => {
      btn.addEventListener("click", () => {
        btns.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        activeJournalSeverity = btn.dataset.severity || "ALL";
        fetchJournalData();
      });
    });
  }

  if (journalSearchInput) {
    let debounceTimer = null;
    journalSearchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        journalSearchQuery = e.target.value.trim();
        fetchJournalData();
      }, 250);
    });
  }

  if (btnExportJournalCsv) {
    btnExportJournalCsv.addEventListener("click", () => {
      window.open("/api/supervisor/journal/export?format=csv", "_blank");
    });
  }

  if (btnExportJournalJson) {
    btnExportJournalJson.addEventListener("click", () => {
      window.open("/api/supervisor/journal/export?format=json", "_blank");
    });
  }

  if (btnClearJournal) {
    btnClearJournal.addEventListener("click", async () => {
      const ok = confirm("Clear all supervisor audit journal entries?");
      if (!ok) return;
      try {
        await fetch("/api/supervisor/journal/clear", { method: "POST" });
        await fetchJournalData();
      } catch (err) {
        alert("Clear journal failed: " + err.message);
      }
    });
  }
}

// --- 11. Settings Tab Management ---
const settingBaseDirInput = document.getElementById("settingBaseDirInput");
const btnSaveStoragePath = document.getElementById("btnSaveStoragePath");
const settingsDiskStatusBadge = document.getElementById("settingsDiskStatusBadge");
const settingDiskFreeText = document.getElementById("settingDiskFreeText");
const settingDiskTotalText = document.getElementById("settingDiskTotalText");
const settingDiskBar = document.getElementById("settingDiskBar");
const settingSubdirModels = document.getElementById("settingSubdirModels");
const settingSubdirTmp = document.getElementById("settingSubdirTmp");
const settingSubdirUploads = document.getElementById("settingSubdirUploads");
const settingSubdirOutputs = document.getElementById("settingSubdirOutputs");

const btnPurgeAllModelsSettings = document.getElementById("btnPurgeAllModelsSettings");
const btnPurgeScratchSettings = document.getElementById("btnPurgeScratchSettings");
const btnDropPageCacheSettingsTab = document.getElementById("btnDropPageCacheSettingsTab");

const settingHfBadge = document.getElementById("settingHfBadge");
const settingHfTokenInput = document.getElementById("settingHfTokenInput");
const btnToggleSettingHfToken = document.getElementById("btnToggleSettingHfToken");
const btnSaveSettingHfToken = document.getElementById("btnSaveSettingHfToken");
const btnClearSettingHfToken = document.getElementById("btnClearSettingHfToken");
const settingHfFeedback = document.getElementById("settingHfFeedback");

const settingDefaultDiarizer = document.getElementById("settingDefaultDiarizer");
const settingVocalBoost = document.getElementById("settingVocalBoost");
const settingGovCeilingSlider = document.getElementById("settingGovCeilingSlider");
const settingGovCeilingVal = document.getElementById("settingGovCeilingVal");
const settingEnableAudex = document.getElementById("settingEnableAudex");
const settingAttentionBackend = document.getElementById("settingAttentionBackend");
const btnSavePipelinePrefs = document.getElementById("btnSavePipelinePrefs");

const glossaryCountBadge = document.getElementById("glossaryCountBadge");
const glossaryTermInput = document.getElementById("glossaryTermInput");
const btnAddGlossaryTerm = document.getElementById("btnAddGlossaryTerm");
const btnClearGlossary = document.getElementById("btnClearGlossary");
const glossaryChipsContainer = document.getElementById("glossaryChipsContainer");
const glossaryEmptyMsg = document.getElementById("glossaryEmptyMsg");
const btnStudioGlossary = document.getElementById("btnStudioGlossary");
const studioGlossaryCount = document.getElementById("studioGlossaryCount");
const pillAttention = document.getElementById("pillAttention");

let currentActiveGlossary = [];
let currentAttentionBackend = "sdpa";

function updateAttentionPill(backend) {
  if (!pillAttention) return;
  if (backend === "sdpa") {
    pillAttention.style.borderColor = "rgba(99, 102, 241, 0.4)";
    pillAttention.style.color = "#818cf8";
    pillAttention.textContent = "⚡ SDPA Active (~35% VRAM saved)";
  } else if (backend === "flash_attention_2") {
    pillAttention.style.borderColor = "rgba(52, 211, 153, 0.4)";
    pillAttention.style.color = "#34d399";
    pillAttention.textContent = "⚡ FA-2 Active";
  } else {
    pillAttention.style.borderColor = "rgba(156, 163, 175, 0.4)";
    pillAttention.style.color = "#9ca3af";
    pillAttention.textContent = "🐢 Eager Mode";
  }
}

async function loadGlossary() {
  try {
    const res = await fetch("/api/glossary");
    if (!res.ok) return;
    const data = await res.json();
    currentActiveGlossary = data.glossary || [];
    renderGlossaryUI();
  } catch (err) {
    console.warn("loadGlossary error:", err);
  }
}

function renderGlossaryUI() {
  if (glossaryCountBadge) {
    glossaryCountBadge.textContent = `${currentActiveGlossary.length} term${currentActiveGlossary.length === 1 ? '' : 's'} active`;
  }
  if (studioGlossaryCount) {
    studioGlossaryCount.textContent = currentActiveGlossary.length;
  }
  if (!glossaryChipsContainer) return;

  glossaryChipsContainer.innerHTML = "";
  if (currentActiveGlossary.length === 0) {
    const span = document.createElement("span");
    span.id = "glossaryEmptyMsg";
    span.style.color = "var(--text-muted)";
    span.style.fontSize = "12px";
    span.style.fontStyle = "italic";
    span.textContent = "No custom terms added yet. Add terms above to prime the ASR council.";
    glossaryChipsContainer.appendChild(span);
    return;
  }

  currentActiveGlossary.forEach((term) => {
    const chip = document.createElement("div");
    chip.style.display = "inline-flex";
    chip.style.alignItems = "center";
    chip.style.gap = "6px";
    chip.style.padding = "4px 10px";
    chip.style.borderRadius = "16px";
    chip.style.background = "rgba(99, 102, 241, 0.15)";
    chip.style.border = "1px solid rgba(99, 102, 241, 0.4)";
    chip.style.color = "#c7d2fe";
    chip.style.fontSize = "12px";
    chip.style.fontWeight = "500";

    const textSpan = document.createElement("span");
    textSpan.textContent = term;
    chip.appendChild(textSpan);

    const delBtn = document.createElement("button");
    delBtn.innerHTML = "&times;";
    delBtn.style.background = "none";
    delBtn.style.border = "none";
    delBtn.style.color = "#ef4444";
    delBtn.style.cursor = "pointer";
    delBtn.style.fontSize = "14px";
    delBtn.style.lineHeight = "1";
    delBtn.style.padding = "0 2px";
    delBtn.title = `Remove "${term}"`;
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await removeGlossaryTerm(term);
    });
    chip.appendChild(delBtn);

    glossaryChipsContainer.appendChild(chip);
  });
}

async function addGlossaryTerms(inputVal) {
  if (!inputVal || !inputVal.trim()) return;
  const rawTerms = inputVal.split(",").map(t => t.trim()).filter(Boolean);
  if (rawTerms.length === 0) return;

  try {
    const res = await fetch("/api/glossary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ terms: rawTerms })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to add term");
    currentActiveGlossary = data.glossary || [];
    renderGlossaryUI();
    if (glossaryTermInput) glossaryTermInput.value = "";
    addNotification("Glossary Updated", `Added ${rawTerms.length} term(s) to phonetic glossary.`, "success");
  } catch (err) {
    alert("Error adding glossary terms: " + err.message);
  }
}

async function removeGlossaryTerm(term) {
  try {
    const res = await fetch("/api/glossary", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term: term })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to remove term");
    currentActiveGlossary = data.glossary || [];
    renderGlossaryUI();
    addNotification("Term Removed", `Removed "${term}" from glossary.`, "info");
  } catch (err) {
    alert("Error removing term: " + err.message);
  }
}

async function clearGlossaryAll() {
  if (!confirm("Are you sure you want to clear all terms from the custom glossary?")) return;
  try {
    const res = await fetch("/api/glossary?all=true", { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to clear glossary");
    currentActiveGlossary = [];
    renderGlossaryUI();
    addNotification("Glossary Cleared", "Custom phonetic glossary cleared.", "info");
  } catch (err) {
    alert("Error clearing glossary: " + err.message);
  }
}

async function fetchSettingsData() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const data = await res.json();
    const s = data.settings || {};
    const st = data.storage || {};

    if (settingBaseDirInput && s.base_dir) {
      settingBaseDirInput.value = s.base_dir;
    }

    if (settingDiskFreeText && st.free_gb !== undefined) {
      settingDiskFreeText.textContent = `${st.free_gb} GB Available`;
    }
    if (settingDiskTotalText && st.total_gb !== undefined) {
      settingDiskTotalText.textContent = `Total: ${st.total_gb} GB (${st.percent || 0}% used)`;
    }
    if (settingDiskBar && st.percent !== undefined) {
      settingDiskBar.style.width = `${st.percent}%`;
    }
    if (settingsDiskStatusBadge) {
      settingsDiskStatusBadge.textContent = `Partition Active (${st.free_gb || 0} GB free)`;
    }

    if (settingSubdirModels && st.models_dir) settingSubdirModels.textContent = st.models_dir;
    if (settingSubdirTmp && st.tmp_dir) settingSubdirTmp.textContent = st.tmp_dir;
    if (settingSubdirUploads && st.upload_dir) settingSubdirUploads.textContent = st.upload_dir;
    if (settingSubdirOutputs && st.output_dir) settingSubdirOutputs.textContent = st.output_dir;

    if (settingDefaultDiarizer && s.default_diarizer) {
      settingDefaultDiarizer.value = s.default_diarizer;
    }
    if (settingVocalBoost && s.vocal_boost_level) {
      settingVocalBoost.value = s.vocal_boost_level;
    }
    if (settingGovCeilingSlider && s.vram_governor_threshold_gb) {
      settingGovCeilingSlider.value = s.vram_governor_threshold_gb;
      if (settingGovCeilingVal) settingGovCeilingVal.textContent = `${s.vram_governor_threshold_gb} GB`;
    }
    if (settingEnableAudex && s.enable_audex_adjudicator !== undefined) {
      settingEnableAudex.value = String(s.enable_audex_adjudicator);
    }
    if (settingAttentionBackend && s.attention_backend) {
      settingAttentionBackend.value = s.attention_backend;
      currentAttentionBackend = s.attention_backend;
      updateAttentionPill(currentAttentionBackend);
    }
    if (s.custom_glossary && Array.isArray(s.custom_glossary)) {
      currentActiveGlossary = s.custom_glossary;
      renderGlossaryUI();
    }

    // Update HF token UI in settings
    const savedToken = localStorage.getItem("ts_hf_token") || "";
    if (settingHfTokenInput && savedToken && !settingHfTokenInput.value) {
      settingHfTokenInput.value = savedToken;
    }
    if (settingHfBadge) {
      if (s.token_configured) {
        settingHfBadge.className = "status-badge-active";
        settingHfBadge.textContent = "Token Configured";
      } else {
        settingHfBadge.className = "status-badge";
        settingHfBadge.textContent = "Token Not Set";
      }
    }
  } catch (err) {
    console.warn("fetchSettingsData failed:", err);
  }
}

function initSettingsTab() {
  if (settingGovCeilingSlider && settingGovCeilingVal) {
    settingGovCeilingSlider.addEventListener("input", (e) => {
      settingGovCeilingVal.textContent = `${e.target.value} GB`;
    });
  }

  if (btnSaveStoragePath && settingBaseDirInput) {
    btnSaveStoragePath.addEventListener("click", async () => {
      const newPath = settingBaseDirInput.value.trim();
      if (!newPath) {
        alert("Please specify a valid absolute directory path.");
        return;
      }
      btnSaveStoragePath.disabled = true;
      btnSaveStoragePath.textContent = "Applying...";
      try {
        const res = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ base_dir: newPath })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update storage root");
        addNotification("Storage Root Updated", `Persistent storage relocated to ${newPath}`, "success");
        await fetchSettingsData();
        await fetchModelData();
        await fetchCacheBreakdown();
      } catch (err) {
        alert("Failed to update storage path: " + err.message);
      } finally {
        btnSaveStoragePath.disabled = false;
        btnSaveStoragePath.textContent = "💾 Apply Storage Root";
      }
    });
  }

  if (btnPurgeAllModelsSettings) {
    btnPurgeAllModelsSettings.addEventListener("click", async () => {
      const ok = confirm("Purge all cached models and scratch files across both persistent storage and legacy caches?\n\nClean fresh copies will be downloaded directly to /mnt/d/transcript_suite_data when next required.");
      if (!ok) return;
      btnPurgeAllModelsSettings.disabled = true;
      btnPurgeAllModelsSettings.textContent = "Purging Checkpoints...";
      try {
        const res = await fetch("/api/storage/purge-all", { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Purge failed");
        addNotification("Model Hub Purged", "All model checkpoints and scratch directories cleared.", "warning");
        await fetchSettingsData();
        await fetchModelData();
        await fetchCacheBreakdown();
      } catch (err) {
        alert("Purge failed: " + err.message);
      } finally {
        btnPurgeAllModelsSettings.disabled = false;
        btnPurgeAllModelsSettings.textContent = "🗑️ Purge All Existing Models (Clean Slate)";
      }
    });
  }

  if (btnPurgeScratchSettings) {
    btnPurgeScratchSettings.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/storage/purge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: "temp_audio" })
        });
        const data = await res.json();
        addNotification("Scratch Purged", `Freed ${data.reclaimed_mb || 0} MB of temporary files.`, "info");
        await fetchSettingsData();
        await fetchCacheBreakdown();
      } catch (err) {
        alert("Purge scratch failed: " + err.message);
      }
    });
  }

  if (btnDropPageCacheSettingsTab) {
    btnDropPageCacheSettingsTab.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/memory/drop-cache", { method: "POST" });
        const data = await res.json();
        addNotification("Page Cache Purged", `Freed ${data.freed_cached_mb || 0} MB cached pages (${data.files_purged || 0} files).`, "info");
        await fetchTelemetryData();
      } catch (err) {
        alert("Drop page cache failed: " + err.message);
      }
    });
  }

  // HF Token in Settings Tab
  if (btnToggleSettingHfToken && settingHfTokenInput) {
    btnToggleSettingHfToken.addEventListener("click", () => {
      settingHfTokenInput.type = settingHfTokenInput.type === "password" ? "text" : "password";
      btnToggleSettingHfToken.textContent = settingHfTokenInput.type === "password" ? "👁️" : "🙈";
    });
  }

  if (btnSaveSettingHfToken && settingHfTokenInput) {
    btnSaveSettingHfToken.addEventListener("click", async () => {
      const tok = settingHfTokenInput.value.trim();
      btnSaveSettingHfToken.disabled = true;
      btnSaveSettingHfToken.textContent = "Verifying...";
      try {
        const res = await fetch("/api/pyannote/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: tok })
        });
        const data = await res.json();
        if (tok && data.token_valid) {
          localStorage.setItem("ts_hf_token", tok);
          if (hfTokenInput) hfTokenInput.value = tok;
        } else if (!tok) {
          localStorage.removeItem("ts_hf_token");
          if (hfTokenInput) hfTokenInput.value = "";
        }
        updatePyAnnoteUI(data);
        if (settingHfBadge) {
          settingHfBadge.className = data.ready ? "status-badge-active" : "status-badge";
          settingHfBadge.textContent = data.ready ? `Ready (@${data.username || "User"})` : "Verification Issue";
        }
        if (settingHfFeedback) {
          settingHfFeedback.style.color = data.ready ? "#10b981" : (data.token_valid ? "#f59e0b" : "#ef4444");
          settingHfFeedback.textContent = data.message || "Token status updated.";
        }
        addNotification("HF Token Saved", data.ready ? `Verified for @${data.username}` : data.message, data.ready ? "success" : "warning");
      } catch (err) {
        alert("Token verification error: " + err.message);
      } finally {
        btnSaveSettingHfToken.disabled = false;
        btnSaveSettingHfToken.textContent = "Verify & Save";
      }
    });
  }

  if (btnClearSettingHfToken && settingHfTokenInput) {
    btnClearSettingHfToken.addEventListener("click", async () => {
      if (!confirm("Remove saved Hugging Face token?")) return;
      settingHfTokenInput.value = "";
      localStorage.removeItem("ts_hf_token");
      if (hfTokenInput) hfTokenInput.value = "";
      try {
        const res = await fetch("/api/pyannote/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: "" })
        });
        const data = await res.json();
        updatePyAnnoteUI(data);
        if (settingHfBadge) {
          settingHfBadge.className = "status-badge";
          settingHfBadge.textContent = "Token Not Set";
        }
        if (settingHfFeedback) {
          settingHfFeedback.style.color = "var(--text-muted)";
          settingHfFeedback.textContent = "Token cleared.";
        }
        addNotification("Token Removed", "Hugging Face token cleared from settings.", "info");
      } catch (err) {
        console.warn("Failed to clear token:", err);
      }
    });
  }

  // Attention Backend Change
  if (settingAttentionBackend) {
    settingAttentionBackend.addEventListener("change", () => {
      currentAttentionBackend = settingAttentionBackend.value;
      updateAttentionPill(currentAttentionBackend);
    });
  }

  // Glossary Term Adding
  if (btnAddGlossaryTerm && glossaryTermInput) {
    btnAddGlossaryTerm.addEventListener("click", () => {
      addGlossaryTerms(glossaryTermInput.value);
    });
    glossaryTermInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addGlossaryTerms(glossaryTermInput.value);
      }
    });
  }

  // Clear Glossary All
  if (btnClearGlossary) {
    btnClearGlossary.addEventListener("click", () => {
      clearGlossaryAll();
    });
  }

  // Studio Quick Glossary Shortcut
  if (btnStudioGlossary) {
    btnStudioGlossary.addEventListener("click", () => {
      const tabBtn = document.getElementById("tabBtnSettings");
      if (tabBtn) tabBtn.click();
      setTimeout(() => {
        if (glossaryTermInput) {
          glossaryTermInput.focus();
          glossaryTermInput.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 150);
    });
  }

  // Pipeline Preferences
  if (btnSavePipelinePrefs) {
    btnSavePipelinePrefs.addEventListener("click", async () => {
      btnSavePipelinePrefs.disabled = true;
      btnSavePipelinePrefs.textContent = "Saving...";
      try {
        const payload = {
          default_diarizer: settingDefaultDiarizer ? settingDefaultDiarizer.value : "pyannote",
          vocal_boost_level: settingVocalBoost ? settingVocalBoost.value : "adaptive",
          vram_governor_threshold_gb: settingGovCeilingSlider ? parseFloat(settingGovCeilingSlider.value) : 5.5,
          enable_audex_adjudicator: settingEnableAudex ? (settingEnableAudex.value === "true") : true,
          attention_backend: settingAttentionBackend ? settingAttentionBackend.value : "sdpa"
        };
        const res = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update pipeline settings");
        addNotification("Preferences Saved", "Pipeline, SDPA & governor preferences updated in settings.json.", "success");
        await fetchSettingsData();
      } catch (err) {
        alert("Failed to save pipeline preferences: " + err.message);
      } finally {
        btnSavePipelinePrefs.disabled = false;
        btnSavePipelinePrefs.textContent = "💾 Save All Pipeline Preferences";
      }
    });
  }

  loadGlossary();
}

// Initialize Everything on Load
initTheme();
initCacheDropdown();
initFloatingPlayer();
initTelemetryTabs();
initCadenceSelector();
initLogLevelFilters();
initTelemetryExports();
initModelManager();
initNotificationSystem();
initPyAnnoteVerifier();
initSupervisorControls();
initJournalControls();
initSettingsTab();
startTelemetryPolling();
fetchTelemetryData();



