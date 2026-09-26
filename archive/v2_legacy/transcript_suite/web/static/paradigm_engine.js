/**
 * Transcript Suite v2 — Dynamic Paradigm Engine
 * 
 * Orchestrates seamless switching between:
 *  - Hybrid Studio (DAW/NLE default)
 *  - Windows 95 & Winamp Pro (1997 Desktop & Titanium Player)
 *  - Silicon Graphics IRIX & NERV MAGI 1995 (Unix Workstation & Consensus)
 *  - Spatial Node Canvas (Infinite 2D Graph Visualizer)
 */

class ParadigmEngine {
  constructor() {
    this.activeParadigm = "hybrid";
    this.audioState = {
      currentTime: 0,
      duration: 0,
      isPlaying: false,
      fileName: "No file loaded"
    };
    this.segments = [];
    this.animFrameId = null;
    this.canvasTransform = { x: 40, y: 40, scale: 1.0 };
    this.isPanning = false;
    this.panStart = { x: 0, y: 0 };
    this.activeMagiView = "network";

    // Bind methods
    this.switchParadigm = this.switchParadigm.bind(this);
    this.syncAudioTime = this.syncAudioTime.bind(this);
    this.syncPlayState = this.syncPlayState.bind(this);
    this.syncSegments = this.syncSegments.bind(this);
  }

  init() {
    // 1. Check URL query parameter first (e.g. ?paradigm=win95_winamp)
    const urlParams = new URLSearchParams(window.location.search);
    const urlParadigm = urlParams.get("paradigm");
    if (urlParadigm && ["hybrid", "win95_winamp", "sgi_irix", "spatial_canvas"].includes(urlParadigm)) {
      this.activeParadigm = urlParadigm;
      localStorage.setItem("transcript_suite_paradigm", urlParadigm);
    } else {
      // 2. Restore paradigm from localStorage or fallback
      const saved = localStorage.getItem("transcript_suite_paradigm");
      if (saved && ["hybrid", "win95_winamp", "sgi_irix", "spatial_canvas"].includes(saved)) {
        this.activeParadigm = saved;
      }
    }

    // 3. Fetch server persistent setting
    fetch("/api/settings")
      .then(res => res.json())
      .then(data => {
        if (data && data.settings && data.settings.ui_paradigm) {
          const serverParadigm = data.settings.ui_paradigm;
          if (!urlParadigm && !localStorage.getItem("transcript_suite_paradigm") && ["hybrid", "win95_winamp", "sgi_irix", "spatial_canvas"].includes(serverParadigm)) {
            this.switchParadigm(serverParadigm, false);
          }
        }
      })
      .catch(() => {});

    // 3. Apply active paradigm attribute
    this.applyDOMState();

    this.switchMagiView("triad");
    this.showSubtitles = localStorage.getItem("magi_show_subtitles") !== "0";
    this.toggleSubtitles(this.showSubtitles);
    if (urlParams.get("cp") === "1") {
      this.openWin95ControlPanel();
    }
    const tabParam = urlParams.get("tab");
    if (tabParam) {
      setTimeout(() => {
        const tabBtn = document.querySelector(`[data-tab="${tabParam}"]`);
        if (tabBtn) tabBtn.click();
      }, 60);
    }

    // 4. Setup interaction listeners across shells
    this.setupWin95Listeners();
    this.setupSgiListeners();
    this.setupCanvasListeners();

    // 5. Start visualizer animation loop
    this.startOscilloscopeLoop();

    // 6. Initialize authentic MAGI-01 Triad Engine
    this.initMagiTriadEngine();

    console.log("[ParadigmEngine] Initialized with active paradigm:", this.activeParadigm);
  }

