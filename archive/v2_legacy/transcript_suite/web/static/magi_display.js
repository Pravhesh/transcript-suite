/**
 * MAGI Display Engine (4:3 High-DPI Canvas Renderer)
 * Renders the authentic Evangelion MAGI system screen at exact 7:6 core proportions,
 * 2.5cm central gap, 1.0cm cut spacing, telemetry, kanji, chunk pixel grids, and stage animations.
 * Integrates directly with code4fukui/crt-filter WebGL shaders.
 */

import { initFilterCRT } from "./crt/initFilterCRT.js";

export class MagiDisplay {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) {
      console.warn("MagiDisplay: canvas not found with id", canvasId);
      return;
    }
    this.ctx = this.canvas.getContext("2d");

    // Reference internal resolution (4:3)
    this.W = 1600;
    this.H = 1200;
    this.canvas.width = this.W;
    this.canvas.height = this.H;

    // Core Geometry Ratios (strict 7cm x 6cm => W/H = 1.1667)
    // Scale: 1 cm = 80 px
    // Core width = 560px, height = 480px (560/480 = 7/6 = 1.1667)
    // Casper-Melchior gap = 2.5/7 * 560 = 200px (2.5 cm)
    // Casper-Balthasar cut distance = 1.0/7 * 560 = 80px (1.0 cm)
    // Cut size c = (W - gap)/2 + dist / sqrt(2) = 180 + 56.57 = 236.57px (~2.96 cm)
    this.coreW = 560;
    this.coreH = 480;
    this.gap = 200;
    this.dist = 80;
    this.cut = 236.57;

    const cx = 800;

    // Casper (Core 3): Bottom-left
    this.casper = {
      x: cx - this.gap / 2 - this.coreW, // 140
      y: 160 + 300, // 460
      w: this.coreW,
      h: this.coreH,
      cut: this.cut
    };

    // Melchior (Core 1): Bottom-right
    this.melchior = {
      x: cx + this.gap / 2, // 900
      y: 160 + 300, // 460
      w: this.coreW,
      h: this.coreH,
      cut: this.cut
    };

    // Balthasar (Core 2): Top-center
    this.balthasar = {
      x: cx - this.coreW / 2, // 520
      y: 160,
      w: this.coreW,
      h: this.coreH,
      cut: this.cut
    };

    // Cut Midpoints for Perpendicular Conduits
    // Casper cut: from (c.x + c.w - cut, c.y) to (c.x + c.w, c.y + cut)
    this.casperMid = {
      x: this.casper.x + this.casper.w - this.cut / 2, // 581.715
      y: this.casper.y + this.cut / 2                   // 578.285
    };

    // Balthasar left cut: from (b.x, b.y + b.h - cut) to (b.x + cut, b.y + b.h)
    this.balthLeftMid = {
      x: this.balthasar.x + this.cut / 2,               // 638.285
      y: this.balthasar.y + this.balthasar.h - this.cut / 2 // 521.715
    };

    // Melchior cut: from (m.x, m.y + cut) to (m.x + cut, m.y)
    this.melchiorMid = {
      x: this.melchior.x + this.cut / 2,                // 1018.285
      y: this.melchior.y + this.cut / 2                 // 578.285
    };

    // Balthasar right cut: from (b.x + b.w - cut, b.y + b.h) to (b.x + b.w, b.y + b.h - cut)
    this.balthRightMid = {
      x: this.balthasar.x + this.balthasar.w - this.cut / 2, // 961.715
      y: this.balthasar.y + this.balthasar.h - this.cut / 2   // 521.715
    };

    // State Variables
    this.stage = 0;
    this.activeCoreIndex = 1; // 1: Melchior, 2: Balthasar, 3: Casper
    this.stageBlinkState = true;
    this.rapidBlinkState = true;
    this.deliberatingText = "審議中";
    this.isEmergencyBadge = false;
    
    // Pixel Blocks for transcription progress (16x16 chunk indicators)
    this.pixelBlocks = { 1: [], 2: [], 3: [] };
    this.initPixelBlocks();

    // Model labels & percentages
    this.labels = {
      1: { name: "MELCHIOR・1", sub: "Canary-Qwen • 98.2%" },
      2: { name: "BALTHASAR・2", sub: "Whisper Large • 98.4%" },
      3: { name: "CASPER・3", sub: "Conformer • 94.7%" }
    };

    // Animation timer
    this.animFrame = null;
    this.lastBlinkTime = 0;
    this.lastRapidBlinkTime = 0;
    this.lastRotateTime = 0;

    // Initialize WebGL CRT Filter
    try {
      this.crtFilter = initFilterCRT(this.canvas, [0, 0, 0]);
      console.log("MagiDisplay: WebGL CRT Filter initialized successfully.");
    } catch (e) {
      console.warn("MagiDisplay: CRT WebGL filter init failed, falling back to direct canvas:", e);
    }

    // Start render loop
    this.startLoop();
  }

  setCRTActive(active) {
    if (this.crtFilter && this.crtFilter.setActive) {
      this.crtFilter.setActive(active);
    }
  }

  initPixelBlocks() {
    const cellSize = 24;
    [1, 2, 3].forEach(id => {
      this.pixelBlocks[id] = [];
      const core = id === 1 ? this.melchior : id === 2 ? this.balthasar : this.casper;
      for (let y = core.y + 40; y < core.y + core.h - 40; y += cellSize + 4) {
        for (let x = core.x + 40; x < core.x + core.w - 40; x += cellSize + 4) {
          this.pixelBlocks[id].push({ x, y, size: cellSize, color: null });
        }
      }
    });
  }

  setStage(stage) {
    this.stage = stage;
    if (stage === 4) {
      this.isEmergencyBadge = true;
      this.deliberatingText = "非常事態";
    } else if (stage === 0) {
      this.isEmergencyBadge = false;
      this.deliberatingText = "審議中";
      // Clear corruption
      [1, 2, 3].forEach(id => {
        this.pixelBlocks[id].forEach(b => b.color = null);
      });
    } else if (stage === 1) {
      this.isEmergencyBadge = false;
      this.deliberatingText = "審議中";
    }
  }

  addChunkPixel(coreId = 1) {
    const blocks = this.pixelBlocks[coreId];
    if (!blocks || blocks.length === 0) return;
    const available = blocks.filter(b => !b.color);
    if (available.length > 0) {
      const pick = available[Math.floor(Math.random() * available.length)];
      pick.color = "#ff1a1a"; // Red chunk pixel
    }
  }

  startLoop() {
    const render = (now) => {
      this.updateTimers(now);
      this.draw();
      this.animFrame = requestAnimationFrame(render);
    };
    this.animFrame = requestAnimationFrame(render);
  }

  updateTimers(now) {
    // Slow blink for Stage 1 (1.0 sec period)
    if (now - this.lastBlinkTime > 500) {
      this.stageBlinkState = !this.stageBlinkState;
      this.lastBlinkTime = now;
    }

    // Rapid blink for Stage 4 (150ms period)
    if (now - this.lastRapidBlinkTime > 150) {
      this.rapidBlinkState = !this.rapidBlinkState;
      this.lastRapidBlinkTime = now;
    }

    // Rotation for Stage 2 and Stage 3 (750ms period)
    if (now - this.lastRotateTime > 750) {
      this.activeCoreIndex = (this.activeCoreIndex % 3) + 1;
      this.lastRotateTime = now;
    }
  }

  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);

    // Deep void black background
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, this.W, this.H);

    // 1. Draw Outer Double Red Frame (Clean, no corner tick brackets)
    this.drawOuterFrame(ctx);

    // 2. Draw Left Section (提訴 & Telemetry)
    this.drawLeftTelemetry(ctx);

    // 3. Draw Right Section (決議 & Badge)
    this.drawRightResolution(ctx);

    // 4. Draw Conduits (Perpendicular midpoints + horizontal line under MAGI)
    this.drawConduits(ctx);

    // 5. Draw Free-Floating Serif MAGI Text (No rectangle)
    this.drawMagiTitle(ctx);

    // 6. Draw the Triad Cores
    this.drawCore(ctx, 2, this.balthasar, "balthasar");
    this.drawCore(ctx, 3, this.casper, "casper");
    this.drawCore(ctx, 1, this.melchior, "melchior");

    // 7. Draw Pixel Blocks (Corruption / progress)
    this.drawPixelBlocks(ctx);
  }

  drawOuterFrame(ctx) {
    ctx.strokeStyle = "#d62020";
    // Outer border line
    ctx.lineWidth = 3;
    ctx.strokeRect(14, 14, this.W - 28, this.H - 28);
    // Inner border line
    ctx.lineWidth = 1;
    ctx.strokeRect(22, 22, this.W - 44, this.H - 44);
  }

  drawLeftTelemetry(ctx) {
    const endX = this.balthasar.x; // Green line touches Balthasar left edge (520)

    // Top and bottom green bars
    ctx.strokeStyle = "#409820";
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(60, 150); ctx.lineTo(endX, 150);
    ctx.moveTo(60, 280); ctx.lineTo(endX, 280);
    ctx.stroke();

    // Kanji "提訴" in Peach/Amber serif font
    ctx.fillStyle = "#eb8250";
    ctx.font = "900 100px 'Noto Serif JP', serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("提訴", (60 + endX) / 2, 215);

    // Telemetry readouts below green bar
    ctx.textAlign = "left";
    ctx.font = "900 30px monospace";
    ctx.fillStyle = "#ec7420";
    ctx.fillText("CODE : 378", 80, 335);

    ctx.font = "700 24px monospace";
    ctx.fillText("FILE : MAGI_SYS", 120, 380);
    ctx.fillText("EXTENTION : 2024", 120, 420);
    
    ctx.fillText("EX_MODE : ", 120, 460);
    ctx.fillStyle = "#60f0a0";
    ctx.fillText("OFF", 255, 460);

    ctx.fillStyle = "#ec7420";
    ctx.fillText("PRIORITY : AAA", 120, 500);
  }

  drawRightResolution(ctx) {
    const startX = this.balthasar.x + this.balthasar.w; // Green line starts at Balthasar right edge (1080)

    // Top and bottom green bars
    ctx.strokeStyle = "#409820";
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(startX, 150); ctx.lineTo(this.W - 60, 150);
    ctx.moveTo(startX, 280); ctx.lineTo(this.W - 60, 280);
    ctx.stroke();

    // Kanji "決議" in Peach/Amber serif font
    ctx.fillStyle = "#eb8250";
    ctx.font = "900 100px 'Noto Serif JP', serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("決議", (startX + this.W - 60) / 2, 215);

    // Status Badge under 決議 [審議中]
    const badgeW = 240;
    const badgeH = 70;
    const badgeX = (startX + this.W - 60) / 2 - badgeW / 2;
    const badgeY = 310;

    if (this.isEmergencyBadge) {
      if (this.rapidBlinkState) {
        ctx.fillStyle = "#ff1a1a";
        ctx.fillRect(badgeX, badgeY, badgeW, badgeH);
        ctx.fillStyle = "#000000";
        ctx.font = "900 42px 'Noto Serif JP', serif";
        ctx.fillText(this.deliberatingText, badgeX + badgeW / 2, badgeY + badgeH / 2 + 2);
      }
    } else {
      ctx.strokeStyle = "#409820";
      ctx.lineWidth = 3;
      ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);
      ctx.fillStyle = "#409820";
      ctx.font = "900 42px 'Noto Serif JP', serif";
      ctx.fillText(this.deliberatingText, badgeX + badgeW / 2, badgeY + badgeH / 2 + 2);
    }
  }

  drawConduits(ctx) {
    ctx.strokeStyle = "#df4028";
    ctx.lineWidth = 16;
    ctx.lineCap = "square";

    // Casper cut midpoint to Balthasar left cut midpoint (strictly perpendicular)
    ctx.beginPath();
    ctx.moveTo(this.casperMid.x, this.casperMid.y);
    ctx.lineTo(this.balthLeftMid.x, this.balthLeftMid.y);
    ctx.stroke();

    // Melchior cut midpoint to Balthasar right cut midpoint (strictly perpendicular)
    ctx.beginPath();
    ctx.moveTo(this.melchiorMid.x, this.melchiorMid.y);
    ctx.lineTo(this.balthRightMid.x, this.balthRightMid.y);
    ctx.stroke();

    // Thin horizontal line under MAGI connecting Casper right edge to Melchior left edge
    const horizY = this.balthasar.y + this.balthasar.h + 75; // 715px
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(this.casper.x + this.casper.w, horizY);
    ctx.lineTo(this.melchior.x, horizY);
    ctx.stroke();
  }

  drawMagiTitle(ctx) {
    // Free-floating serif MAGI text directly below Balthasar and above the horizontal line
    // No surrounding rectangle
    ctx.fillStyle = "#df4028";
    ctx.font = "900 52px 'Times New Roman', Times, serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.letterSpacing = "8px";
    ctx.fillText("MAGI", 800, this.balthasar.y + this.balthasar.h + 35);
  }

  drawCore(ctx, id, c, type) {
    // Determine visual state based on stage
    let isLit = true;
    let isHollow = false;

    if (this.stage === 0) {
      isLit = true; // All cyan
    } else if (this.stage === 1) {
      // Stage 1 (Deliberating): Melchior is outlined / deliberating (as in reference photo)
      if (id === 1) {
        isHollow = true;
        isLit = false;
      } else {
        isLit = true;
      }
    } else if (this.stage === 2) {
      // Stage 2 (Voting consensus)
      isHollow = true;
      isLit = (this.activeCoreIndex === id);
    } else if (this.stage === 3) {
      // Stage 3 (Infiltration / corruption)
      isLit = (this.activeCoreIndex !== id);
    } else if (this.stage === 4) {
      if (id === 1) {
        isLit = this.rapidBlinkState;
      } else {
        isLit = true;
      }
    }

    // Build polygon path
    ctx.beginPath();
    if (type === "balthasar") {
      ctx.moveTo(c.x, c.y);
      ctx.lineTo(c.x + c.w, c.y);
      ctx.lineTo(c.x + c.w, c.y + c.h - c.cut);
      ctx.lineTo(c.x + c.w - c.cut, c.y + c.h);
      ctx.lineTo(c.x + c.cut, c.y + c.h);
      ctx.lineTo(c.x, c.y + c.h - c.cut);
    } else if (type === "casper") {
      ctx.moveTo(c.x, c.y);
      ctx.lineTo(c.x + c.w - c.cut, c.y);
      ctx.lineTo(c.x + c.w, c.y + c.cut);
      ctx.lineTo(c.x + c.w, c.y + c.h);
      ctx.lineTo(c.x, c.y + c.h);
    } else if (type === "melchior") {
      ctx.moveTo(c.x + c.cut, c.y);
      ctx.lineTo(c.x + c.w, c.y);
      ctx.lineTo(c.x + c.w, c.y + c.h);
      ctx.lineTo(c.x, c.y + c.h);
      ctx.lineTo(c.x, c.y + c.cut);
    }
    ctx.closePath();

    // Fill & Stroke
    if (isHollow && !isLit) {
      // Hollow outlined state (Stage 1 Melchior in reference)
      ctx.fillStyle = "#000000";
      ctx.fill();
      ctx.strokeStyle = "#df4028";
      ctx.lineWidth = 3;
      ctx.stroke();
    } else if (isLit) {
      // Active Cyan Core
      ctx.fillStyle = "#2fa0f0"; // Vibrant Evangelion Cyan
      ctx.fill();
      ctx.strokeStyle = "#60c0ff";
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.fillStyle = "#020406";
      ctx.fill();
      ctx.strokeStyle = "#402010";
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // Text inside Core (only if lit or if label should be shown)
    if (isLit) {
      const title = this.labels[id].name;
      const sub = this.labels[id].sub;

      const titleY = (type === "balthasar") ? c.y + c.h / 2 - 20 : c.y + c.h / 2 + 30;
      const subY = titleY + 40;

      ctx.fillStyle = "#051525";
      ctx.font = "900 36px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(title, c.x + c.w / 2, titleY);

      ctx.font = "700 20px sans-serif";
      ctx.fillText(sub, c.x + c.w / 2, subY);
    }
  }

  drawPixelBlocks(ctx) {
    [1, 2, 3].forEach(id => {
      const blocks = this.pixelBlocks[id];
      blocks.forEach(b => {
        if (b.color) {
          ctx.fillStyle = b.color;
          ctx.fillRect(b.x, b.y, b.size, b.size);
          ctx.strokeStyle = "#000000";
          ctx.lineWidth = 1;
          ctx.strokeRect(b.x, b.y, b.size, b.size);
        }
      });
    });
  }
}
