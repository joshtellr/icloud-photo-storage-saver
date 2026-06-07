#!/usr/bin/env python3
"""
Lightweight web dashboard for monitoring photos_compress.py progress.
No dependencies beyond Python stdlib.

Usage:
  python3 ~/photos_monitor.py
  Then open: http://localhost:8420
"""

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LOG_FILE = Path.home() / "Desktop" / "compress_log.txt"
PORT = 8420


# ─── Log Parser ────────────────────────────────────────────────────────────────

def parse_log():
    if not LOG_FILE.exists():
        return {"error": "Log file not found. Is photos_compress.py running?"}

    text = LOG_FILE.read_text(errors="replace")
    lines = text.splitlines()

    result = {
        "total_files": 0,
        "total_size_str": "",
        "files": [],
        "job_running": is_job_running(),
    }

    # Header: total count and size
    for line in lines:
        m = re.search(r"Found (\d+) files", line)
        if m:
            result["total_files"] = int(m.group(1))
        m = re.search(r"Total size: ([\d.]+ \w+)", line)
        if m:
            result["total_size_str"] = m.group(1)

    # Parse header table (listing of all selected files before compression starts)
    header_files = {}
    in_table = False
    for line in lines:
        if re.match(r"\s*#\s+Size\s+Type", line):
            in_table = True
            continue
        if in_table:
            if re.match(r"[─\-─]+", line.strip()):
                continue
            m = re.match(r"\s*(\d+)\s+([\d.]+ \w+)\s+(VIDEO|PHOTO)\s+\S+\s+\S+\s+(.+?)\s*$", line)
            if m:
                idx = int(m.group(1))
                header_files[idx] = {
                    "index": idx,
                    "size": m.group(2).strip(),
                    "name": m.group(4).strip(),
                }
            elif line.strip() == "" or "Compressing" in line:
                in_table = False

    # Parse per-file blocks
    current = None
    for line in lines:
        # New file block: "  [N] FILENAME  (SIZE)"
        m = re.match(r"\s+\[(\d+)\] (.+?)\s{2,}\((.+?)\)", line)
        if m:
            if current:
                result["files"].append(current)
            current = {
                "index": int(m.group(1)),
                "name": m.group(2).strip(),
                "orig_size": m.group(3).strip(),
                "phase": "queued",       # queued | downloading | compressing | importing | done | failed
                "dl_pct": 0,
                "dl_took": "",
                "cmp_pct": 0,
                "cmp_took": "",
                "cmp_elapsed": 0,
                "compressed_size": "",
                "saved_pct": "",
                "imported": False,
                "failed": False,
            }
            continue

        if current is None:
            continue

        if re.search(r"(Exporting|Downloading from iCloud)\.\.\.", line):
            current["phase"] = "downloading"

        m = re.search(r"download (\d+)%\s+elapsed (\d+)s", line)
        if m:
            current["dl_pct"] = int(m.group(1))
            current["phase"] = "downloading"

        m = re.search(r"download 100%\s+took (.+)", line)
        if m:
            current["dl_pct"] = 100
            current["dl_took"] = m.group(1).strip()

        if re.search(r"Compressing\.\.\.", line):
            current["phase"] = "compressing"

        m = re.search(r"compress (\d+)%\s+elapsed (\d+)s", line)
        if m:
            current["cmp_pct"] = int(m.group(1))
            current["cmp_elapsed"] = int(m.group(2))
            current["phase"] = "compressing"

        m = re.search(r"compress 100%\s+took (.+)", line)
        if m:
            current["cmp_pct"] = 100
            current["cmp_took"] = m.group(1).strip()

        # "3.65 GB → 752.7 MB  (saved 80%)"
        m = re.search(r"([\d.]+ \w+) → ([\d.]+ \w+)\s+\(saved (\d+)%\)", line)
        if m:
            current["compressed_size"] = m.group(2).strip()
            current["saved_pct"] = m.group(3)

        if re.search(r"Importing\.\.\.", line):
            current["phase"] = "importing"

        if re.search(r"Importing\.\.\. done", line):
            current["phase"] = "done"
            current["imported"] = True

        if re.search(r"export failed|compression failed", line):
            current["phase"] = "failed"
            current["failed"] = True

    if current:
        result["files"].append(current)

    # Summaries
    done = [f for f in result["files"] if f["phase"] == "done"]
    failed = [f for f in result["files"] if f["failed"]]
    in_progress = [f for f in result["files"] if f["phase"] in ("downloading", "compressing", "importing")]

    result["done_count"] = len(done)
    result["failed_count"] = len(failed)
    result["in_progress"] = in_progress[0] if in_progress else None

    # Total space saved
    saved_bytes = 0
    for f in done:
        orig = parse_size(f["orig_size"])
        comp = parse_size(f["compressed_size"])
        if orig and comp:
            saved_bytes += orig - comp
    result["total_saved"] = fmt_bytes(saved_bytes) if saved_bytes else "—"

    # ETA calculation
    # Build a bytes-per-second rate from completed files
    done_with_time = []
    for f in done:
        total_s = parse_time_str(f["dl_took"]) + parse_time_str(f["cmp_took"])
        orig_b = parse_size(f["orig_size"])
        if total_s > 0 and orig_b > 0:
            done_with_time.append((orig_b, total_s))

    eta_seconds = None
    rate_mbps = None

    if done_with_time:
        total_b = sum(b for b, _ in done_with_time)
        total_s = sum(s for _, s in done_with_time)
        rate_bps = total_b / total_s  # bytes per second (average)
        rate_mbps = round(rate_bps / 1024 / 1024, 2)

        remaining = 0

        # Time left in current file
        cur = result["in_progress"]
        if cur:
            if cur["cmp_pct"] > 0 and cur["cmp_elapsed"] > 0:
                # Extrapolate from measured progress
                projected = cur["cmp_elapsed"] / cur["cmp_pct"] * 100
                remaining += max(0, projected - cur["cmp_elapsed"])
            elif rate_bps > 0:
                # File just started or still downloading — estimate full time from size
                remaining += parse_size(cur["orig_size"]) / rate_bps

        # Time for queued files (not yet started) — use header_files for full list
        started_indices = {f["index"] for f in result["files"]}
        if header_files:
            queued_bytes = [parse_size(h["size"]) for idx, h in header_files.items()
                            if idx not in started_indices]
        else:
            queued_bytes = [parse_size(f["orig_size"]) for f in result["files"]
                            if f["phase"] == "queued"]
        if rate_bps > 0:
            remaining += sum(b / rate_bps for b in queued_bytes)

        eta_seconds = int(remaining)

    result["eta_str"] = fmt_duration(eta_seconds) if eta_seconds is not None else "—"
    result["eta_seconds"] = eta_seconds
    result["rate_mbps"] = rate_mbps

    # Build all_files: merge header entries (all 50) with processing entries
    files_by_index = {f["index"]: f for f in result["files"]}
    if header_files:
        all_files = []
        for idx in sorted(header_files):
            if idx in files_by_index:
                all_files.append(files_by_index[idx])
            else:
                h = header_files[idx]
                all_files.append({
                    "index": idx,
                    "name": h["name"],
                    "orig_size": h["size"],
                    "phase": "queued",
                    "dl_pct": 0, "dl_took": "", "cmp_pct": 0, "cmp_took": "",
                    "cmp_elapsed": 0, "compressed_size": "", "saved_pct": "",
                    "imported": False, "failed": False,
                })
        result["all_files"] = all_files
    else:
        result["all_files"] = result["files"]

    return result