  switchParadigm(paradigmId, persist = true) {
    if (!["hybrid", "win95_winamp", "sgi_irix", "spatial_canvas"].includes(paradigmId)) return;
    this.activeParadigm = paradigmId;

    this.applyDOMState();

    if (persist) {
      localStorage.setItem("transcript_suite_paradigm", paradigmId);
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ui_paradigm: paradigmId })
      }).catch(() => {});
    }

    // Redraw paradigm-specific elements
    if (paradigmId === "spatial_canvas") {
      this.renderCanvasNodes();
    } else if (paradigmId === "win95_winamp") {
      this.renderWinampPlaylist();
    } else if (paradigmId === "sgi_irix") {
      this.renderSgiTerminal();
    }
  }

  applyDOMState() {
    const wrapper = document.getElementById("paradigm-wrapper");
    if (wrapper) {
      wrapper.setAttribute("data-active-paradigm", this.activeParadigm);
    }
    document.body.setAttribute("data-active-paradigm", this.activeParadigm);

    // Update settings selector cards
    document.querySelectorAll(".paradigm-option-card").forEach(card => {
      const p = card.getAttribute("data-paradigm");
      card.classList.toggle("active", p === this.activeParadigm);
    });

    // Update top bar badge if present
    const badge = document.getElementById("activeParadigmBadge");
    if (badge) {
      const names = {
        hybrid: "Hybrid Studio",
        win95_winamp: "Win95 & Winamp",
        sgi_irix: "SGI IRIX 1995",
        spatial_canvas: "Spatial Canvas"
      };
      badge.textContent = names[this.activeParadigm] || "Hybrid Studio";
    }
  }

  formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  syncAudioTime(currentTime, duration) {
    this.audioState.currentTime = currentTime || 0;
    this.audioState.duration = duration || 0;

    const timeStr = this.formatTime(currentTime);

    // 1. Winamp LCD display & scrub slider
    const waTimer = document.getElementById("waTimerDisplay");
    if (waTimer) waTimer.textContent = timeStr;

    const waScrub = document.getElementById("waScrubSlider");
    if (waScrub && duration > 0) {
      waScrub.value = (currentTime / duration) * 100;
    }

    // 2. SGI timecode
    const sgiClock = document.getElementById("sgiTimecodeDisplay");
    if (sgiClock) sgiClock.textContent = `T+ ${timeStr} / ${this.formatTime(duration)}`;

    // 3. Highlight currently playing segment across shells
    this.updateActiveSegmentHighlight(currentTime);
  }

  syncPlayState(isPlaying) {
    this.audioState.isPlaying = isPlaying;

    // Winamp Play/Pause toggle highlight
    const btnPlay = document.getElementById("waBtnPlay");
    const btnPause = document.getElementById("waBtnPause");
    if (btnPlay) btnPlay.classList.toggle("active", isPlaying);
    if (btnPause) btnPause.classList.toggle("active", !isPlaying);
  }

  syncLoadedFile(fileName) {
    this.audioState.fileName = fileName || "Audio_Session.wav";

    // Winamp Titlebar & LCD rate info
    const waTitle = document.getElementById("waTrackTitle");
    if (waTitle) waTitle.textContent = `1. ${this.audioState.fileName}`;

    // Win95 Window Title
    const w95Title = document.getElementById("w95WindowFileName");
    if (w95Title) w95Title.textContent = `${this.audioState.fileName} - Transcript Suite 95`;

    // SGI Session Title
    const sgiSession = document.getElementById("sgiSessionTitle");
    if (sgiSession) sgiSession.textContent = `SESSION: ${this.audioState.fileName}`;
    const triadTrack = document.getElementById("triadTrackName");
    if (triadTrack) triadTrack.textContent = this.audioState.fileName;
  }

  syncSegments(segments) {
    this.segments = segments || [];
    this.renderWin95Transcript();
    this.renderWinampPlaylist();
    this.renderSgiTerminal();
    this.renderCanvasNodes();
  }

  updateActiveSegmentHighlight(currentTime) {
    if (!this.segments.length) return;
    const currentIdx = this.segments.findIndex(s => currentTime >= (s.start || 0) && currentTime <= (s.end || 0));

    // Win95 rows
    document.querySelectorAll(".w95-segment-row").forEach((row, idx) => {
      row.classList.toggle("active", idx === currentIdx);
      if (idx === currentIdx && this.activeParadigm === "win95_winamp") {
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });

    // Evangelion MAGI Deliberation & Tactical Reaction
    if (currentIdx >= 0 && this.segments[currentIdx]) {
      const activeSeg = this.segments[currentIdx];
      const isDispute = activeSeg.dispute || (activeSeg.disputed_tokens && activeSeg.disputed_tokens.length > 0);
      const text = activeSeg.text || "";
      const spk = activeSeg.speaker || "SPEAKER";

      // View 1: Worldwide Tactical Network
      const streamText = document.getElementById("tokyoMagiTokenStream");
      const statusText = document.getElementById("magiNetworkStatus");
      const u1 = document.getElementById("tokyoUnit1");
      const u2 = document.getElementById("tokyoUnit2");
      const u3 = document.getElementById("tokyoUnit3");

      if (streamText) {
        streamText.textContent = `[TOKYO-3 MAGI 01] <${spk}> "${text.slice(0, 75)}"`;
      }

      if (isDispute) {
        if (statusText) {
          statusText.textContent = "DISPUTE ACTIVE - AUDEX ARBITRATION";
          statusText.style.color = "#ff0033";
        }
        if (u1) { u1.classList.remove("ratified"); u1.classList.add("voting"); }
        if (u2) { u2.classList.remove("ratified"); u2.classList.add("disputed"); }
        if (u3) { u3.classList.remove("ratified"); u3.classList.add("voting"); }

        // Triad Core Telemetry & Unit Glitch
        const opCode = document.getElementById("triadOpCode");
        if (opCode) opCode.textContent = "672";
        const balthasarPoly = document.getElementById("triadUnitBalthasar");
        if (balthasarPoly) balthasarPoly.classList.add("glitching", "hacked");
      } else {
        if (statusText) {
          statusText.textContent = "DEFENSE INTACT - 3-0 RATIFIED";
          statusText.style.color = "#00ff88";
        }
        if (u1) { u1.classList.remove("disputed"); u1.classList.add("ratified"); }
        if (u2) { u2.classList.remove("disputed"); u2.classList.add("ratified"); }
        if (u3) { u3.classList.remove("disputed"); u3.classList.add("ratified"); }

        // Triad Core Telemetry
        const opCode = document.getElementById("triadOpCode");
        if (opCode) opCode.textContent = "378";
        const balthasarPoly = document.getElementById("triadUnitBalthasar");
        if (balthasarPoly) balthasarPoly.classList.remove("glitching", "hacked");
      }
    }

    // Winamp playlist
    document.querySelectorAll(".wa-pl-item").forEach((item, idx) => {
      item.classList.toggle("active", idx === currentIdx);
    });

    // Spatial Canvas Nodes
    document.querySelectorAll(".canvas-node-card[data-seg-idx]").forEach((node) => {
      const idx = parseInt(node.getAttribute("data-seg-idx"), 10);
      node.classList.toggle("active-playback", idx === currentIdx);
    });
  }

  /* =========================================================================
     WIN95 & WINAMP PRO RENDERING & CONTROLS
     ========================================================================= */
  renderWin95Transcript() {
    const container = document.getElementById("w95EditorContent");
    if (!container) return;

    if (!this.segments || this.segments.length === 0) {
      container.innerHTML = `<div style="color: #666; font-style: italic;">No audio loaded. Drop a file or click File -> Open Audio...</div>`;
      return;
    }

    container.innerHTML = this.segments.map((seg, idx) => {
      const spk = seg.speaker || `Speaker ${idx % 3}`;
      const timeStr = `[${this.formatTime(seg.start)} ➔ ${this.formatTime(seg.end)}]`;
      let text = seg.text || "";

      // Highlight disputes if present
      if (seg.dispute || seg.disputed_tokens) {
        const tokens = seg.disputed_tokens || ["dispute"];
        tokens.forEach(tok => {
          const reg = new RegExp(`\\b(${tok})\\b`, "gi");
          text = text.replace(reg, `<span class="w95-dispute-chip">$1 [?]</span>`);
        });
      }

      return `
        <div class="w95-segment-row" data-seg-idx="${idx}" onclick="window.paradigmEngine.seekTo(${seg.start || 0})">
          <div class="w95-spk-hdr">${spk} ${timeStr}</div>
          <div>${text}</div>
        </div>
      `;
    }).join("");
  }

  renderWinampPlaylist() {
    const list = document.getElementById("waPlaylistItems");
    if (!list) return;

    if (!this.segments || this.segments.length === 0) {
      list.innerHTML = `<div style="color: #00aa00; font-size: 10px; padding: 4px;">1. [Empty Audio Session]</div>`;
      return;
    }

    list.innerHTML = this.segments.map((seg, idx) => {
      const dur = this.formatTime((seg.end || 0) - (seg.start || 0));
      const label = `${idx + 1}. ${(seg.speaker || "Speaker")}: ${(seg.text || "").slice(0, 24)}...`;
      return `
        <div class="wa-pl-item" onclick="window.paradigmEngine.seekTo(${seg.start || 0})">
          <span>${label}</span>
          <span>${dur}</span>
        </div>
      `;
    }).join("");
  }

  setupWin95Listeners() {
    // Start Menu toggle
    const startBtn = document.getElementById("w95StartBtn");
    const startMenu = document.getElementById("w95StartMenu");
    if (startBtn && startMenu) {
      startBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = startMenu.classList.toggle("open");
        startBtn.classList.toggle("open", isOpen);
      });

      document.addEventListener("click", () => {
        startMenu.classList.remove("open");
        startBtn.classList.remove("open");
      });
    }

    // Winamp Transport Buttons
    const btnPlay = document.getElementById("waBtnPlay");
    const btnPause = document.getElementById("waBtnPause");
    const btnStop = document.getElementById("waBtnStop");
    const btnEject = document.getElementById("waBtnEject");

    if (btnPlay) {
      btnPlay.addEventListener("click", () => {
        if (window.wavesurferOrig) window.wavesurferOrig.play();
      });
    }
    if (btnPause) {
      btnPause.addEventListener("click", () => {
        if (window.wavesurferOrig) window.wavesurferOrig.pause();
      });
    }
    if (btnStop) {
      btnStop.addEventListener("click", () => {
        if (window.wavesurferOrig) {
          window.wavesurferOrig.stop();
          window.wavesurferOrig.seekTo(0);
        }
      });
    }
    if (btnEject) {
      btnEject.addEventListener("click", () => {
        const fileIn = document.getElementById("fileInput");
        if (fileIn) fileIn.click();
      });
    }

    // Winamp Scrub Slider
    const waScrub = document.getElementById("waScrubSlider");
    if (waScrub) {
      waScrub.addEventListener("input", (e) => {
        const pct = parseFloat(e.target.value) / 100;
        if (window.wavesurferOrig && this.audioState.duration > 0) {
          window.wavesurferOrig.seekTo(pct);
        }
      });
    }

    // Win95 Desktop Icons Action
    const iconControlPanel = document.getElementById("w95IconControlPanel");
    if (iconControlPanel) {
      iconControlPanel.addEventListener("click", () => this.openSettings());
    }
  }

  seekTo(seconds) {
    if (window.wavesurferOrig && this.audioState.duration > 0) {
      const pct = Math.max(0, Math.min(1, seconds / this.audioState.duration));
      window.wavesurferOrig.seekTo(pct);
      window.wavesurferOrig.play();
    }
  }

  openSettings() {
    if (this.activeParadigm === "win95_winamp") {
      this.openWin95ControlPanel();
    } else if (this.activeParadigm === "sgi_irix") {
      this.openSgiSettings();
    } else {
      const settingsTab = document.getElementById("tabBtnSettings");
      if (settingsTab) settingsTab.click();
      this.switchParadigm("hybrid");
    }
  }

  openWin95ControlPanel() {
    const modal = document.getElementById("w95ControlPanelModal");
    if (modal) modal.style.display = "flex";
  }

  closeWin95ControlPanel() {
    const modal = document.getElementById("w95ControlPanelModal");
    if (modal) modal.style.display = "none";
  }

  switchWin95CpTab(tabId, btn) {
    document.querySelectorAll(".w95-cp-tab").forEach(t => t.classList.remove("active"));
    if (btn) btn.classList.add("active");
    document.querySelectorAll(".w95-cp-pane").forEach(p => p.classList.remove("active"));
    const pane = document.getElementById(`w95Cp${tabId.charAt(0).toUpperCase() + tabId.slice(1)}`);
    if (pane) pane.classList.add("active");
  }

  switchMagiView(viewId = "triad") {
    this.activeMagiView = "triad";
    document.querySelectorAll(".magi-view-container").forEach(c => c.classList.remove("active"));
    const active = document.getElementById("magiViewTriad");
    if (active) active.classList.add("active");

    const title = document.getElementById("magiHeaderTitle");
    if (title) {
      title.textContent = "MAGI SYSTEM // TRIAD CORE";
    }
  }

  setTranscribing(isTranscribing) {
    this.isTranscribing = !!isTranscribing;
    if (!isTranscribing) {
      this.setMagiStage(0);
    }
  }

  toggleSubtitles(forceState) {
    if (typeof forceState === "boolean") {
      this.showSubtitles = forceState;
    } else {
      this.showSubtitles = !this.showSubtitles;
    }
    const triad = document.getElementById("magiViewTriad");
    if (triad) {
      triad.classList.toggle("hide-subtitles", !this.showSubtitles);
    }
    const indicator = document.getElementById("subtitlesToggleState");
    if (indicator) {
      indicator.textContent = this.showSubtitles ? "ON" : "OFF";
      indicator.style.color = this.showSubtitles ? "#00ff88" : "#88a2b5";
    }
    localStorage.setItem("magi_show_subtitles", this.showSubtitles ? "1" : "0");
  }

  /* =========================================================================
     MAGI-01 TRIAD 11-STAGE STATE MACHINE & PIXEL MOSAIC ENGINE
     ========================================================================= */

  initMagiTriadEngine() {
    this.magiStage = 0;
    this.coreMap = {
      1: document.getElementById("triadUnitMelchior"),
      2: document.getElementById("triadUnitBalthasar"),
      3: document.getElementById("triadUnitCasper")
    };
    this.mosaicMap = {
      1: document.getElementById("mosaicMelchior"),
      2: document.getElementById("mosaicBalthasar"),
      3: document.getElementById("mosaicCasper")
    };
    this.pixelGrids = { 1: [], 2: [], 3: [] };
    this.stageTimers = [];
    this.rotationInterval = null;
    this.disputeHistory = [];

    // Generate initial pixel grids
    this.buildPixelGrids();
  }

  buildPixelGrids() {
    const specs = {
      1: { minX: 484, maxX: 729, minY: 260, maxY: 470 }, // Melchior (245x210)
      2: { minX: 317, maxX: 563, minY: 25, maxY: 235 },   // Balthasar (246x210)
      3: { minX: 151, maxX: 396, minY: 260, maxY: 470 }   // Casper (245x210)
    };

    const cellSize = 16;
    for (let coreId = 1; coreId <= 3; coreId++) {
      const container = this.mosaicMap[coreId];
      if (!container) continue;
      container.innerHTML = "";
      this.pixelGrids[coreId] = [];
      const { minX, maxX, minY, maxY } = specs[coreId];

      for (let y = minY; y < maxY; y += cellSize) {
        for (let x = minX; x < maxX; x += cellSize) {
          const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
          rect.setAttribute("x", x);
          rect.setAttribute("y", y);
          rect.setAttribute("width", cellSize);
          rect.setAttribute("height", cellSize);
          rect.setAttribute("class", "px-cell");
          container.appendChild(rect);
          this.pixelGrids[coreId].push(rect);
        }
      }
    }
  }

  clearStageTimers() {
    if (this.stageTimers) {
      this.stageTimers.forEach(t => clearTimeout(t));
      this.stageTimers = [];
    }
    if (this.rotationInterval) {
      clearInterval(this.rotationInterval);
      this.rotationInterval = null;
    }
  }

  resetAllCores() {
    [1, 2, 3].forEach(id => {
      const unit = this.coreMap[id];
      if (unit) {
        unit.classList.remove("core-rapid-blink", "core-slow-blink", "core-hollow", "core-off", "core-cyan");
      }
    });
  }

  setMagiBadge(type, text) {
    const badge = document.getElementById("triadDeliberatingBadge");
    const badgeText = document.getElementById("triadBadgeText");
    if (!badge || !badgeText) return;

    badge.classList.remove("badge-emergency", "badge-recovery", "badge-deliberating", "deliberating-active");
    if (type === "off") {
      badge.style.opacity = "0";
      return;
    }
    badge.style.opacity = "1";
    badge.classList.add("deliberating-active");
    if (type === "emergency") {
      badge.classList.add("badge-emergency");
      badgeText.textContent = text || "非常事態";
    } else if (type === "recovery") {
      badge.classList.add("badge-recovery");
      badgeText.textContent = text || "回復";
    } else if (type === "deliberating") {
      badge.classList.add("badge-deliberating");
      badgeText.textContent = text || "審議中";
    }
  }

  setEmergencyBanner(show) {
    const banner = document.getElementById("sgiHeaderEmergency");
    const normal = document.getElementById("sgiHeaderNormal");
    if (banner && normal) {
      banner.style.display = show ? "flex" : "none";
      normal.style.display = show ? "none" : "flex";
    }
    const evBanner = document.getElementById("evEmergencyBanner");
    if (evBanner) {
      evBanner.classList.toggle("active", !!show);
    }
  }

  corruptCorePixels(coreId, targetFrac = 1.0, maxCap = 1.0) {
    if (window.magiDisplay) {
      window.magiDisplay.addChunkPixel(coreId);
    }
    const grid = this.pixelGrids[coreId];
    if (!grid || !grid.length) return;

    const effectiveFrac = Math.min(targetFrac, maxCap);
    const targetCount = Math.floor(grid.length * effectiveFrac);

    const uncolored = grid.filter(cell => !cell.classList.contains("px-red"));
    const currentRed = grid.filter(cell => cell.classList.contains("px-red")).length;
    const needed = Math.max(0, targetCount - currentRed);

    for (let i = 0; i < needed; i++) {
      const cell = uncolored[Math.floor(Math.random() * uncolored.length)];
      if (cell) {
        if (!cell.classList.contains("px-black")) {
          cell.classList.add("px-black");
        }
        if (Math.random() < 0.6 || targetFrac >= 0.99) {
          cell.classList.remove("px-black");
          cell.classList.add("px-red");
        }
      }
    }

    if (targetFrac >= 1.0 && maxCap >= 1.0) {
      grid.forEach(cell => {
        cell.classList.remove("px-black");
        cell.classList.add("px-red");
      });
    } else if (maxCap < 1.0 && targetFrac >= 1.0) {
      // 99% cap for Casper in Stage 6 (leaves 1% blue)
      const keepBlueCount = Math.max(1, Math.floor(grid.length * (1.0 - maxCap)));
      grid.forEach((cell, idx) => {
        if (idx < grid.length - keepBlueCount) {
          cell.classList.remove("px-black");
          cell.classList.add("px-red");
        } else {
          cell.classList.remove("px-black", "px-red");
        }
      });
    }
  }

  reclaimCorePixels(coreId, onDone) {
    const grid = this.pixelGrids[coreId];
    if (!grid || !grid.length) {
      if (onDone) onDone();
      return;
    }
    const redCells = grid.filter(cell => cell.classList.contains("px-red") || cell.classList.contains("px-black"));
    if (!redCells.length) {
      if (onDone) onDone();
      return;
    }

    const stepBatch = Math.max(2, Math.ceil(redCells.length / 15));
    const interval = setInterval(() => {
      const remaining = grid.filter(cell => cell.classList.contains("px-red") || cell.classList.contains("px-black"));
      if (!remaining.length) {
        clearInterval(interval);
        if (onDone) onDone();
        return;
      }
      for (let i = 0; i < stepBatch && remaining.length > 0; i++) {
        const randIdx = Math.floor(Math.random() * remaining.length);
        const cell = remaining.splice(randIdx, 1)[0];
        cell.classList.remove("px-red", "px-black");
        cell.classList.add("px-blue");
        setTimeout(() => cell.classList.remove("px-blue"), 300);
      }
    }, 80);
  }

  async setMagiStage(stageIndex, data = {}) {
    this.magiStage = stageIndex;
    this.clearStageTimers();
    if (window.magiDisplay) {
      window.magiDisplay.setStage(typeof stageIndex === "number" ? stageIndex : 4);
    }

    const opCode = document.getElementById("triadOpCode");

    switch (stageIndex) {
      case 0: // Idle / Standby
        this.resetAllCores();
        this.setEmergencyBanner(false);
        this.setMagiBadge("off");
        if (opCode) opCode.textContent = "378";
        [1, 2, 3].forEach(id => {
          (this.pixelGrids[id] || []).forEach(c => c.classList.remove("px-red", "px-black", "px-blue"));
        });
        break;

      case 1: // Ingestion & Audio Prep: Slow blinking on all cores
        this.resetAllCores();
        this.setEmergencyBanner(false);
        this.setMagiBadge("off");
        if (opCode) opCode.textContent = "412";
        [1, 2, 3].forEach(id => {
          this.coreMap[id]?.classList.add("core-slow-blink");
        });
        break;

      case 2: // VAD: All cores hollow, one active at a time rotating clockwise slowly
        this.resetAllCores();
        this.setEmergencyBanner(false);
        this.setMagiBadge("off");
        if (opCode) opCode.textContent = "204";
        [1, 2, 3].forEach(id => {
          this.coreMap[id]?.classList.add("core-hollow");
        });
        {
          const order = [3, 2, 1]; // Casper (3) -> Balthasar (2) -> Melchior (1)
          let step = 0;
          this.rotationInterval = setInterval(() => {
            [1, 2, 3].forEach(id => this.coreMap[id]?.classList.add("core-hollow"));
            const activeId = order[step % 3];
            this.coreMap[activeId]?.classList.remove("core-hollow");
            step++;
          }, 800);
        }
        break;

      case 3: // Speaker Diarization: One core off at a time, rotating slowly
        this.resetAllCores();
        this.setEmergencyBanner(false);
        this.setMagiBadge("off");
        if (opCode) opCode.textContent = "289";
        {
          const order = [1, 2, 3];
          let step = 0;
          this.rotationInterval = setInterval(() => {
            [1, 2, 3].forEach(id => this.coreMap[id]?.classList.remove("core-off"));
            const offId = order[step % 3];
            this.coreMap[offId]?.classList.add("core-off");
            step++;
          }, 800);
        }
        break;

      case 4: // Council Pass 1: Core 1 (Melchior) rapid blink (min 5s), Emergency banner ON, red 非常事態 badge, 16x16 pixel corruption
        this.resetAllCores();
        this.setEmergencyBanner(true);
        this.setMagiBadge("emergency", "非常事態");
        if (opCode) opCode.textContent = "666";

        this.coreMap[1]?.classList.add("core-rapid-blink");
        await this.waitMs(5000);
        this.coreMap[1]?.classList.remove("core-rapid-blink");

        this.corruptCorePixels(1, data.progress || 1.0, 1.0);
        break;

      case "4_unload": // Pass 1 unload callback: blue cores off, Core 1 blinks rapidly for 3s min, blue cores restore
        this.coreMap[2]?.classList.add("core-off");
        this.coreMap[3]?.classList.add("core-off");
        this.coreMap[1]?.classList.add("core-rapid-blink");
        await this.waitMs(3000);
        this.coreMap[1]?.classList.remove("core-rapid-blink");
        this.coreMap[2]?.classList.remove("core-off");
        this.coreMap[3]?.classList.remove("core-off");
        break;

      case 5: // Council Pass 2: Core 2 (Balthasar) rapid blink (min 5s), 16x16 pixel corruption
        this.setEmergencyBanner(true);
        this.setMagiBadge("emergency", "非常事態");
        if (opCode) opCode.textContent = "666";
        this.corruptCorePixels(1, 1.0, 1.0);

        this.coreMap[2]?.classList.add("core-rapid-blink");
        await this.waitMs(5000);
        this.coreMap[2]?.classList.remove("core-rapid-blink");

        this.corruptCorePixels(2, data.progress || 1.0, 1.0);
        break;

      case "5_unload": // Pass 2 unload callback: blue core 3 off, Core 1 & 2 blink rapidly for 3s min, restore
        this.coreMap[3]?.classList.add("core-off");
        this.coreMap[1]?.classList.add("core-rapid-blink");
        this.coreMap[2]?.classList.add("core-rapid-blink");
        await this.waitMs(3000);
        this.coreMap[1]?.classList.remove("core-rapid-blink");
        this.coreMap[2]?.classList.remove("core-rapid-blink");
        this.coreMap[3]?.classList.remove("core-off");
        break;

      case 6: // Council Pass 3A: Core 3 (Casper) rapid blink (min 5s), max 99% corruption
        this.setEmergencyBanner(true);
        this.setMagiBadge("emergency", "非常事態");
        if (opCode) opCode.textContent = "666";
        this.corruptCorePixels(1, 1.0, 1.0);
        this.corruptCorePixels(2, 1.0, 1.0);

        this.coreMap[3]?.classList.add("core-rapid-blink");
        await this.waitMs(5000);
        this.coreMap[3]?.classList.remove("core-rapid-blink");

        this.corruptCorePixels(3, data.progress || 1.0, 0.99);
        break;

      case "6_unload": // Pass 3A unload: Core 3 retains 1% blue and DOES NOT blink
        // Nothing blinks
        break;

      case 7: // Council Pass 3B: Terminal popup (min 5s), badge turns 回復, reclaim Core 3 -> Core 2 -> Core 1, banner OFF, unload slow blink 3s
        this.openHackerTerminal();
        await this.waitMs(5000);
        this.closeHackerTerminal();

        this.setMagiBadge("recovery", "回復");

        await new Promise(resolve => this.reclaimCorePixels(3, resolve));
        await new Promise(resolve => this.reclaimCorePixels(2, resolve));
        await new Promise(resolve => this.reclaimCorePixels(1, resolve));

        this.setEmergencyBanner(false);
        break;

      case "7_unload": // Pass 3B unload: All cores blink slowly simultaneously min 3s then stop
        [1, 2, 3].forEach(id => this.coreMap[id]?.classList.add("core-slow-blink"));
        await this.waitMs(3000);
        [1, 2, 3].forEach(id => this.coreMap[id]?.classList.remove("core-slow-blink"));
        break;

      case 8: // Council Adjudication: 3 slow cyan blinks -> blue; 審議中 badge; dispute arbitration
        this.setEmergencyBanner(false);
        this.setMagiBadge("deliberating", "審議中");
        if (opCode) opCode.textContent = "501";

        for (let i = 0; i < 3; i++) {
          [1, 2, 3].forEach(id => this.coreMap[id]?.classList.add("core-cyan"));
          await this.waitMs(500);
          [1, 2, 3].forEach(id => this.coreMap[id]?.classList.remove("core-cyan"));
          await this.waitMs(300);
        }

        if (data.disputes && data.disputes.length > 0) {
          await this.runDisputeArbitration(data.disputes);
        }
        break;

      case 9:
      case 10: // Completion / Session Playback
        this.setEmergencyBanner(false);
        this.setMagiBadge("off");
        if (opCode) opCode.textContent = "132";
        [1, 2, 3].forEach(id => this.coreMap[id]?.classList.add("core-slow-blink"));
        await this.waitMs(2000);
        [1, 2, 3].forEach(id => this.coreMap[id]?.classList.remove("core-slow-blink"));
        break;
    }
  }

  async runDisputeArbitration(disputes = []) {
    const startTime = Date.now();
    let lastCore = null;
    let repeatCount = 0;

    while (Date.now() - startTime < 3000) {
      let coreId;
      do {
        coreId = Math.floor(Math.random() * 3) + 1;
      } while (coreId === lastCore && repeatCount >= 3);

      if (coreId === lastCore) {
        repeatCount++;
      } else {
        lastCore = coreId;
        repeatCount = 1;
      }

      this.coreMap[coreId]?.classList.add("core-rapid-blink");
      await this.waitMs(350);
      this.coreMap[coreId]?.classList.remove("core-rapid-blink");
      await this.waitMs(100);
    }

    const winningCore = Math.floor(Math.random() * 3) + 1;
    this.coreMap[winningCore]?.classList.add("core-cyan");
    await this.waitMs(1500);
    this.coreMap[winningCore]?.classList.remove("core-cyan");

    for (let i = 0; i < 3; i++) {
      [1, 2, 3].forEach(id => this.coreMap[id]?.classList.add("core-cyan"));
      await this.waitMs(450);
      [1, 2, 3].forEach(id => this.coreMap[id]?.classList.remove("core-cyan"));
      await this.waitMs(250);
    }
  }

  waitMs(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  openHackerTerminal() {
    const modal = document.getElementById("sgiHackerTerminalModal");
    const stream = document.getElementById("sgiHackerTerminalStream");
    if (!modal || !stream) return;

    modal.style.display = "flex";
    const lines = [
      "[ALERT] UNIDENTIFIED PATTERN BLOOD-TYPE: ORANGE",
      "[MAGI_KERNEL] INITIALIZING PURGE PROTOCOL 666...",
      "[AT_FIELD] DIVERGENCE COMPENSATOR: 99.8% STABLE",
      "[CASPER_RESTORE] MEMORY ACCESS GRANTED BY NERV HQ",
      "[SYNCHRONIZATION] PURGING RED TYPE-3 CONTAMINANTS",
      "[REWRITE_VECTOR] RESTORING KERNEL HEURISTICS",
      "[SYS_SUCCESS] ANOMALY RESOLVED. AT-FIELD RE-ESTABLISHED."
    ];

    stream.innerHTML = "";
    lines.forEach((l, idx) => {
      setTimeout(() => {
        const div = document.createElement("div");
        div.className = "hacker-line" + (idx === 0 || idx === lines.length - 1 ? " highlight" : "");
        div.textContent = l;
        stream.appendChild(div);
        stream.scrollTop = stream.scrollHeight;
      }, idx * 600);
    });
  }

  closeHackerTerminal() {
    const modal = document.getElementById("sgiHackerTerminalModal");
    if (modal) modal.style.display = "none";
  }

  updateFromPipelineMessage(msg = "", progress = 0, data = {}) {
    const text = (msg || "").toLowerCase();
    
    if (text.includes("loading audio") || text.includes("gpu speech") || text.includes("converting to 16khz")) {
      if (this.magiStage !== 1) this.setMagiStage(1);
    } else if (text.includes("voice activity") || text.includes("vad")) {
      if (this.magiStage !== 2) this.setMagiStage(2);
    } else if (text.includes("diarization")) {
      if (this.magiStage !== 3) this.setMagiStage(3);
    } else if (text.includes("pass 1") || text.includes("canary")) {
      if (text.includes("unloaded")) {
        this.setMagiStage("4_unload");
      } else {
        const prog = (progress >= 38 && progress <= 56) ? (progress - 38) / 18 : (progress / 100);
        if (this.magiStage !== 4) this.setMagiStage(4, { progress: prog });
        else this.corruptCorePixels(1, prog, 1.0);
      }
    } else if (text.includes("pass 2") || text.includes("whisper")) {
      if (text.includes("unloaded")) {
        this.setMagiStage("5_unload");
      } else {
        const prog = (progress >= 56 && progress <= 74) ? (progress - 56) / 18 : (progress / 100);
        if (this.magiStage !== 5) this.setMagiStage(5, { progress: prog });
        else this.corruptCorePixels(2, prog, 1.0);
      }
    } else if (text.includes("phase a") || text.includes("conformer")) {
      if (text.includes("unloaded")) {
        this.setMagiStage("6_unload");
      } else {
        const prog = (progress >= 74 && progress <= 81) ? (progress - 74) / 7 : (progress / 100);
        if (this.magiStage !== 6) this.setMagiStage(6, { progress: prog });
        else this.corruptCorePixels(3, prog, 0.99);
      }
    } else if (text.includes("phase b") || text.includes("parakeet")) {
      if (text.includes("unloaded")) {
        this.setMagiStage("7_unload");
      } else {
        if (this.magiStage !== 7) this.setMagiStage(7);
      }
    } else if (text.includes("adjudicating") || text.includes("consensus")) {
      if (this.magiStage !== 8) this.setMagiStage(8, { disputes: data.disputes || [] });
    }
  }

  openSgiSettings() {
    const term = document.getElementById("sgiTerminalBody");
    if (term) {
      term.innerHTML = `
        <div style="color: #ffaa00; font-weight:700;">[MAGI KERNEL CONFIGURATION]</div>
        <div style="color: #00ff88;">• Hardware: SGI Indigo2 R10000 / Octane Vector DSP</div>
        <div style="color: #60a5fa;">• VRAM Threshold: 5.5 GB Governor Active</div>
        <div style="color: #eab308;">• Audex Reasoner: Stage 5 Neural Adjudication ENGAGED</div>
        <div style="color: #ededed;">• Storage: /mnt/d/transcript_suite_data (284.9 GB Used / 105.7 GB Free)</div>
      `;
    }
  }


  /* =========================================================================
     SGI IRIX & NERV MAGI RENDERING
     ========================================================================= */
  renderSgiTerminal() {
    const term = document.getElementById("sgiTerminalBody");
    if (!term) return;

    if (!this.segments || this.segments.length === 0) {
      term.innerHTML = `<div style="color: #64748b;">[MAGI KERNEL] Idle. Ready for audio ingestion.</div>`;
      return;
    }

    term.innerHTML = this.segments.map((seg, idx) => {
      const spk = seg.speaker || `CH_${idx % 3}`;
      return `
        <div style="margin-bottom: 8px; cursor: pointer;" onclick="window.paradigmEngine.seekTo(${seg.start || 0})">
          <span style="color: #00ff88;">[${this.formatTime(seg.start)}]</span>
          <strong style="color: #60a5fa;">&lt;${spk}&gt;</strong>
          <span>${seg.text || ""}</span>
        </div>
      `;
    }).join("");

    // Update MAGI Boxes based on latest dispute
    const disputeSeg = this.segments.find(s => s.dispute || (s.disputed_tokens && s.disputed_tokens.length > 0));
    const banner = document.getElementById("sgiMagiBanner");
    const m1 = document.getElementById("magiBox1Vote");
    const m2 = document.getElementById("magiBox2Vote");
    const m3 = document.getElementById("magiBox3Vote");

    if (disputeSeg) {
      if (banner) banner.innerHTML = `<div>STATUS: DISPUTE ARBITRATION ACTIVE</div><div style="color: #f59e0b;">PLURALITY 2-1 RATIFIED</div>`;
      if (m1) m1.textContent = '"UPTICK"';
      if (m2) m2.textContent = '"UPTURN"';
      if (m3) m3.textContent = '"UPTICK"';
    } else {
      if (banner) banner.innerHTML = `<div>STATUS: DELIBERATION RATIFIED</div><div style="color: #00ff88;">UNANIMOUS CONSENSUS</div>`;
      if (m1) m1.textContent = '"AGREE"';
      if (m2) m2.textContent = '"AGREE"';
      if (m3) m3.textContent = '"AGREE"';
    }
  }

  setupSgiListeners() {
    const btnSgiSettings = document.getElementById("sgiBtnSettings");
    if (btnSgiSettings) {
      btnSgiSettings.addEventListener("click", () => this.openSettings());
    }
    const btnSgiUpload = document.getElementById("sgiBtnUpload");
    if (btnSgiUpload) {
      btnSgiUpload.addEventListener("click", () => {
        const fileIn = document.getElementById("fileInput");
        if (fileIn) fileIn.click();
      });
    }
  }

  /* =========================================================================
     SPATIAL NODE CANVAS RENDERING & PAN/ZOOM
     ========================================================================= */
  renderCanvasNodes() {
    const plane = document.getElementById("canvasPlane");
    const svgLayer = document.getElementById("canvasSvgLayer");
    if (!plane || !svgLayer) return;

    // Clear dynamic segments (keep fixed root nodes)
    plane.querySelectorAll(".canvas-dynamic-node").forEach(n => n.remove());
    svgLayer.innerHTML = "";

    // 1. Audio Root Node
    const audioNode = document.getElementById("canvasNodeAudio");
    const jurNode1 = document.getElementById("canvasNodeWhisper");
    const jurNode2 = document.getElementById("canvasNodeCanary");
    const jurNode3 = document.getElementById("canvasNodeNova");
    const verdictNode = document.getElementById("canvasNodeVerdict");

    // Draw base cables between core nodes
    if (audioNode && jurNode1 && jurNode2 && jurNode3 && verdictNode) {
      this.drawCable(audioNode, jurNode1, svgLayer, false);
      this.drawCable(audioNode, jurNode2, svgLayer, false);
      this.drawCable(audioNode, jurNode3, svgLayer, false);
      this.drawCable(jurNode1, verdictNode, svgLayer, false);
      this.drawCable(jurNode2, verdictNode, svgLayer, true); // Canary dispute cable
      this.drawCable(jurNode3, verdictNode, svgLayer, false);
    }

    // 2. Render Utterance Stream Nodes
    if (!this.segments || this.segments.length === 0) return;

    let startX = 680;
    let startY = 160;

    this.segments.slice(0, 10).forEach((seg, idx) => {
      const node = document.createElement("div");
      node.className = "canvas-node-card canvas-dynamic-node";
      node.setAttribute("data-seg-idx", idx);
      node.style.left = `${startX}px`;
      node.style.top = `${startY}px`;
      node.style.width = "260px";

      const isDisputed = seg.dispute || (seg.disputed_tokens && seg.disputed_tokens.length > 0);
      node.innerHTML = `
        <div class="node-hdr">
          <span class="node-title">${seg.speaker || `Speaker ${idx}`}</span>
          <span style="font-family:monospace; font-size:9.5px;">${this.formatTime(seg.start)}</span>
        </div>
        <div style="font-size: 11.5px; line-height: 1.5; color: #cbd5e1;">${seg.text || ""}</div>
      `;

      node.addEventListener("click", () => this.seekTo(seg.start || 0));
      plane.appendChild(node);

      // Draw cable from verdict node to first utterance, then between utterances
      if (idx === 0 && verdictNode) {
        this.drawCable(verdictNode, node, svgLayer, isDisputed);
      }

      startX += 290;
      startY += (idx % 2 === 0 ? 50 : -40);
    });
  }

  drawCable(nodeA, nodeB, svgContainer, isDisputed) {
    const rA = nodeA.getBoundingClientRect();
    const rB = nodeB.getBoundingClientRect();
    const pRect = svgContainer.getBoundingClientRect();

    const x1 = (rA.right - pRect.left) / this.canvasTransform.scale;
    const y1 = (rA.top + rA.height / 2 - pRect.top) / this.canvasTransform.scale;
    const x2 = (rB.left - pRect.left) / this.canvasTransform.scale;
    const y2 = (rB.top + rB.height / 2 - pRect.top) / this.canvasTransform.scale;

    const dx = Math.abs(x2 - x1) * 0.5;
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("class", isDisputed ? "canvas-cable-path disputed" : "canvas-cable-path");
    svgContainer.appendChild(path);
  }

  setupCanvasListeners() {
    const viewport = document.getElementById("canvasViewport");
    const plane = document.getElementById("canvasPlane");
    if (!viewport || !plane) return;

    viewport.addEventListener("mousedown", (e) => {
      if (e.target.closest(".canvas-node-card") || e.target.closest(".canvas-hud-toolbar")) return;
      this.isPanning = true;
      this.panStart = { x: e.clientX - this.canvasTransform.x, y: e.clientY - this.canvasTransform.y };
    });

    window.addEventListener("mousemove", (e) => {
      if (!this.isPanning) return;
      this.canvasTransform.x = e.clientX - this.panStart.x;
      this.canvasTransform.y = e.clientY - this.panStart.y;
      this.applyCanvasTransform();
    });

    window.addEventListener("mouseup", () => {
      this.isPanning = false;
    });

    viewport.addEventListener("wheel", (e) => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.08 : 0.92;
      this.canvasTransform.scale = Math.max(0.4, Math.min(2.0, this.canvasTransform.scale * zoomFactor));
      this.applyCanvasTransform();
    }, { passive: false });

    // HUD buttons
    const btnZoomIn = document.getElementById("canvasBtnZoomIn");
    const btnZoomOut = document.getElementById("canvasBtnZoomOut");
    const btnReset = document.getElementById("canvasBtnReset");
    const btnSettings = document.getElementById("canvasBtnSettings");

    if (btnZoomIn) {
      btnZoomIn.addEventListener("click", () => {
        this.canvasTransform.scale = Math.min(2.0, this.canvasTransform.scale * 1.15);
        this.applyCanvasTransform();
      });
    }
    if (btnZoomOut) {
      btnZoomOut.addEventListener("click", () => {
        this.canvasTransform.scale = Math.max(0.4, this.canvasTransform.scale * 0.85);
        this.applyCanvasTransform();
      });
    }
    if (btnReset) {
      btnReset.addEventListener("click", () => {
        this.canvasTransform = { x: 40, y: 40, scale: 1.0 };
        this.applyCanvasTransform();
      });
    }
    if (btnSettings) {
      btnSettings.addEventListener("click", () => this.openSettings());
    }
  }

  applyCanvasTransform() {
    const plane = document.getElementById("canvasPlane");
    if (plane) {
      plane.style.transform = `translate(${this.canvasTransform.x}px, ${this.canvasTransform.y}px) scale(${this.canvasTransform.scale})`;
    }
  }

  /* =========================================================================
     ANIMATED OSCILLOSCOPE & SPECTRUM ENGINE
     ========================================================================= */
  startOscilloscopeLoop() {
    const canvas = document.getElementById("sgiOscilloscopeCanvas");
    const ctx = canvas ? canvas.getContext("2d") : null;
    let phase = 0;

    const animate = () => {
      // 1. Winamp animated EQ bars
      const eqBars = document.querySelectorAll(".wa-eq-bar");
      if (eqBars.length > 0) {
        eqBars.forEach((bar, i) => {
          if (this.audioState.isPlaying) {
            const h = 20 + Math.sin(phase + i * 0.8) * 35 + Math.random() * 25;
            bar.style.height = `${Math.min(100, Math.max(8, h))}%`;
          } else {
            bar.style.height = "6px";
          }
        });
      }

      // 2. SGI Vector Oscilloscope (Canvas Wireframe)
      if (canvas && ctx && this.activeParadigm === "sgi_irix") {
        ctx.fillStyle = "#05080c";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Draw green phosphorus grid
        ctx.strokeStyle = "rgba(0, 255, 136, 0.15)";
        ctx.lineWidth = 1;
        for (let x = 0; x < canvas.width; x += 30) {
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, canvas.height);
          ctx.stroke();
        }
        for (let y = 0; y < canvas.height; y += 20) {
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(canvas.width, y);
          ctx.stroke();
        }

        // Draw animated 3D Lissajous / FFT vector waves
        ctx.strokeStyle = "#00ff88";
        ctx.shadowColor = "#00ff88";
        ctx.shadowBlur = 8;
        ctx.lineWidth = 2;
        ctx.beginPath();

        const midY = canvas.height / 2;
        const amp = this.audioState.isPlaying ? 38 : 4;

        for (let x = 0; x < canvas.width; x += 3) {
          const y = midY + Math.sin((x * 0.04) + phase) * amp * Math.cos((x * 0.01) + phase * 0.5);
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      phase += this.audioState.isPlaying ? 0.12 : 0.02;
      this.animFrameId = requestAnimationFrame(animate);
    };

    animate();
  }
}

// Global Singleton Instance
window.paradigmEngine = new ParadigmEngine();
document.addEventListener("DOMContentLoaded", () => {
  window.paradigmEngine.init();
});