def parse_size(s):
    """Parse '3.65 GB' or '752.7 MB' → bytes."""
    if not s:
        return 0
    m = re.match(r"([\d.]+)\s*(GB|MB|KB)", s.strip(), re.I)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * {"GB": 1024**3, "MB": 1024**2, "KB": 1024}[unit])


def parse_time_str(s):
    """Parse '34m18s', '2m10s', '18s' → seconds."""
    if not s:
        return 0
    m = re.match(r"(?:(\d+)h\s*)?(?:(\d+)m\s*)?(\d+)s?", s.strip())
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def fmt_duration(seconds):
    if seconds <= 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"~{h}h {m}m"
    if m > 0:
        return f"~{m}m {s}s"
    return f"~{s}s"


def fmt_bytes(b):
    if b >= 1024**3:
        return f"{b/1024**3:.1f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    return f"{b/1024:.0f} KB"


def is_job_running():
    try:
        result = subprocess.run(
            ["pgrep", "-f", "photos_compress.py"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


# ─── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Photos Compress Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0a0d14; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; padding: 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; color: #f1f5f9; }
  .subtitle { color: #64748b; margin-bottom: 24px; font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .card { background: #131720; border: 1px solid #1e2535; border-radius: 10px; padding: 14px 16px; }
  .card .label { font-size: 10px; color: #475569; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: 700; color: #f1f5f9; }
  .card .sub { font-size: 11px; color: #475569; margin-top: 3px; }
  .card.green .value { color: #4ade80; }
  .card.blue  .value { color: #60a5fa; }
  .card.amber .value { color: #fbbf24; }
  .card.red   .value { color: #f87171; }
  .card.pink  .value { color: #e879f9; }

  .section-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: #334155; margin-bottom: 10px; }

  .overall-bar-wrap { background: #131720; border: 1px solid #1e2535; border-radius: 10px; padding: 14px 16px; margin-bottom: 20px; }
  .overall-bar-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  .overall-bar-header .pct { font-size: 18px; font-weight: 700; color: #60a5fa; }
  .bar-track { background: #1e2535; border-radius: 9999px; height: 8px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 9999px; transition: width .8s ease; }
  .bar-fill.blue  { background: linear-gradient(90deg, #2563eb, #60a5fa); }
  .bar-fill.green { background: linear-gradient(90deg, #16a34a, #4ade80); }

  /* Viz canvas wrapper */
  .viz-wrap { background: #0d1018; border: 1px solid #1e2535; border-radius: 12px; margin-bottom: 20px; overflow: hidden; }
  canvas { display: block; width: 100%; height: 260px; }

  /* File info strip below canvas */
  .viz-info { display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-top: 1px solid #1a2030; }
  .phase-badge { display: inline-block; padding: 2px 9px; border-radius: 9999px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }
  .phase-downloading { background: #0c2044; color: #60a5fa; border: 1px solid #1e3a6e; }
  .phase-compressing { background: #1e0f3d; color: #c084fc; border: 1px solid #3b1f6e; }
  .phase-importing   { background: #0a2418; color: #4ade80; border: 1px solid #14532d; }
  .phase-done        { background: #0a2418; color: #4ade80; border: 1px solid #14532d; }
  .phase-queued      { background: #131720; color: #475569; border: 1px solid #1e2535; }
  .phase-failed      { background: #2d0a0a; color: #f87171; border: 1px solid #450a0a; }

  /* Done table */
  table { width: 100%; border-collapse: collapse; }
  th { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; color: #334155; padding: 8px 12px; text-align: left; border-bottom: 1px solid #1e2535; }
  td { padding: 9px 12px; border-bottom: 1px solid #131720; font-size: 13px; color: #94a3b8; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #131720; }
  .savings { color: #4ade80; font-weight: 600; }
  .fail-txt { color: #f87171; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 9999px; font-size: 10px; font-weight: 700; }
  .pill-done { background: #0a2418; color: #4ade80; border: 1px solid #14532d; }
  .pill-fail { background: #2d0a0a; color: #f87171; border: 1px solid #450a0a; }

  .pulse::after { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #4ade80; margin-left: 7px; vertical-align: middle; animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.3;transform:scale(.7)} }
  .stopped { color: #f87171; }
  footer { margin-top: 20px; font-size: 11px; color: #1e2535; }
</style>
</head>
<body>
<h1>Photos Compression Monitor</h1>
<div class="subtitle" id="status-line">Loading…</div>

<div class="cards">
  <div class="card blue">
    <div class="label">Files Done</div>
    <div class="value" id="done-count">—</div>
    <div class="sub" id="done-of">of — total</div>
  </div>
  <div class="card green">
    <div class="label">Space Saved</div>
    <div class="value" id="total-saved">—</div>
    <div class="sub">compressed so far</div>
  </div>
  <div class="card amber">
    <div class="label">Current File</div>
    <div class="value" id="current-file-short" style="font-size:15px;padding-top:3px">—</div>
    <div class="sub" id="current-phase-short">—</div>
  </div>
  <div class="card pink">
    <div class="label">Est. Remaining</div>
    <div class="value" id="eta-value">—</div>
    <div class="sub" id="eta-rate">calculating…</div>
  </div>
  <div class="card red" id="failed-card" style="display:none">
    <div class="label">Failed</div>
    <div class="value" id="failed-count">0</div>
    <div class="sub">check log</div>
  </div>
</div>

<div class="overall-bar-wrap">
  <div class="overall-bar-header">
    <span style="font-size:12px;color:#475569;font-weight:600;letter-spacing:.05em;text-transform:uppercase">Overall</span>
    <span class="pct" id="overall-pct">0%</span>
  </div>
  <div class="bar-track"><div class="bar-fill blue" id="overall-bar" style="width:0%"></div></div>
  <div style="font-size:11px;color:#475569;margin-top:6px" id="overall-sub">0 of — files done</div>
</div>

<div id="viz-section" style="display:none">
  <div class="section-title">Current File</div>
  <div class="viz-wrap">
    <canvas id="viz"></canvas>
    <div class="viz-info">
      <span class="phase-badge phase-queued" id="viz-badge">Queued</span>
      <span style="color:#f1f5f9;font-weight:600;font-size:14px" id="viz-name">—</span>
      <span style="color:#334155;font-size:12px" id="viz-meta"></span>
    </div>
  </div>
</div>

<div id="all-files-section" style="display:none">
  <div class="section-title">All Files</div>
  <div style="background:#0d1018;border:1px solid #1e2535;border-radius:10px;overflow:hidden">
    <table>
      <thead><tr><th>#</th><th>Filename</th><th>Original</th><th>Status</th><th>Compressed</th><th>Saved</th></tr></thead>
      <tbody id="all-files-tbody"></tbody>
    </table>
  </div>
</div>

<footer>Refreshing every 1s &nbsp;·&nbsp; ~/Desktop/compress_log.txt</footer>

<script>
// ── Canvas setup ──────────────────────────────────────────────────────────────
const canvas = document.getElementById('viz');
const ctx = canvas.getContext('2d');
const DPR = window.devicePixelRatio || 1;

function resizeCanvas() {
  const W = canvas.parentElement.clientWidth || 800;
  const H = 260;
  if (canvas.width === W * DPR && canvas.height === H * DPR) return;
  canvas.width  = W * DPR;
  canvas.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  particles = null; // recalculate positions for new dimensions
}
// Don't call resizeCanvas() yet — viz-section starts display:none so clientWidth=0
window.addEventListener('resize', resizeCanvas);

// ── Particle class ────────────────────────────────────────────────────────────
class Particle {
  constructor(cx, cy, baseR, col) {
    this.cx = cx; this.cy = cy; this.col = col;
    this.reset(baseR);
  }
  reset(baseR) {
    this.angle  = Math.random() * Math.PI * 2;
    this.r      = baseR + 14 + Math.random() * 28;
    this.speed  = (0.006 + Math.random() * 0.014) * (Math.random() < .5 ? 1 : -1);
    this.size   = 1.2 + Math.random() * 2.2;
    this.alpha  = 0.25 + Math.random() * 0.65;
    this.phase  = Math.random() * Math.PI * 2;
    this.pspeed = 0.018 + Math.random() * 0.025;
  }
  update(speedMult) {
    this.angle += this.speed * speedMult;
    this.phase += this.pspeed;
  }
  draw(opacity) {
    const x = this.cx + Math.cos(this.angle) * this.r;
    const y = this.cy + Math.sin(this.angle) * this.r;
    const pulse = 0.5 + 0.5 * Math.sin(this.phase);
    ctx.beginPath();
    ctx.arc(x, y, this.size * (0.6 + pulse * 0.7), 0, Math.PI * 2);
    ctx.fillStyle = this.col.replace('A', '' + (this.alpha * pulse * opacity).toFixed(2));
    ctx.fill();
  }
}

// ── Flow particles ────────────────────────────────────────────────────────────
class FlowDot {
  constructor(x1, x2, cy) {
    this.x1 = x1; this.x2 = x2; this.cy = cy;
    this.reset();
    this.x = x1 + Math.random() * (x2 - x1); // spread out at start
  }
  reset() {
    this.x = this.x1;
    this.y = this.cy + (Math.random() - 0.5) * 36;
    this.spd = 1.4 + Math.random() * 2.8;
    this.sz  = 1 + Math.random() * 2;
    this.a   = 0.4 + Math.random() * 0.5;
    this.hue = Math.random() < 0.5 ? '96,165,250' : '168,85,247';
  }
  update() { this.x += this.spd; if (this.x > this.x2) this.reset(); }
  draw() {
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.sz, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(' + this.hue + ',' + this.a + ')';
    ctx.fill();
  }
}

// ── Layout constants (recalc on draw so they respond to resize) ───────────────
function layout() {
  const W = canvas.width / DPR;
  const H = canvas.height / DPR;
  const cy = H / 2 - 8;
  const R  = Math.min(72, H * 0.28);
  const lx = W * 0.27;
  const rx = W * 0.73;
  return { W, H, cy, R, lx, rx };
}

let particles = null;
function ensureParticles() {
  const { cy, R, lx, rx } = layout();
  if (!particles) {
    particles = {
      dl:   Array.from({length: 28}, () => new Particle(lx, cy, R, 'rgba(96,165,250,A)')),
      cmp:  Array.from({length: 28}, () => new Particle(rx, cy, R, 'rgba(168,85,247,A)')),
      flow: Array.from({length: 32}, () => new FlowDot(lx + R + 18, rx - R - 18, cy)),
    };
  }
}

// ── Draw a glowing arc ring ───────────────────────────────────────────────────
function drawRing(cx, cy, R, pct, strokeCol, glowCol, label, meta) {
  const start = -Math.PI / 2;
  const sweep = (pct / 100) * Math.PI * 2;

  // Track
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 9;
  ctx.stroke();

  if (pct > 0.3) {
    // Soft outer glow
    ctx.save();
    ctx.shadowBlur = 28;
    ctx.shadowColor = glowCol;
    ctx.beginPath();
    ctx.arc(cx, cy, R, start, start + sweep);
    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = 9;
    ctx.lineCap = 'round';
    ctx.stroke();
    ctx.restore();

    // Bright leading-edge dot
    const ex = cx + Math.cos(start + sweep) * R;
    const ey = cy + Math.sin(start + sweep) * R;
    ctx.save();
    ctx.shadowBlur = 18;
    ctx.shadowColor = '#ffffff';
    ctx.beginPath();
    ctx.arc(ex, ey, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.restore();
  }

  // Centre text
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = pct > 0 ? '#f1f5f9' : '#334155';
  ctx.font = 'bold 26px -apple-system,BlinkMacSystemFont,sans-serif';
  ctx.fillText(Math.round(pct) + '%', cx, cy - 7);

  ctx.fillStyle = '#475569';
  ctx.font = '11px -apple-system,BlinkMacSystemFont,sans-serif';
  ctx.fillText(label, cx, cy + 13);

  if (meta) {
    ctx.fillStyle = '#334155';
    const { H, R: Rv } = layout();
    ctx.fillText(meta, cx, cy + Rv + 20);
  }
}

// ── Duration formatter (mirrors Python fmt_duration) ─────────────────────────
function fmtDur(s) {
  s = Math.round(s);
  if (s <= 0) return '—';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return '~' + h + 'h ' + m + 'm';
  if (m > 0) return '~' + m + 'm ' + sec + 's';
  return '~' + sec + 's';
}

// ── Extrapolating progress tracker ───────────────────────────────────────────
// The log only updates at 1% milestones — we extrapolate forward between them
// so the ring appears to move continuously rather than jump every few minutes.
function makeTrack() {
  return { pct: 0, prevPct: 0, t: 0, prevT: 0, rate: 0 }; // rate = % per ms
}
function updateTrack(tr, newPct) {
  if (newPct === tr.pct) return;
  tr.prevPct = tr.pct; tr.prevT = tr.t;
  tr.pct = newPct;     tr.t    = Date.now();
  if (tr.prevT > 0 && tr.pct > tr.prevPct) {
    tr.rate = (tr.pct - tr.prevPct) / (tr.t - tr.prevT); // % per ms
  }
}
function extrap(tr) {
  if (tr.pct >= 100 || tr.rate <= 0 || tr.t === 0) return tr.pct;
  // Extrapolate but cap at +9% past last known (one milestone ahead max)
  return Math.min(tr.pct + tr.rate * (Date.now() - tr.t), tr.pct + 9);
}

const dlTrack  = makeTrack();
const cmpTrack = makeTrack();

// ── Animated state ────────────────────────────────────────────────────────────
let aDl = 0, aCmp = 0;
let curPhase = 'queued', curName = '', curOrig = '';
let dlTook = '', cmpTook = '', cmpElapsed = 0;

let serverEtaSec = null, etaFetchedAt = 0;

let frame = 0;
function animate() {
  requestAnimationFrame(animate);
  frame++;
  ensureParticles();

  const { W, H, cy, R, lx, rx } = layout();

  // Lerp toward extrapolated targets (smooth 60fps motion)
  const tDl  = extrap(dlTrack);
  const tCmp = extrap(cmpTrack);

  // Tick ETA countdown every frame (smooth between server refreshes)
  if (serverEtaSec !== null && etaFetchedAt > 0) {
    const remaining = Math.max(0, serverEtaSec - (Date.now() - etaFetchedAt) / 1000);
    document.getElementById('eta-value').textContent = fmtDur(remaining);
  }
  aDl  += (tDl  - aDl)  * 0.07;
  aCmp += (tCmp - aCmp) * 0.07;

  ctx.clearRect(0, 0, W, H);

  // Subtle dot-grid background
  ctx.fillStyle = 'rgba(255,255,255,0.018)';
  for (let x = 20; x < W; x += 38)
    for (let y = 14; y < H - 14; y += 38)
      { ctx.beginPath(); ctx.arc(x, y, 1, 0, Math.PI * 2); ctx.fill(); }

  const dlActive  = curPhase === 'downloading' || aDl > 0;
  const cmpActive = curPhase === 'compressing' || aCmp > 0;

  // Flow dots (dl done, compress running)
  if (aDl >= 99 && cmpActive) {
    particles.flow.forEach(p => { p.update(); p.draw(); });
  }

  // Arrow / connector line
  ctx.beginPath();
  ctx.moveTo(lx + R + 14, cy);
  ctx.lineTo(rx - R - 14, cy);
  ctx.strokeStyle = 'rgba(255,255,255,' + (cmpActive ? '0.06' : '0.025') + ')';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 8]);
  ctx.stroke();
  ctx.setLineDash([]);

  // Orbiting particles
  const dlMult  = dlActive  && aDl  < 99.5 ? 1.8 : 0.25;
  const cmpMult = cmpActive && aCmp < 99.5 ? 1.8 : 0.25;
  particles.dl.forEach(p  => { p.cx = lx; p.cy = cy; p.update(dlMult);  p.draw(dlActive  ? 0.9 : 0.15); });
  particles.cmp.forEach(p => { p.cx = rx; p.cy = cy; p.update(cmpMult); p.draw(cmpActive ? 0.9 : 0.15); });

  // Build meta strings
  const dlMeta  = dlTook   ? 'took ' + dlTook
                : tDl > 0  ? tDl + '%' : '';
  const cmpMeta = cmpTook  ? 'took ' + cmpTook
                : cmpElapsed > 0
                  ? Math.floor(cmpElapsed/60) + 'm' + String(cmpElapsed%60).padStart(2,'0') + 's elapsed'
                : tCmp > 0 ? tCmp + '%' : '';

  // Rings
  drawRing(lx, cy, R, aDl,  '#60a5fa', '#3b82f6', 'Download / Export', dlMeta);
  drawRing(rx, cy, R, aCmp, '#c084fc', '#9333ea', 'Compress',           cmpMeta);

  // Centre label
  if (curName) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#94a3b8';
    ctx.font = '12px -apple-system,BlinkMacSystemFont,sans-serif';
    ctx.fillText(curName, W / 2, cy - 8);
    ctx.fillStyle = '#334155';
    ctx.font = '11px -apple-system,BlinkMacSystemFont,sans-serif';
    ctx.fillText(curOrig, W / 2, cy + 8);
  }
}
animate();

// ── Data fetch ────────────────────────────────────────────────────────────────
const phaseLabel = { downloading:'Downloading', compressing:'Compressing',
                     importing:'Importing', done:'Done', queued:'Queued', failed:'Failed' };

async function fetchData() {
  let data;
  try { const r = await fetch('/status'); data = await r.json(); }
  catch(e) { document.getElementById('status-line').textContent = 'Server unreachable'; return; }

  if (data.error) { document.getElementById('status-line').textContent = data.error; return; }

  const total = data.total_files || 50;
  const done  = data.done_count  || 0;
  const pct   = total > 0 ? Math.round(done / total * 100) : 0;

  // Status line
  const runEl = document.getElementById('status-line');
  if      (data.job_running)           runEl.innerHTML = '<span class="pulse">Job running</span>';
  else if (done === total && total > 0) runEl.textContent = 'Job complete ✓';
  else                                 runEl.innerHTML = '<span class="stopped">Job not running</span>';

  // Stat cards
  document.getElementById('done-count').textContent  = done;
  document.getElementById('done-of').textContent     = 'of ' + total + ' total';
  document.getElementById('total-saved').textContent = data.total_saved || '—';
  document.getElementById('overall-pct').textContent = pct + '%';
  document.getElementById('overall-bar').style.width = pct + '%';
  document.getElementById('overall-sub').textContent = done + ' of ' + total + ' files done';
  if (pct === 100) document.getElementById('overall-bar').className = 'bar-fill green';

  if (data.eta_seconds !== null && data.eta_seconds !== undefined) {
    serverEtaSec  = data.eta_seconds;
    etaFetchedAt  = Date.now();
  }
  document.getElementById('eta-rate').textContent  = data.rate_mbps
    ? data.rate_mbps + ' MB/s avg' : 'waiting for first file…';

  const failed = data.failed_count || 0;
  if (failed > 0) {
    document.getElementById('failed-card').style.display = '';
    document.getElementById('failed-count').textContent = failed;
  }

  // Current file → update canvas state
  const cur = data.in_progress;
  if (cur) {
    const vizEl = document.getElementById('viz-section');
    const wasHidden = vizEl.style.display === 'none';
    vizEl.style.display = '';
    // Defer resize one rAF so the browser has reflowed and clientWidth is valid
    if (wasHidden) requestAnimationFrame(resizeCanvas);
    updateTrack(dlTrack,  cur.dl_pct  || 0);
    updateTrack(cmpTrack, cur.cmp_pct || 0);
    curPhase   = cur.phase;
    curName    = cur.name;
    curOrig    = cur.orig_size;
    dlTook     = cur.dl_took    || '';
    cmpTook    = cur.cmp_took   || '';
    cmpElapsed = cur.cmp_elapsed || 0;

    document.getElementById('viz-badge').textContent  = phaseLabel[cur.phase] || cur.phase;
    document.getElementById('viz-badge').className    = 'phase-badge phase-' + cur.phase;
    document.getElementById('viz-name').textContent   = cur.name;
    document.getElementById('viz-meta').textContent   = 'File ' + cur.index + ' of ' + total + '  ·  ' + cur.orig_size;

    document.getElementById('current-file-short').textContent  = cur.name.replace(/[.][^.]+$/, '');
    document.getElementById('current-phase-short').textContent = phaseLabel[cur.phase] || cur.phase;
  } else {
    Object.assign(dlTrack,  makeTrack());
    Object.assign(cmpTrack, makeTrack());
    aDl = aCmp = 0;
    curName = curOrig = curPhase = '';
    document.getElementById('viz-section').style.display = 'none';
    document.getElementById('current-file-short').textContent  = done === total ? 'All done' : '—';
    document.getElementById('current-phase-short').textContent = '';
  }

  // All-files table
  const allFiles = data.all_files || data.files;
  if (allFiles && allFiles.length > 0) {
    document.getElementById('all-files-section').style.display = '';
    const curIdx = data.in_progress ? data.in_progress.index : -1;
    document.getElementById('all-files-tbody').innerHTML = allFiles.map(f => {
      const isActive  = f.index === curIdx;
      const isDone    = f.phase === 'done';
      const isFailed  = f.failed;
      const isQueued  = f.phase === 'queued';

      let rowStyle = isQueued ? 'opacity:.35' : '';
      let nameCol  = isActive ? '<td style="color:#f1f5f9;font-weight:600">' + f.name + '</td>'
                   : isDone   ? '<td style="color:#cbd5e1">' + f.name + '</td>'
                   :            '<td style="color:#475569">' + f.name + '</td>';

      let statusCol;
      if (isFailed)       statusCol = '<td><span class="pill pill-fail">failed</span></td>';
      else if (isDone)    statusCol = '<td><span class="pill pill-done">done</span></td>';
      else if (isActive) {
        const ph = f.phase === 'compressing' ? 'Compressing ' + f.cmp_pct + '%'
                 : f.phase === 'downloading'  ? 'Downloading ' + f.dl_pct + '%'
                 : f.phase === 'importing'    ? 'Importing…'
                 : f.phase;
        statusCol = '<td><span class="phase-badge phase-' + f.phase + '">' + ph + '</span></td>';
      } else              statusCol = '<td style="color:#334155">queued</td>';

      let compCol = isDone  ? '<td style="color:#94a3b8">' + f.compressed_size + '</td>'
                  :           '<td style="color:#334155">—</td>';
      let savedCol = isFailed  ? '<td><span class="fail-txt">failed</span></td>'
                   : isDone    ? '<td><span class="savings">-' + f.saved_pct + '%</span></td>'
                   :             '<td style="color:#334155">—</td>';

      return '<tr style="' + rowStyle + '">' +
        '<td style="color:#334155">' + f.index + '</td>' +
        nameCol + '<td>' + f.orig_size + '</td>' +
        statusCol + compCol + savedCol + '</tr>';
    }).join('');
  }
}

fetchData();
setInterval(fetchData, 1000);
</script>
</body>
</html>"""


# ─── Server ────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            data = parse_log()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/" or self.path == "/index.html":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Monitor running at {url}")
    print("Press Ctrl+C to stop.")
    subprocess.Popen(["open", url])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
