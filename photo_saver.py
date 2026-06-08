#!/usr/bin/env python3
"""
iCloud Photo Storage Saver — reclaim space in your Apple Photos / iCloud library.

A local web app (served at http://localhost:8421) with three tools:
  • Duplicates   — perceptual-hash dedup of near-identical photos/videos
  • Large Files  — browse by size; delete or compress big videos to H.265
  • Activity     — audit log + metrics of what you've reclaimed over time

Runs entirely on your Mac. No photo ever leaves the machine.

Run with:
  osxphotos run photo_saver.py
  osxphotos run photo_saver.py --rescan        # ignore hash cache
  osxphotos run photo_saver.py --threshold 8   # stricter dup matching (default 10)
  osxphotos run photo_saver.py --window 120    # time window secs (default 60)
  osxphotos run photo_saver.py --no-open       # don't auto-open the browser

Then open: http://localhost:8421
GitHub: https://github.com/joshtellr/icloud-photo-storage-saver  (GPL-3.0)
"""

import argparse
import gzip
import io
import json
import os
import sys
import tempfile
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CACHE_FILE     = Path.home() / ".photos_dedup_cache.json"
DECISIONS_FILE = Path.home() / ".photos_dedup_decisions.json"
DECISIONS_LOCK = threading.Lock()   # guards concurrent read/write of the decisions file
PORT = 8421
MAX_POST_BYTES = 2 * 1024 * 1024    # reject POST bodies larger than this (DoS guard)

# Optional access token. If PHOTO_SAVER_TOKEN is set, every request must carry it
# (via ?token=, an X-Auth-Token header, or the ps_token cookie). Off by default —
# safe for localhost; strongly recommended when exposing beyond your own machine.
AUTH_TOKEN = os.environ.get("PHOTO_SAVER_TOKEN", "").strip()

AUTH_PAGE = (
    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<body style='background:#0a0d14;color:#e2e8f0;font-family:-apple-system,sans-serif;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center'>"
    "<div><div style='font-size:40px'>🔒</div><h2>Token required</h2>"
    "<p style='color:#94a3b8'>Open this page with <code>?token=YOUR_TOKEN</code> in the URL.</p></div>"
)


def read_decisions():
    """Read the saved decisions dict; thread-safe, tolerant of a missing/corrupt file."""
    with DECISIONS_LOCK:
        if not DECISIONS_FILE.exists():
            return {}
        try:
            return json.loads(DECISIONS_FILE.read_text())
        except Exception:
            return {}


def write_decisions(data):
    """Atomically write the decisions dict; thread-safe."""
    with DECISIONS_LOCK:
        tmp = DECISIONS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, DECISIONS_FILE)


def clear_decisions(uuids):
    """Drop the given UUIDs from the saved decisions (e.g. after they're trashed),
    so a page refresh can't resurrect a mark for a photo that's already gone."""
    uuids = set(uuids)
    if not uuids:
        return
    d = read_decisions()
    pruned = {u: v for u, v in d.items() if u not in uuids}
    if len(pruned) != len(d):
        write_decisions(pruned)


def prune_decisions(valid_uuids):
    """Keep only decisions whose UUID is still in the library. Removes stale marks
    for photos that have since been trashed/emptied (heals an out-of-date file)."""
    valid_uuids = set(valid_uuids)
    d = read_decisions()
    pruned = {u: v for u, v in d.items() if u in valid_uuids}
    if len(pruned) != len(d):
        write_decisions(pruned)


# ─── Hashing ──────────────────────────────────────────────────────────────────

def dhash(img, size=8):
    """64-bit difference hash. Pure PIL — no numpy."""
    img = img.convert("L").resize((size + 1, size))
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            idx = row * (size + 1) + col
            bits = (bits << 1) | (1 if px[idx] > px[idx + 1] else 0)
    return f"{bits:016x}"


def hamming(h1, h2):
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


# ─── Cache ────────────────────────────────────────────────────────────────────

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache))


# ─── Hashing all photos ───────────────────────────────────────────────────────

def hash_all_photos(photos, cache, rescan=False, progress_cb=None):
    """
    Hash every photo using its local derivative thumbnail.
    Returns {uuid: hex_hash}. Skips photos with no derivative.
    progress_cb(fraction) is called periodically with 0..1 completion.
    """
    from PIL import Image

    hashes = {}
    total = len(photos)
    new_count = 0
    start = time.time()

    for i, photo in enumerate(photos):
        uuid = photo.uuid

        if i % 500 == 0 or i == total - 1:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 1
            eta_s = int((total - i - 1) / rate)
            m, s = divmod(eta_s, 60)
            eta = f"ETA {m}m{s:02d}s" if m else f"ETA {s}s"
            print(f"\r  {i+1:,}/{total:,}  ({new_count} new)  {eta}   ", end="", flush=True)
            if progress_cb:
                progress_cb((i + 1) / total)

        # Use cache if present
        if not rescan and uuid in cache:
            hashes[uuid] = cache[uuid]
            continue

        derivs = photo.path_derivatives
        if not derivs:
            continue
        path = derivs[0]
        if not Path(path).exists():
            continue

        try:
            with Image.open(path) as img:
                h = dhash(img)
            hashes[uuid] = h
            cache[uuid] = h
            new_count += 1
        except Exception:
            pass

    print(f"\r  {total:,} photos processed  ({new_count} newly hashed)              ")
    return hashes


# ─── Clustering ───────────────────────────────────────────────────────────────

def find_clusters(photos, hashes, threshold, time_window_sec):
    """
    Groups photos into duplicate clusters via union-find.
    Two passes:
      1. Exact hash matches (any time — catches re-downloads / copies)
      2. Near-matches within time_window_sec of each other
    """
    uuids_with_hash = set(hashes)
    parent = {u: u for u in uuids_with_hash}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 1. Exact matches
    by_hash = {}
    for uuid, h in hashes.items():
        by_hash.setdefault(h, []).append(uuid)
    for group in by_hash.values():
        for i in range(1, len(group)):
            union(group[0], group[i])

    # 2. Near-matches within time window (sliding window, O(n·w))
    dated = []
    for photo in photos:
        if photo.uuid not in uuids_with_hash:
            continue
        try:
            t = photo.date
            if t:
                dated.append((t, photo.uuid, hashes[photo.uuid]))
        except Exception:
            pass
    dated.sort(key=lambda x: x[0])

    left = 0
    for right in range(len(dated)):
        tr, ur, hr = dated[right]
        while (tr - dated[left][0]).total_seconds() > time_window_sec:
            left += 1
        for k in range(left, right):
            tk, uk, hk = dated[k]
            if hamming(hr, hk) <= threshold:
                union(ur, uk)

    # Collect clusters with 2+ members
    groups = {}
    for uuid in uuids_with_hash:
        root = find(uuid)
        groups.setdefault(root, []).append(uuid)

    return {r: m for r, m in groups.items() if len(m) >= 2}


# ─── Scoring ──────────────────────────────────────────────────────────────────

def score_photo(photo):
    """Higher = better to keep by default."""
    score = photo.original_filesize or 0
    if photo.favorite:
        score += 10 ** 12
    if photo.path and Path(photo.path).exists():
        score += 10 ** 9  # prefer local original over iCloud-only
    return score


def fmt_bytes(b):
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024:.0f} KB"


def cluster_tightness(uuids, hashes):
    """Max pairwise hamming distance among a cluster's hashes — how 'loose' it is.
    0 = identical; small = near-identical dupes; large = a whole photo session
    chained together by transitive similarity. None if hashes unavailable."""
    hs = [hashes[u] for u in uuids if u in hashes]
    if len(hs) < 2:
        return None
    worst = 0
    # Clusters are small; full pairwise is cheap. Cap work on rare huge ones.
    if len(hs) > 60:
        hs = hs[:60]
    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            d = hamming(hs[i], hs[j])
            if d > worst:
                worst = d
    return worst


def tightness_label(t):
    if t is None:   return "similar"
    if t == 0:      return "identical"
    if t <= 4:      return "near-identical"
    if t <= 8:      return "similar"
    return "loose"


def build_cluster_list(photos_by_uuid, clusters, hashes=None):
    hashes = hashes or {}
    out = []
    for root, uuids in clusters.items():
        photos = [photos_by_uuid[u] for u in uuids if u in photos_by_uuid]
        if len(photos) < 2:
            continue
        photos.sort(key=score_photo, reverse=True)
        tight = cluster_tightness(uuids, hashes)
        removable = sum(p.original_filesize or 0 for p in photos[1:])  # if all-but-best removed
        out.append({
            "id": root,
            "tightness": tight if tight is not None else 99,
            "match": tightness_label(tight),
            "removable_bytes": removable,
            "photos": [{
                "uuid": p.uuid,
                "filename": p.original_filename,
                "size": p.original_filesize or 0,
                "size_str": fmt_bytes(p.original_filesize or 0),
                "date": str(p.date)[:10] if p.date else "",
                "is_video": bool(p.ismovie),
                "in_cloud": not (p.path and Path(p.path).exists()),
                "favorite": bool(p.favorite),
                "has_deriv": bool(p.path_derivatives and Path(p.path_derivatives[0]).exists()),
                "source": photo_source(p.uuid),  # library | shared | hidden | missing
            } for p in photos],
            "keep_uuid": photos[0].uuid,
        })
    # Default order: tightest (most confident dupes) first, then larger groups.
    out.sort(key=lambda c: (c["tightness"], -len(c["photos"])))
    return out


FILES_LIMIT = 5000   # show the N largest files (where essentially all the space is)


def build_files_list(photos):
    """Flat list of the largest items for the Large Files browser, sorted by size."""
    ordered = sorted(photos, key=lambda p: p.original_filesize or 0, reverse=True)[:FILES_LIMIT]
    out = []
    for p in ordered:
        ext = (p.original_filename.rsplit(".", 1)[-1].lower() if "." in p.original_filename else "")
        out.append({
            "uuid": p.uuid,
            "filename": p.original_filename,
            "size": p.original_filesize or 0,
            "size_str": fmt_bytes(p.original_filesize or 0),
            "date": str(p.date)[:10] if p.date else "",
            "is_video": bool(p.ismovie),
            "ext": ext,
            "is_raw": ext in ("dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "raw", "tif", "tiff"),
            "has_deriv": bool(p.path_derivatives and Path(p.path_derivatives[0]).exists()),
            "source": photo_source(p.uuid),
        })
    return out


# ─── Trash via PhotoKit ───────────────────────────────────────────────────────

def trash_photos(uuids):
    if not uuids:
        return {"trashed": 0, "failed": 0, "trashed_uuids": []}
    try:
        import Photos as PHPhotos
    except ImportError:
        return {"error": "PhotoKit not available — run via: osxphotos run ~/photos_dedup.py"}

    # Only delete photos in YOUR library. Shared-album / "shared with you" /
    # orphan items aren't yours to delete, and including one in the batch would
    # fail the whole performChangesAndWait (all-or-nothing). Skip them cleanly.
    #   gone = a library photo with no live asset → already in Recently Deleted
    #          (e.g. trashed in an earlier action). Treat as already-done and clean
    #          up its stale state instead of reporting a confusing failure.
    assets, found_uuids, gone, skipped = [], [], [], []
    for uuid in uuids:
        if LIBRARY_UUIDS is not None and uuid not in LIBRARY_UUIDS:
            skipped.append(uuid)
            continue
        result = PHPhotos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([uuid], None)
        asset = result.firstObject()
        if asset:
            assets.append(asset)
            found_uuids.append(uuid)
        else:
            gone.append(uuid)

    if not assets:
        # Nothing live to delete — but heal any stale "already gone" library photos.
        _finalize_trash(gone)
        return {"trashed": 0, "already_gone": len(gone),
                "failed": len(skipped), "trashed_uuids": gone}

    # ONE change request for the whole batch → a single macOS confirmation
    # dialog, regardless of how many photos are selected.
    def do_delete():
        PHPhotos.PHAssetChangeRequest.deleteAssets_(assets)

    ok, _ = PHPhotos.PHPhotoLibrary.sharedPhotoLibrary().performChangesAndWait_error_(
        do_delete, None
    )

    if ok:
        kept = [PHOTOS_BY_UUID[u] for u in found_uuids if u in PHOTOS_BY_UUID]
        bytes_freed = sum(p.original_filesize or 0 for p in kept)
        n_vid = sum(1 for p in kept if p.ismovie)
        names = [p.original_filename for p in kept[:50]]
        audit_log({"type": "trash", "count": len(found_uuids), "bytes": bytes_freed,
                   "videos": n_vid, "photos": len(kept) - n_vid, "items": names})
        cleared = found_uuids + gone
        _finalize_trash(cleared)
        return {"trashed": len(found_uuids), "already_gone": len(gone),
                "failed": len(skipped), "trashed_uuids": cleared}
    # User clicked "Don't Allow" or it failed — nothing deleted. Still clean up
    # the genuinely-gone ones so they stop reappearing.
    _finalize_trash(gone)
    return {"trashed": 0, "already_gone": len(gone),
            "failed": len(found_uuids) + len(skipped), "trashed_uuids": gone}


def _finalize_trash(uuids):
    """Persist the consequences of a trash: forget the marks and drop the photos
    from the live payloads so a refresh stays in sync with what's actually gone."""
    if not uuids:
        return
    clear_decisions(uuids)
    prune_payloads(uuids)


# ─── Compress (video → H.265, re-import, trash original) ──────────────────────

# The self-contained .app sets PS_OSXPHOTOS_BIN to its bundled osxphotos wrapper.
# Falls back to the user-level install for source/dev runs (`osxphotos run …`).
OSXPHOTOS_BIN = Path(os.environ.get(
    "PS_OSXPHOTOS_BIN", Path.home() / ".local" / "bin" / "osxphotos"))

COMPRESS_STATE = {
    "running": False, "total": 0, "done": 0, "current": "", "current_uuid": "",
    "current_pct": 0, "saved_bytes": 0, "errors": [], "trashed_uuids": [], "finished": False,
}
COMPRESS_LOCK = threading.Lock()


def cset(**kw):
    with COMPRESS_LOCK:
        COMPRESS_STATE.update(kw)


def _video_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def _compress_one(uuid, photo):
    """Export → H.265 → copy metadata → import → trash original. Returns saved bytes or raises."""
    import Photos as PHPhotos
    import tempfile
    orig_size = photo.original_filesize or 0

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        # 1. Export original (download from iCloud if needed)
        subprocess.run([str(OSXPHOTOS_BIN), "export", "--uuid", uuid, "--download-missing",
                        "--skip-edited", str(tmpd)],
                       capture_output=True, text=True)
        exported = [f for f in tmpd.iterdir()
                    if not f.name.startswith(".") and f.suffix.lower() != ".db"]
        if not exported:
            raise RuntimeError("export failed")
        src = exported[0]

        # 2. Re-encode to H.265 / HEVC
        dst = tmpd / "compressed.mp4"
        duration = _video_duration(src)
        proc = subprocess.Popen(
            ["ffmpeg", "-i", str(src), "-c:v", "libx265", "-crf", "28", "-preset", "medium",
             "-c:a", "aac", "-b:a", "128k", "-tag:v", "hvc1", "-map_metadata", "0",
             "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-y", str(dst)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    pct = min(int(line.split("=")[1]) / (duration * 1_000_000) * 100, 100)
                    cset(current_pct=round(pct))
                except (ValueError, IndexError):
                    pass
        proc.wait()
        if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            raise RuntimeError("ffmpeg failed")

        new_size = dst.stat().st_size
        if new_size >= orig_size:
            raise RuntimeError("compressed not smaller — skipped")

        # 3. Copy metadata (dates, GPS, etc.) from original
        subprocess.run(["exiftool", "-TagsFromFile", str(src), "-all:all",
                        "-overwrite_original", "-q", str(dst)], capture_output=True)

        # 4. Import compressed copy into Photos
        imp = subprocess.run(["osascript", "-e",
                              f'tell application "Photos" to import POSIX file "{dst}"'],
                             capture_output=True, text=True)
        if imp.returncode != 0:
            raise RuntimeError("import failed: " + imp.stderr.strip()[:60])

        # 5. Trash the original (single-asset change request)
        res = PHPhotos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([uuid], None)
        asset = res.firstObject()
        if asset:
            def _del():
                PHPhotos.PHAssetChangeRequest.deleteAssets_([asset])
            PHPhotos.PHPhotoLibrary.sharedPhotoLibrary().performChangesAndWait_error_(_del, None)

        return orig_size - new_size


def compress_worker(uuids):
    cset(running=True, finished=False, total=len(uuids), done=0,
         saved_bytes=0, errors=[], trashed_uuids=[], current="", current_uuid="", current_pct=0)
    for uuid in uuids:
        photo = PHOTOS_BY_UUID.get(uuid)
        if not photo:
            with COMPRESS_LOCK:
                COMPRESS_STATE["errors"].append(f"{uuid[:8]}: not found")
                COMPRESS_STATE["done"] += 1
            continue
        cset(current=photo.original_filename, current_uuid=uuid, current_pct=0)
        try:
            saved = _compress_one(uuid, photo)
            with COMPRESS_LOCK:
                COMPRESS_STATE["saved_bytes"] += max(0, saved)
                COMPRESS_STATE["trashed_uuids"].append(uuid)
                COMPRESS_STATE["done"] += 1
            orig = photo.original_filesize or 0
            audit_log({"type": "compress", "filename": photo.original_filename,
                       "orig_bytes": orig, "new_bytes": max(0, orig - saved),
                       "saved_bytes": max(0, saved)})
            print(f"  compressed {photo.original_filename}: saved {fmt_bytes(saved)}")
        except Exception as e:
            with COMPRESS_LOCK:
                COMPRESS_STATE["errors"].append(f"{photo.original_filename}: {e}")
                COMPRESS_STATE["done"] += 1
            print(f"  compress FAILED {photo.original_filename}: {e}")
    cset(running=False, finished=True, current="", current_uuid="")


def start_compress(uuids):
    if COMPRESS_STATE["running"]:
        return {"error": "A compression job is already running."}
    # Only videos in your own library can be compressed.
    vids = [u for u in uuids
            if (p := PHOTOS_BY_UUID.get(u)) and p.ismovie
            and (LIBRARY_UUIDS is None or u in LIBRARY_UUIDS)]
    if not vids:
        return {"error": "No compressible videos selected (videos in your library only)."}
    threading.Thread(target=compress_worker, args=(vids,), daemon=True, name="compress").start()
    return {"ok": True, "count": len(vids)}


# ─── Audit log + metrics ──────────────────────────────────────────────────────

AUDIT_FILE = Path.home() / ".photo_saver_audit.jsonl"
AUDIT_LOCK = threading.Lock()


def audit_log(event):
    """Append one event to the persistent audit log (JSON lines)."""
    rec = {"ts": time.time(), "iso": datetime.now().isoformat(timespec="seconds"), **event}
    try:
        with AUDIT_LOCK:
            with AUDIT_FILE.open("a") as f:
                f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def read_audit():
    if not AUDIT_FILE.exists():
        return []
    out = []
    for line in AUDIT_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def audit_summary():
    events = read_audit()
    reclaimed = deleted = compressed = compress_saved = sessions = 0
    by_day = {}
    for e in events:
        t = e.get("type")
        day = e.get("iso", "")[:10]
        if t == "trash":
            reclaimed += e.get("bytes", 0); deleted += e.get("count", 0)
            by_day[day] = by_day.get(day, 0) + e.get("bytes", 0)
        elif t == "compress":
            compressed += 1; compress_saved += e.get("saved_bytes", 0)
            reclaimed += e.get("saved_bytes", 0)
            by_day[day] = by_day.get(day, 0) + e.get("saved_bytes", 0)
        elif t == "session":
            sessions += 1
    return {
        "events": events[-300:][::-1],   # most recent first
        "totals": {
            "reclaimed": reclaimed, "deleted": deleted, "compressed": compressed,
            "compress_saved": compress_saved, "sessions": sessions,
            "first": events[0]["iso"] if events else None,
            "event_count": len(events),
        },
        "by_day": by_day,
    }


def weekly_report():
    """7-day lookback of what was reclaimed + space-saving recommendations drawn
    from the current duplicate clusters and largest library files."""
    now = time.time()
    cutoff = now - 7 * 86400

    # 1. What happened in the last 7 days (from the audit log)
    recl = deleted = compressed = 0
    for e in read_audit():
        if e.get("ts", 0) < cutoff:
            continue
        if e.get("type") == "trash":
            recl += e.get("bytes", 0); deleted += e.get("count", 0)
        elif e.get("type") == "compress":
            recl += e.get("saved_bytes", 0); compressed += 1

    recs = []

    # 2. Confident duplicates you still have (library photos only)
    dup_groups, dup_bytes = 0, 0
    for c in CLUSTER_DATA:
        if c.get("match") in ("identical", "near-identical"):
            libs = [p for p in c["photos"] if p.get("source") == "library"]
            if len(libs) >= 2:
                dup_groups += 1
                dup_bytes += sum(p["size"] for p in libs[1:])   # all but the best
    if dup_bytes > 0:
        recs.append({"key": "dupes", "icon": "🗂", "action": "duplicates",
                     "title": f"Clear {dup_groups:,} confident duplicate group" + ("s" if dup_groups != 1 else ""),
                     "detail": "Near-identical photos/videos — keep the best of each, trash the rest.",
                     "save_bytes": dup_bytes})

    # 3. Large library videos worth compressing to H.265
    VID_MIN = 200 * 1024 * 1024
    vids = [f for f in FILES_DATA if f.get("is_video") and f.get("source") == "library" and f.get("size", 0) >= VID_MIN]
    vsave = int(sum(f["size"] for f in vids) * 0.6)
    if vids:
        recs.append({"key": "compress", "icon": "🗜", "action": "files-videos",
                     "title": f"Compress {len(vids):,} large video" + ("s" if len(vids) != 1 else ""),
                     "detail": f"{fmt_bytes(sum(f['size'] for f in vids))} of video — H.265 typically saves ~60%, same quality.",
                     "save_bytes": vsave})

    # 4. Your biggest single files, worth a look
    biggest = [f for f in FILES_DATA if f.get("source") == "library"][:15]
    if biggest:
        recs.append({"key": "biggest", "icon": "📦", "action": "files",
                     "title": f"Review your {len(biggest)} biggest files",
                     "detail": f"They total {fmt_bytes(sum(f['size'] for f in biggest))} — delete anything you no longer need.",
                     "save_bytes": 0})

    recs.sort(key=lambda r: r["save_bytes"], reverse=True)
    return {
        "period_days": 7,
        "since": datetime.fromtimestamp(cutoff).isoformat()[:10],
        "activity": {"reclaimed": recl, "deleted": deleted, "compressed": compressed},
        "opportunity": dup_bytes + vsave,   # "up to" — small overlap possible
        "recommendations": recs,
    }


# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Photo Saver">
<title>iCloud Photo Storage Saver</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0a0d14; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; padding: 0 24px 40px; max-width: 1300px; margin: 0 auto; }

  /* ── Sticky header ── */
  .header { position: sticky; top: 0; z-index: 30; background: rgba(10,13,20,.95); backdrop-filter: blur(14px); border-bottom: 1px solid #1e2535; padding: 10px 0 8px; }
  .header-row1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .header-row2 { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  h1 { font-size: 17px; font-weight: 700; color: #f1f5f9; flex-shrink: 0; }

  /* stats inline */
  .stat-pill { background: #131720; border: 1px solid #1e2535; border-radius: 20px; padding: 4px 12px; font-size: 12px; white-space: nowrap; }
  .stat-pill b { font-weight: 700; }
  .stat-pill.red b  { color: #f87171; }
  .stat-pill.green b { color: #4ade80; }
  .stat-pill.amber b { color: #fbbf24; }
  .stat-pill.blue b  { color: #60a5fa; }

  /* progress bar */
  .prog-wrap { flex: 1; min-width: 120px; }
  .prog-track { height: 4px; background: #1e2535; border-radius: 2px; overflow: hidden; }
  .prog-fill  { height: 100%; background: #3b82f6; border-radius: 2px; transition: width .4s ease; }
  .prog-label { font-size: 10px; color: #475569; margin-top: 3px; }

  /* buttons */
  .btn { border-radius: 7px; padding: 7px 16px; font-size: 12px; font-weight: 600; cursor: pointer; border: none; white-space: nowrap; }
  .btn-trash { background: #991b1b; color: #fca5a5; border: 1px solid #b91c1c; }
  .btn-trash:hover:not(:disabled) { background: #b91c1c; }
  .btn-trash:disabled { opacity: .35; cursor: default; }
  .save-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #334155; vertical-align: middle; transition: background .3s; flex-shrink: 0; }
  .save-dot.saved  { background: #4ade80; }
  .save-dot.saving { background: #fbbf24; }

  .run-pill { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 20px; white-space: nowrap; background: #0a2418; color: #4ade80; border: 1px solid #14532d; }
  .run-pill.stopped { background: #2d0a0a; color: #f87171; border-color: #450a0a; }
  .run-pill .dot { animation: blink 1.6s ease-in-out infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.35} }
  .btn-quit { background: #1e2535; color: #94a3b8; border: 1px solid #334155; }
  .btn-quit:hover { background: #2d0a0a; color: #f87171; border-color: #450a0a; }

  .hidden-toggle { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #64748b; cursor: pointer; margin-left: auto; user-select: none; }
  .hidden-toggle input { appearance: none; -webkit-appearance: none; width: 30px; height: 17px; background: #1e2535; border-radius: 9px; position: relative; cursor: pointer; transition: background .2s; flex-shrink: 0; }
  .hidden-toggle input:checked { background: #7e22ce; }
  .hidden-toggle input::after { content: ''; position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: #fff; transition: transform .2s; }
  .hidden-toggle input:checked::after { transform: translateX(13px); }

  /* filter row */
  .filter-group { display: flex; gap: 4px; }
  .fchip { background: #131720; border: 1px solid #1e2535; border-radius: 6px; padding: 4px 10px; font-size: 11px; color: #64748b; cursor: pointer; white-space: nowrap; }
  .fchip.active { background: #1e3a5f; border-color: #3b82f6; color: #93c5fd; }
  .fsep { width: 1px; background: #1e2535; margin: 0 2px; }
  .flabel { font-size: 10px; color: #475569; text-transform: uppercase; letter-spacing: .06em; margin: 0 4px 0 10px; flex-shrink: 0; }
  .flabel:first-child { margin-left: 0; }
  .sort-select { background: #131720; border: 1px solid #1e2535; border-radius: 6px; padding: 4px 8px; font-size: 11px; color: #64748b; cursor: pointer; }

  /* ── Cluster list ── */
  #wrap { padding-top: 14px; }
  .cluster-ph { margin-bottom: 10px; border-radius: 10px; background: #0d1018; border: 1px solid #1e2535; }
  .cluster-inner { padding: 12px 14px; }

  .cluster-hdr { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; cursor: pointer; user-select: none; }
  .cluster-hdr:hover .chdr-title { color: #94a3b8; }
  .chdr-count { background: #1e2535; color: #60a5fa; font-size: 10px; font-weight: 700; border-radius: 10px; padding: 2px 8px; flex-shrink: 0; }
  .match-chip { font-size: 10px; font-weight: 700; border-radius: 10px; padding: 2px 9px; flex-shrink: 0; letter-spacing: .03em; }
  .m-identical { background: #0a2418; color: #4ade80; border: 1px solid #14532d; }
  .m-near      { background: #0a2418; color: #86efac; border: 1px solid #14532d; }
  .m-similar   { background: #1c1a08; color: #fbbf24; border: 1px solid #a16207; }
  .m-loose     { background: #2d0a0a; color: #f87171; border: 1px solid #450a0a; }
  .chdr-title { font-size: 12px; color: #475569; flex: 1; }
  .chdr-actions { display: flex; gap: 6px; }
  .chdr-btn { font-size: 10px; color: #94a3b8; background: #131720; border: 1px solid #1e2535; border-radius: 5px; padding: 3px 10px; cursor: pointer; }
  .chdr-btn:hover { border-color: #334155; color: #f1f5f9; }
  .chdr-btn-trash:hover { background: #2d0a0a; color: #f87171; border-color: #450a0a; }
  .chdr-btn:hover { color: #94a3b8; border-color: #334155; }
  .collapse-arrow { font-size: 10px; color: #334155; transition: transform .2s; }
  .cluster-ph.collapsed .collapse-arrow { transform: rotate(-90deg); }
  .cluster-ph.collapsed .photos-row { display: none; }

  .photos-row { display: flex; gap: 8px; flex-wrap: wrap; }

  /* ── Photo card ── */
  .photo-card { width: 160px; border-radius: 8px; overflow: hidden; border: 2px solid #1e2535; transition: border-color .12s, opacity .12s; flex-shrink: 0; display: flex; flex-direction: column; }
  .photo-card.keep  { border-color: #16a34a; }
  .photo-card.trash { border-color: #dc2626; opacity: .6; }
  .photo-card img { width: 160px; height: 120px; object-fit: cover; display: block; background: #131720; }
  .photo-card .ph  { width: 160px; height: 120px; display: flex; align-items: center; justify-content: center; color: #334155; font-size: 11px; background: #0a0d14; flex-direction: column; gap: 4px; }
  /* flex column so the toggle can pin to the bottom regardless of badge count */
  .photo-info { padding: 5px 7px 6px; background: #0a0d14; flex: 1 1 auto; display: flex; flex-direction: column; }
  .photo-name { font-size: 10px; color: #94a3b8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .photo-meta { font-size: 10px; color: #475569; margin-top: 1px; display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
  .badge { display: inline-block; font-size: 8px; font-weight: 700; padding: 1px 4px; border-radius: 3px; }
  .b-video { background: #1e0f3d; color: #c084fc; border: 1px solid #3b1f6e; }
  .b-fav   { background: #2d2000; color: #fbbf24; border: 1px solid #a16207; }
  .b-cloud { background: #0c1a2e; color: #60a5fa; border: 1px solid #1e3a6e; }
  .b-shared  { background: #0c1a2e; color: #60a5fa; border: 1px solid #1e3a6e; }
  .b-missing { background: #2d2000; color: #fbbf24; border: 1px solid #a16207; }
  .b-hidden  { background: #1e0f3d; color: #c084fc; border: 1px solid #3b1f6e; }
  .b-raw     { background: #1c1405; color: #fbbf24; border: 1px solid #a16207; }
  /* dim cards that can't be acted on so they read as informational */
  .photo-card.src-missing, .photo-card.src-hidden { opacity: .75; }

  /* img-wrap zoom hint */
  .img-wrap { cursor: zoom-in; position: relative; overflow: hidden; }
  .img-wrap:hover::after { content: '🔍'; position: absolute; bottom: 5px; right: 6px; font-size: 14px; opacity: .75; }

  /* ── Toggle slider ── */
  .toggle-sw { position: relative; width: 84px; height: 24px; border-radius: 12px; cursor: pointer; user-select: none; margin-top: auto; overflow: hidden; flex-shrink: 0; transition: background-color .22s ease; }
  .toggle-sw.keep  { background: #16a34a; }
  .toggle-sw.trash { background: #dc2626; }
  .ts-knob { position: absolute; top: 2px; left: 2px; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 2px 4px rgba(0,0,0,.35); will-change: transform; transition: transform .22s cubic-bezier(.34,1.56,.64,1); pointer-events: none; }
  .toggle-sw.keep  .ts-knob { transform: translateX(60px); }
  .toggle-sw.trash .ts-knob { transform: translateX(0); }
  .ts-lbl { position: absolute; top: 50%; transform: translateY(-50%); font-size: 9px; font-weight: 800; letter-spacing: .06em; color: rgba(255,255,255,.9); pointer-events: none; transition: opacity .2s; }
  .ts-lbl-keep  { left: 7px; }
  .ts-lbl-trash { right: 7px; }
  .toggle-sw.keep  .ts-lbl-trash { opacity: 0; }
  .toggle-sw.trash .ts-lbl-keep  { opacity: 0; }

  /* ── Lightbox ── */
  .lb-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.9); z-index: 200; align-items: center; justify-content: center; flex-direction: column; gap: 10px; }
  .lb-overlay.open { display: flex; }
  .lb-overlay img { max-width: 90vw; max-height: 82vh; border-radius: 6px; box-shadow: 0 0 60px rgba(0,0,0,.8); object-fit: contain; }
  .lb-meta { color: #94a3b8; font-size: 13px; text-align: center; }
  .lb-close { position: absolute; top: 16px; right: 22px; font-size: 26px; color: #64748b; cursor: pointer; }
  .lb-close:hover { color: #f1f5f9; }
  .lb-nav { display: flex; gap: 12px; align-items: center; }
  .lb-nav button { background: #131720; border: 1px solid #1e2535; color: #94a3b8; border-radius: 6px; padding: 5px 16px; font-size: 12px; cursor: pointer; }
  .lb-nav button:hover { border-color: #334155; color: #f1f5f9; }
  .lb-open-btn { background: #1e3a5f; border: 1px solid #2563eb; color: #93c5fd; border-radius: 6px; padding: 5px 14px; font-size: 12px; cursor: pointer; display: flex; align-items: center; gap: 5px; }
  .lb-open-btn:hover { background: #2563eb; color: #fff; }

  /* loading / toast */
  #loading { text-align: center; padding: 80px 0; color: #334155; font-size: 15px; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #1e2535; border-top-color: #60a5fa; border-radius: 50%; animation: spin .7s linear infinite; vertical-align: middle; margin-right: 8px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .toast { position: fixed; bottom: 22px; right: 22px; background: #131720; border: 1px solid #1e2535; border-radius: 8px; padding: 10px 18px; font-size: 13px; opacity: 0; pointer-events: none; transition: opacity .25s; z-index: 100; }
  .toast.show { opacity: 1; pointer-events: auto; }
  .toast.ok  { border-color: #16a34a; color: #4ade80; }
  .toast.err { border-color: #dc2626; color: #f87171; }
  #empty-msg { text-align: center; padding: 60px 0; color: #334155; display: none; }

  /* ── Tabs ── */
  .tabbar { display: flex; gap: 4px; background: #0d1018; border: 1px solid #1e2535; border-radius: 8px; padding: 3px; }
  .tab { background: transparent; border: none; color: #64748b; font-size: 12px; font-weight: 600; padding: 5px 14px; border-radius: 6px; cursor: pointer; }
  .tab.active { background: #1e3a5f; color: #93c5fd; }
  .tab:hover:not(.active) { color: #94a3b8; }

  /* ── Large Files rows ── */
  #files-wrap { padding-top: 14px; }
  .frow { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid #131720; border-radius: 8px; }
  .frow:hover { background: #0d1018; }
  .frow.trash { opacity: .55; }
  .frow.src-shared, .frow.src-hidden { opacity: .8; }
  .fthumb { width: 64px; height: 48px; border-radius: 5px; object-fit: cover; background: #131720; cursor: zoom-in; flex-shrink: 0; }
  /* Privacy: ?blur=1 blurs all thumbnails (for screenshots / screen-sharing) */
  body.blur-thumbs .fthumb, body.blur-thumbs .photo-card img, body.blur-thumbs .lb-overlay img { filter: blur(14px); }
  .fthumb-ph { width: 64px; height: 48px; border-radius: 5px; background: #0a0d14; display: flex; align-items: center; justify-content: center; color: #334155; font-size: 9px; flex-shrink: 0; }
  .fname { width: 230px; flex-shrink: 0; overflow: hidden; }
  .fname .n { font-size: 12px; color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .fname .d { font-size: 10px; color: #475569; }
  .fbar-wrap { flex: 1; min-width: 80px; }
  .fbar-track { height: 16px; background: #131720; border-radius: 4px; overflow: hidden; }
  .fbar { height: 100%; border-radius: 4px; background: linear-gradient(90deg,#1d4ed8,#60a5fa); }
  .fbar.video { background: linear-gradient(90deg,#7e22ce,#c084fc); }
  .fbar.raw   { background: linear-gradient(90deg,#b45309,#fbbf24); }
  .fsize { width: 86px; text-align: right; font-size: 13px; font-weight: 700; color: #e2e8f0; flex-shrink: 0; }
  .fbadges { width: 130px; display: flex; gap: 4px; flex-shrink: 0; flex-wrap: wrap; }
  .frow.r-delete   { opacity: .5; }
  .frow.r-compress { background: #0c1730; }

  /* 3-way Keep / Compress / Delete control */
  .seg { display: flex; border: 1px solid #1e2535; border-radius: 7px; overflow: hidden; flex-shrink: 0; }
  .seg-btn { background: #0d1018; border: none; border-right: 1px solid #1e2535; color: #64748b; font-size: 11px; font-weight: 700; padding: 6px 10px; cursor: pointer; }
  .seg-btn:last-child { border-right: none; }
  .seg-btn:hover:not(.active) { color: #cbd5e1; }
  .seg-btn.active.seg-keep     { background: #16a34a; color: #fff; }
  .seg-btn.active.seg-compress { background: #2563eb; color: #fff; }
  .seg-btn.active.seg-delete   { background: #dc2626; color: #fff; }

  .btn-compress { background: #1e3a5f; color: #93c5fd; border: 1px solid #2563eb; }
  .btn-compress:hover:not(:disabled) { background: #2563eb; color: #fff; }
  .btn-compress:disabled { opacity: .35; cursor: default; }

  /* Compress progress strip */
  #compress-bar { display: none; align-items: center; gap: 12px; background: #0c1730; border: 1px solid #1e3a6e; border-radius: 10px; padding: 10px 16px; margin: 14px 0; }
  #compress-bar.on { display: flex; }
  #compress-bar .cp-track { flex: 1; height: 6px; background: #1e2535; border-radius: 3px; overflow: hidden; }
  #compress-bar .cp-fill { height: 100%; background: linear-gradient(90deg,#2563eb,#60a5fa); border-radius: 3px; transition: width .4s; }
  #compress-bar .cp-label { font-size: 12px; color: #93c5fd; white-space: nowrap; }

  .files-explainer { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 10px 14px; margin-top: 14px; background: #0d1018; border: 1px solid #1e2535; border-radius: 10px; font-size: 12px; color: #94a3b8; }
  .files-explainer b { font-weight: 700; }

  /* ── Activity / metrics ── */
  #activity-wrap { padding-top: 16px; }
  /* Weekly report */
  #weekly-report { margin-bottom: 18px; }
  .wr { background: linear-gradient(135deg,#0c1730,#0d1018); border: 1px solid #1e3a6e; border-radius: 14px; padding: 18px 20px; }
  .wr-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  .wr-head h2 { font-size: 17px; font-weight: 800; color: #f1f5f9; }
  .wr-head .wr-since { font-size: 11px; color: #475569; }
  .wr-summary { font-size: 13px; color: #94a3b8; margin-bottom: 4px; }
  .wr-summary b { color: #4ade80; }
  .wr-opp { font-size: 13px; color: #93c5fd; margin: 8px 0 14px; }
  .wr-opp b { font-size: 18px; font-weight: 800; color: #60a5fa; }
  .wr-rec { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-top: 1px solid #1e2535; }
  .wr-rec .wr-ic { font-size: 20px; flex-shrink: 0; width: 26px; text-align: center; }
  .wr-rec .wr-txt { flex: 1; min-width: 0; }
  .wr-rec .wr-t { font-size: 14px; font-weight: 700; color: #e2e8f0; }
  .wr-rec .wr-d { font-size: 12px; color: #64748b; margin-top: 2px; }
  .wr-rec .wr-save { font-size: 13px; font-weight: 800; color: #4ade80; white-space: nowrap; flex-shrink: 0; }
  .wr-rec .wr-btn { background: #1e3a5f; color: #93c5fd; border: 1px solid #2563eb; border-radius: 7px; padding: 7px 14px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
  .wr-rec .wr-btn:hover { background: #2563eb; color: #fff; }
  .wr-empty { font-size: 13px; color: #4ade80; padding: 10px 0 2px; }

  .metric-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 8px; }
  .mcard { background: #131720; border: 1px solid #1e2535; border-radius: 12px; padding: 16px 18px; }
  .mcard .ml { font-size: 10px; color: #475569; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 8px; }
  .mcard .mv { font-size: 26px; font-weight: 800; color: #f1f5f9; }
  .mcard .ms { font-size: 11px; color: #475569; margin-top: 3px; }
  .mcard.green .mv { color: #4ade80; }
  .mcard.red   .mv { color: #f87171; }
  .mcard.blue  .mv { color: #60a5fa; }
  .section-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #334155; margin: 22px 0 10px; }
  #audit-chart { padding: 14px; background: #0d1018; border: 1px solid #1e2535; border-radius: 12px; overflow-x: auto; }
  .hm-months { display: flex; gap: 3px; margin-left: 26px; margin-bottom: 4px; }
  .hm-months span { font-size: 9px; color: #475569; }
  .hm-body { display: flex; gap: 6px; }
  .hm-dows { display: flex; flex-direction: column; gap: 3px; padding-top: 0; }
  .hm-dows span { height: 12px; font-size: 8px; color: #475569; line-height: 12px; }
  .hm-grid { display: flex; gap: 3px; }
  .hm-col { display: flex; flex-direction: column; gap: 3px; }
  .hm-cell { width: 12px; height: 12px; border-radius: 2px; background: #161b22; cursor: default; }
  .hm-cell.lvl1 { background: #0e4429; }
  .hm-cell.lvl2 { background: #006d32; }
  .hm-cell.lvl3 { background: #26a641; }
  .hm-cell.lvl4 { background: #39d353; }
  .hm-cell.future { background: transparent; }
  .hm-legend { display: flex; align-items: center; gap: 4px; justify-content: flex-end; margin-top: 8px; font-size: 10px; color: #475569; }
  .hm-legend .hm-cell { cursor: default; }
  #hm-tip { position: fixed; z-index: 300; background: #1e2535; border: 1px solid #334155; color: #e2e8f0; font-size: 11px; font-weight: 600; padding: 5px 9px; border-radius: 6px; pointer-events: none; white-space: nowrap; opacity: 0; transition: opacity .1s; }
  #hm-tip.show { opacity: 1; }
  #audit-list { background: #0d1018; border: 1px solid #1e2535; border-radius: 12px; overflow: hidden; }
  .alog { display: flex; align-items: center; gap: 12px; padding: 9px 14px; border-bottom: 1px solid #131720; font-size: 13px; }
  .alog:last-child { border-bottom: none; }
  .alog .ai { width: 22px; text-align: center; font-size: 14px; flex-shrink: 0; }
  .alog .at { color: #cbd5e1; flex: 1; }
  .alog .aw { color: #4ade80; font-weight: 700; font-size: 12px; }
  .alog .ad { color: #475569; font-size: 11px; white-space: nowrap; }

  .sel-toolbar { background: #0c1730; border-top: 1px solid #1e3a6e; padding-top: 8px; margin-top: 4px; border-radius: 0 0 8px 8px; }
  .selall { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #cbd5e1; cursor: pointer; }
  .selall input { width: 15px; height: 15px; cursor: pointer; accent-color: #3b82f6; }
  .sel-count { font-size: 12px; font-weight: 700; color: #93c5fd; min-width: 78px; }
  .sel-apply { font-weight: 700; }
  /* row checkbox */
  .fcheck { width: 17px; height: 17px; flex-shrink: 0; cursor: pointer; accent-color: #3b82f6; }
  .frow.selected { background: #122a4d; outline: 1px solid #2563eb; }
  .frow.r-compressing { background: #0c1730; outline: 1px solid #2563eb; }
  .frow.compressed-done { opacity: .6; }
  .row-status { min-width: 110px; text-align: center; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .row-status.doing { color: #60a5fa; }
  .row-status.done  { color: #4ade80; }
  .row-status.err   { color: #f87171; }
  .row-mini { display: inline-block; width: 60px; height: 5px; background: #1e2535; border-radius: 3px; overflow: hidden; vertical-align: middle; margin-left: 6px; }
  .row-mini > i { display: block; height: 100%; background: #60a5fa; }

  /* ── Mobile / narrow screens ── */
  @media (max-width: 700px) {
    body { padding: 0 10px 30px; }
    h1 { font-size: 15px; width: 100%; }   /* title on its own line */
    .header { padding-top: 8px; }
    .header-row1 { gap: 7px; }
    .tabbar { order: -1; }
    .stat-pill { padding: 3px 9px; font-size: 11px; }
    .prog-wrap { display: none; }           /* reclaim vertical space */
    .btn { padding: 7px 12px; }
    .hidden-toggle { margin-left: 0; }
    .flabel { margin-left: 4px; }

    /* Dedup cards: 2 across */
    .photo-card { width: 46vw; max-width: 230px; }
    .photo-card img, .photo-card .ph { width: 100%; height: 120px; }

    /* Large Files rows reflow into a compact card with big tap targets */
    .frow { flex-wrap: wrap; gap: 8px 10px; padding: 12px 10px; }
    .fthumb, .fthumb-ph { width: 56px; height: 42px; }
    .fname { width: auto; flex: 1; min-width: 0; }
    .fsize { width: auto; order: 2; }
    .fbar-wrap { order: 5; flex-basis: 100%; min-width: 0; }
    .fbadges { width: auto; order: 6; }
    .seg, .row-status { order: 7; }
    .row-status { min-width: 0; }
    .fcheck { width: 24px; height: 24px; }          /* finger-sized */
    .seg-btn { padding: 9px 13px; font-size: 12px; } /* bigger Keep/Compress/Delete */
    .selall input { width: 22px; height: 22px; }

    .sel-toolbar, .header-row2 { gap: 6px; }

    /* Activity */
    .metric-cards { grid-template-columns: repeat(2, 1fr); }
    .mcard .mv { font-size: 20px; }
    .alog { flex-wrap: wrap; gap: 4px 10px; }
    .alog .ad { width: 100%; order: 5; }

    .lb-nav { flex-wrap: wrap; justify-content: center; }
    .lb-nav button, .lb-open-btn { padding: 9px 18px; font-size: 13px; }
    .hm-cell { width: 14px; height: 14px; }          /* easier to tap */
  }
</style>
</head>
<body>

<div class="header">
  <div class="header-row1">
    <h1>iCloud Photo Storage Saver</h1>
    <div class="tabbar">
      <button class="tab active" id="tab-btn-dupes"    onclick="switchTab('dupes')">Duplicates</button>
      <button class="tab"        id="tab-btn-files"    onclick="switchTab('files')">Large Files</button>
      <button class="tab"        id="tab-btn-activity" onclick="switchTab('activity')">Activity</button>
    </div>
    <span class="run-pill" id="run-pill" title="Server status"><span class="dot">●</span> Running</span>
    <!-- Duplicates-mode stats -->
    <span class="mode-dupes">
      <span class="stat-pill blue" title="Duplicate groups in the current view">Groups: <b id="s-clusters">—</b></span>
    </span>
    <!-- Large-Files-mode stats -->
    <span class="mode-files" style="display:none">
      <span class="stat-pill blue" title="Files shown (largest first)">Files: <b id="s-files">—</b></span>
      <span class="stat-pill" title="Total size of the files shown">Total: <b id="s-files-size">—</b></span>
    </span>
    <div class="stat-pill red" title="Items you've marked to trash or compress">Marked: <b id="s-trash">0</b></div>
    <div class="stat-pill green" title="Space your current selections will reclaim — 0 if you keep everything, grows as you mark items to trash or compress">Will save: <b id="s-willsave">0 MB</b></div>
    <div class="prog-wrap mode-dupes">
      <div class="prog-track"><div class="prog-fill" id="prog-fill" style="width:0%"></div></div>
      <div class="prog-label" id="prog-label">0 reviewed</div>
    </div>
    <button class="btn btn-compress mode-files" id="compress-btn" style="display:none" disabled onclick="startCompress()" title="Re-encode marked videos to H.265 and trash the originals">Compress marked</button>
    <button class="btn btn-trash" id="trash-btn" disabled onclick="trashSelected()">Trash marked</button>
    <span class="save-dot" id="save-dot" title="Auto-save"></span>
    <label class="hidden-toggle" title="Show your Hidden album as a filter option (off by default)">
      <input type="checkbox" id="hidden-cb" onchange="setHiddenEnabled(this.checked)">
      <span>Hidden album</span>
    </label>
    <button class="btn btn-quit" id="quit-btn" onclick="quitApp()" title="Stop the app">Quit</button>
  </div>
  <div class="header-row2 mode-dupes">
    <span class="flabel">Type</span>
    <div class="filter-group" id="type-filter">
      <div class="fchip active" data-val="all"    onclick="setFilter('type','all',this)">All</div>
      <div class="fchip"        data-val="photos" onclick="setFilter('type','photos',this)">Photos</div>
      <div class="fchip"        data-val="videos" onclick="setFilter('type','videos',this)">Videos</div>
    </div>
    <span class="flabel">Group size</span>
    <div class="filter-group" id="size-filter">
      <div class="fchip active" data-val="0" title="Groups of 2 or more" onclick="setFilter('minDupes',0,this)">2+</div>
      <div class="fchip"        data-val="2" title="Groups of 3 or more" onclick="setFilter('minDupes',2,this)">3+</div>
      <div class="fchip"        data-val="3" title="Groups of 5 or more" onclick="setFilter('minDupes',3,this)">5+</div>
    </div>
    <span class="flabel">Match</span>
    <div class="filter-group" id="match-filter">
      <div class="fchip active" data-val="all"       onclick="setFilter('match','all',this)">All</div>
      <div class="fchip"        data-val="confident" title="Only identical / near-identical groups" onclick="setFilter('match','confident',this)">Confident only</div>
    </div>
    <span class="flabel">Where</span>
    <div class="filter-group" id="source-filter">
      <div class="fchip active" data-val="all"     title="Your own deletable library (shared & hidden excluded)" onclick="setFilter('source','all',this)">All</div>
      <div class="fchip"        data-val="library" title="Photos in your own library (deletable)" onclick="setFilter('source','library',this)">My library</div>
      <div class="fchip"        data-val="shared"  title="Shared albums / shared with you" onclick="setFilter('source','shared',this)">Shared</div>
      <div class="fchip hidden-chip" style="display:none" data-val="hidden"  title="Your Hidden album (manage inside Photos)" onclick="setFilter('source','hidden',this)">Hidden</div>
      <div class="fchip"        data-val="missing" title="Photos can't locate these by ID" onclick="setFilter('source','missing',this)">Can't locate</div>
    </div>
    <span class="flabel">Sort</span>
    <select class="sort-select" onchange="setSort(this.value)">
      <option value="default">Most confident first</option>
      <option value="savings">Most savings first</option>
      <option value="dupes">Most photos first</option>
      <option value="date-new">Newest first</option>
      <option value="date-old">Oldest first</option>
    </select>
  </div>
  <!-- Large Files controls -->
  <div class="header-row2 mode-files" style="display:none">
    <span class="flabel">Type</span>
    <div class="filter-group" id="f-type-filter">
      <div class="fchip active" data-val="all"    onclick="setFFilter('type','all',this)">All</div>
      <div class="fchip"        data-val="videos" onclick="setFFilter('type','videos',this)">Videos</div>
      <div class="fchip"        data-val="photos" onclick="setFFilter('type','photos',this)">Photos</div>
      <div class="fchip"        data-val="raw"    onclick="setFFilter('type','raw',this)">RAW</div>
    </div>
    <span class="flabel">Where</span>
    <div class="filter-group" id="f-source-filter">
      <div class="fchip active" data-val="all"     title="Your own deletable library" onclick="setFFilter('source','all',this)">My library</div>
      <div class="fchip"        data-val="shared"  onclick="setFFilter('source','shared',this)">Shared</div>
      <div class="fchip hidden-chip" style="display:none" data-val="hidden"  onclick="setFFilter('source','hidden',this)">Hidden</div>
    </div>
    <span class="flabel">Sort</span>
    <select class="sort-select" onchange="setFSort(this.value)">
      <option value="size">Largest first</option>
      <option value="size-asc">Smallest first</option>
      <option value="date-new">Newest first</option>
      <option value="date-old">Oldest first</option>
      <option value="name">Name (A–Z)</option>
    </select>
  </div>
  <!-- Selection toolbar (Large Files) -->
  <div class="header-row2 mode-files sel-toolbar" style="display:none">
    <label class="selall"><input type="checkbox" id="sel-all-cb" onchange="toggleSelectAll(this.checked)"> Select all shown</label>
    <span class="sel-count" id="sel-count">0 selected</span>
    <span class="flabel" style="margin-left:6px">Apply to selected</span>
    <div class="filter-group">
      <div class="fchip sel-apply" onclick="applyToSelected('keep')">Keep</div>
      <div class="fchip sel-apply" onclick="applyToSelected('compress')">Compress</div>
      <div class="fchip sel-apply" onclick="applyToSelected('delete')">Delete</div>
    </div>
    <div class="fchip" id="range-btn" title="Tap two rows to select everything between them" onclick="toggleRangeMode()">Range</div>
    <div class="fchip" style="margin-left:6px" onclick="clearSelection()">Clear</div>
  </div>
</div>

<!-- What the Large Files actions do -->
<div class="files-explainer mode-files" style="display:none">
  <span><b style="color:#16a34a">Keep</b> leaves the file as-is.</span>
  <span><b style="color:#2563eb">Compress</b> re-encodes the video to <b>H.265 / HEVC</b> — same resolution &amp; length, near-identical quality, typically <b>50–80% smaller</b>; the original moves to Photos trash. <i>(library videos only)</i></span>
  <span><b style="color:#dc2626">Delete</b> moves the file to Photos trash.</span>
</div>

<div id="loading" style="padding-top:60px"><span class="spinner"></span>Loading…</div>
<div id="compress-bar">
  <span class="spinner" style="margin:0"></span>
  <span class="cp-label" id="cp-label">Compressing…</span>
  <div class="cp-track"><div class="cp-fill" id="cp-fill" style="width:0%"></div></div>
  <span class="cp-label" id="cp-saved"></span>
</div>
<div id="wrap" class="mode-dupes"></div>
<div id="files-wrap" class="mode-files" style="display:none"></div>

<!-- Activity / metrics tab -->
<div id="activity-wrap" class="mode-activity" style="display:none">
  <div id="weekly-report"></div>
  <div class="metric-cards">
    <div class="mcard green"><div class="ml">Total reclaimed</div><div class="mv" id="m-reclaimed">—</div><div class="ms" id="m-reclaimed-sub">all time</div></div>
    <div class="mcard red">  <div class="ml">Photos deleted</div><div class="mv" id="m-deleted">—</div></div>
    <div class="mcard blue"> <div class="ml">Videos compressed</div><div class="mv" id="m-compressed">—</div><div class="ms" id="m-compressed-sub"></div></div>
    <div class="mcard">      <div class="ml">App sessions</div><div class="mv" id="m-sessions">—</div></div>
    <div class="mcard">      <div class="ml">Tracking since</div><div class="mv" id="m-since" style="font-size:15px">—</div></div>
  </div>
  <div class="section-title">Reclaimed by day</div>
  <div id="audit-chart"></div>
  <div class="section-title">Activity log</div>
  <div id="audit-list"></div>
</div>
<div id="empty-msg">Nothing matches the current filters.</div>
<div class="toast" id="toast"></div>
<div id="hm-tip"></div>

<!-- Lightbox -->
<div class="lb-overlay" id="lb" onclick="closeLightbox(event)">
  <span class="lb-close" onclick="closeLightbox()">✕</span>
  <img id="lb-img" src="" alt="">
  <div class="lb-meta" id="lb-meta"></div>
  <div class="lb-nav">
    <button onclick="event.stopPropagation();lbStep(-1)">◀ Prev</button>
    <button onclick="event.stopPropagation();lbStep(1)">Next ▶</button>
    <button class="lb-open-btn" id="lb-open-btn" onclick="event.stopPropagation();openInPhotos()" title="Open in Photos.app">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg>
      Open in Photos
    </button>
    <button class="lb-open-btn" id="lb-finddate-btn" style="display:none" onclick="event.stopPropagation();findByDate()" title="Search Photos for this date">
      🔎 Find by date
    </button>
    <span id="lb-note" style="display:none;color:#c084fc;font-size:12px;max-width:480px"></span>
  </div>
</div>

<script>
let allClusters = [];   // original full list
let clusters    = [];   // filtered + sorted view
let decisions   = {};
let photoMeta   = {};
const clusterMap = {};

let filters = { type: 'all', minDupes: 0, source: 'all', match: 'all' };
let sortMode = 'default';
let reviewedSet = new Set();  // cluster ids with at least one explicit decision
let uuidToClusterId = {};     // uuid → cluster id, for marking reviewed on toggle

// ── Filters & sort ──────────────────────────────────────────────────────────
function setFilter(key, val, el) {
  filters[key] = val;
  el.closest('.filter-group').querySelectorAll('.fchip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  applyFiltersAndSort();
}
function setSort(val) { sortMode = val; applyFiltersAndSort(); }

// Which photos in a cluster are visible under the current Source filter.
// Shared and Hidden photos are OPT-IN: excluded from "All" (you can't delete
// them through the app anyway), shown only when their filter is selected.
function visiblePhotosFor(c) {
  const s = filters.source;
  return c.photos.filter(p => {
    const ps = p.source || 'library';
    if (s === 'all') return ps !== 'hidden' && ps !== 'shared';
    return ps === s;
  });
}

function applyFiltersAndSort() {
  let list = allClusters.filter(c => {
    c._vis = visiblePhotosFor(c);                 // stash visible set for render
    if (c._vis.length < filters.minDupes + 2) return false;
    if (filters.type === 'photos' && c._vis.some(p => p.is_video)) return false;
    if (filters.type === 'videos' && !c._vis.some(p => p.is_video)) return false;
    if (filters.match === 'confident' && !(c.match === 'identical' || c.match === 'near-identical')) return false;
    return true;
  });
  // 'default' keeps backend order = tightest/most-confident first.
  if (sortMode === 'date-new') list = [...list].sort((a,b) => b.photos[0].date.localeCompare(a.photos[0].date));
  else if (sortMode === 'date-old') list = [...list].sort((a,b) => a.photos[0].date.localeCompare(b.photos[0].date));
  else if (sortMode === 'savings') list = [...list].sort((a,b) => (b.removable_bytes||0) - (a.removable_bytes||0));
  else if (sortMode === 'dupes') list = [...list].sort((a,b) => b.photos.length - a.photos.length);
  clusters = list;
  renderAll();
  renderStats();
}

// ── Lazy render: IntersectionObserver + requestAnimationFrame batching ────────
let observer;
const renderQueue = [];
let rafPending = false;

function flushQueue() {
  rafPending = false;
  // Paint up to 4 clusters per animation frame — smooth without blocking
  const batch = renderQueue.splice(0, 4);
  batch.forEach(({ el, cluster }) => {
    if (!el.dataset.painted) { el.dataset.painted = '1'; paintCluster(el, cluster); }
  });
  if (renderQueue.length) scheduleFlush();
}

function scheduleFlush() {
  if (!rafPending) { rafPending = true; requestAnimationFrame(flushQueue); }
}

function initObserver() {
  observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting || entry.target.dataset.queued) return;
      entry.target.dataset.queued = '1';
      observer.unobserve(entry.target);
      renderQueue.push({ el: entry.target, cluster: clusters[+entry.target.dataset.idx] });
    });
    if (renderQueue.length) scheduleFlush();
  }, { rootMargin: '200px' });
}

function renderAll() {
  if (observer) observer.disconnect();
  renderQueue.length = 0; rafPending = false;
  initObserver();

  const wrap = document.getElementById('wrap');
  const empty = document.getElementById('empty-msg');

  if (!clusters.length) { wrap.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  // Build placeholders via DocumentFragment — single DOM write, no innerHTML churn
  const frag = document.createDocumentFragment();
  clusters.forEach((c, i) => {
    const el = document.createElement('div');
    el.className = 'cluster-ph';
    el.dataset.idx = i; el.dataset.id = c.id;
    el.style.minHeight = (36 + Math.ceil((c._vis||c.photos).length / 6) * 160 + 24) + 'px';
    frag.appendChild(el);
  });
  wrap.innerHTML = '';
  wrap.appendChild(frag);

  wrap.querySelectorAll('.cluster-ph').forEach(el => observer.observe(el));
}

function photoCardHTML(p, cid) {
  const d = decisions[p.uuid] || 'keep';
  const src = p.source || 'library';
  const thumb = p.has_deriv
    ? `<img src="/thumb/${p.uuid}" loading="lazy" onerror="this.outerHTML='<div class=ph>No preview</div>'">`
    : `<div class="ph">☁ iCloud</div>`;
  const badges = [
    p.favorite  ? `<span class="badge b-fav">★</span>` : '',
    p.is_video  ? `<span class="badge b-video">VIDEO</span>` : '',
    p.in_cloud  ? `<span class="badge b-cloud">☁</span>` : '',
    src === 'shared'  ? `<span class="badge b-shared" title="In a shared album / shared with you">SHARED</span>` : '',
    src === 'hidden'  ? `<span class="badge b-hidden" title="In your Hidden album — unlock it in Photos to manage">HIDDEN</span>` : '',
    src === 'missing' ? `<span class="badge b-missing" title="Photos can't locate this by ID — find it manually by date">CAN'T LOCATE</span>` : '',
  ].filter(Boolean).join('');
  return `<div class="photo-card ${d} src-${src}" data-uuid="${p.uuid}" onclick="toggle('${p.uuid}')">
    <div class="img-wrap" onclick="event.stopPropagation();openLightbox('${p.uuid}','${cid}')">${thumb}</div>
    <div class="photo-info">
      <div class="photo-name" title="${esc(p.filename)}">${esc(p.filename)}</div>
      <div class="photo-meta"><span>${p.size_str}</span><span>${p.date}</span>${badges}</div>
      <div class="toggle-sw action-badge ${d}" onclick="event.stopPropagation();toggle('${p.uuid}')">
        <span class="ts-knob"></span>
        <span class="ts-lbl ts-lbl-keep">KEEP</span>
        <span class="ts-lbl ts-lbl-trash">TRASH</span>
      </div>
    </div>
  </div>`;
}

const MATCH_LABELS = {
  'identical':      ['Identical',      'm-identical'],
  'near-identical': ['Near-identical', 'm-near'],
  'similar':        ['Similar',        'm-similar'],
  'loose':          ['Loosely similar','m-loose'],
};

function paintCluster(el, c) {
  const photos = c._vis || c.photos;
  clusterMap[c.id] = photos;
  const totalSize = photos.reduce((s,p) => s + p.size, 0);
  const savings = photos.slice(1).reduce((s,p) => s + p.size, 0);  // all-but-best
  const [mlabel, mcls] = MATCH_LABELS[c.match] || MATCH_LABELS['similar'];
  el.innerHTML = `<div class="cluster-inner">
    <div class="cluster-hdr" onclick="toggleCollapse('${c.id}')">
      <span class="chdr-count">${photos.length} photos</span>
      <span class="match-chip ${mcls}" title="How alike the photos in this group are">${mlabel}</span>
      <span class="chdr-title">${photos[0].date} · ${fmtBytes(totalSize)} total · save up to ${fmtBytes(savings)}</span>
      <div class="chdr-actions" onclick="event.stopPropagation()">
        <button class="chdr-btn" onclick="keepAll('${c.id}')">Keep all</button>
        <button class="chdr-btn chdr-btn-trash" onclick="trashAll('${c.id}')">Trash dupes (keep best)</button>
      </div>
      <span class="collapse-arrow">▼</span>
    </div>
    <div class="photos-row">${photos.map(p => photoCardHTML(p, c.id)).join('')}</div>
  </div>`;
}

function toggleCollapse(cid) {
  const el = document.querySelector(`.cluster-ph[data-id="${cid}"]`);
  if (el) el.classList.toggle('collapsed');
}

function keepAll(cid) {
  (clusterMap[cid] || []).forEach(p => { decisions[p.uuid] = 'keep'; refreshCard(p.uuid); });
  reviewedSet.add(cid); updateProgress();
  rebuildStats(); scheduleSave();
}
function trashAll(cid) {
  (clusterMap[cid] || []).forEach((p, i) => { decisions[p.uuid] = i === 0 ? 'keep' : 'trash'; refreshCard(p.uuid); });
  reviewedSet.add(cid); updateProgress();
  rebuildStats(); scheduleSave();
}

function refreshCard(uuid) {
  const d = decisions[uuid];
  // Works for both dedup cards (.photo-card) and Large Files rows (.frow).
  document.querySelectorAll(`[data-uuid="${uuid}"]`).forEach(el => {
    if (!el.classList.contains('photo-card') && !el.classList.contains('frow')) return;
    el.classList.remove('keep', 'trash'); el.classList.add(d);
    const sw = el.querySelector('.toggle-sw');
    if (sw) { sw.classList.remove('keep', 'trash'); sw.classList.add(d); }
  });
}

// ── Lightbox ─────────────────────────────────────────────────────────────────
let lbPhotos = [], lbIndex = 0;
function openLightbox(uuid, cid) {
  lbPhotos = clusterMap[cid] || [];
  lbIndex  = lbPhotos.findIndex(p => p.uuid === uuid);
  _renderLb();
  document.getElementById('lb').classList.add('open');
}
function _renderLb() {
  const p = lbPhotos[lbIndex];
  if (!p) return;
  document.getElementById('lb-img').src = p.has_deriv ? `/thumb/${p.uuid}` : '';
  const src = p.source || 'library';
  const tag = src === 'shared' ? '  ·  SHARED'
            : src === 'hidden' ? '  ·  HIDDEN ALBUM'
            : src === 'missing' ? "  ·  CAN'T LOCATE" : '';
  document.getElementById('lb-meta').textContent =
    `${p.filename}  ·  ${p.size_str}  ·  ${p.date}${tag}  ·  ${lbIndex+1}/${lbPhotos.length}`;
  // Open-in-Photos (spotlight) only reliably navigates to your OWN library
  // timeline. Shared-album items return success but don't actually navigate,
  // so offer Find-by-date for them. Hidden items are unreachable → guidance.
  const openBtn = document.getElementById('lb-open-btn');
  const findBtn = document.getElementById('lb-finddate-btn');
  const note    = document.getElementById('lb-note');
  openBtn.style.display = note.style.display = findBtn.style.display = 'none';
  if (src === 'hidden') {
    note.textContent = 'In your Hidden album — open Photos ▸ View ▸ Show Hidden Album (unlock) to manage it.';
    note.style.display = '';
  } else if (src === 'shared') {
    findBtn.style.display = '';
    note.textContent = "In a shared album — Photos can't jump to it directly; use Find by date.";
    note.style.display = '';
  } else if (src === 'missing') {
    findBtn.style.display = '';
  } else {
    openBtn.style.display = '';
  }
}
function lbStep(d) { lbIndex = (lbIndex + d + lbPhotos.length) % lbPhotos.length; _renderLb(); }

async function findByDate() {
  const p = lbPhotos[lbIndex];
  if (!p || !p.date) return;
  const btn = document.getElementById('lb-finddate-btn');
  btn.textContent = 'Searching…';
  try {
    const res = await fetch('/finddate/' + encodeURIComponent(p.date)).then(r=>r.json());
    if (res.ok) {
      toast(`Searching Photos for ${res.date}`, 'ok');
    } else {
      toast(`Date ${res.date} copied — press ⌘F in Photos and paste (grant Accessibility to automate)`, 'err');
    }
  } catch(e) { toast('Find by date failed: ' + e.message, 'err'); }
  setTimeout(() => { btn.textContent = '🔎 Find by date'; }, 2000);
}

const OPEN_BTN_HTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg> Open in Photos';

async function openInPhotos() {
  const p = lbPhotos[lbIndex];
  if (!p) return;
  const btn = document.querySelector('.lb-open-btn');
  if (btn) { btn.textContent = 'Opening…'; btn.style.opacity = '.6'; }
  try {
    const res = await fetch(`/open/${p.uuid}`).then(r => r.json());
    if (btn) {
      btn.style.opacity = '1';
      if (res.ok) {
        btn.textContent = '✓ Opened in Photos';
      } else {
        btn.textContent = '✕ Failed';
        toast('Open in Photos failed: ' + (res.error || 'unknown'), 'err');
      }
      setTimeout(() => { btn.innerHTML = OPEN_BTN_HTML; }, 2000);
    }
  } catch(e) {
    if (btn) { btn.textContent = '✕ Failed'; btn.style.opacity = '1'; setTimeout(() => { btn.innerHTML = OPEN_BTN_HTML; }, 2000); }
  }
}

function closeLightbox(e) {
  if (e && e.target !== document.getElementById('lb') && !e.target.classList.contains('lb-close')) return;
  document.getElementById('lb').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (!document.getElementById('lb').classList.contains('open')) return;
  if (e.key === 'Escape') document.getElementById('lb').classList.remove('open');
  if (e.key === 'ArrowLeft')  lbStep(-1);
  if (e.key === 'ArrowRight') lbStep(1);
});

// ── Incremental stats (O(1) per toggle, not O(n)) ───────────────────────────
function fmtBytes(b) {
  if (b >= 1073741824) return (b/1073741824).toFixed(1)+' GB';
  if (b >= 1048576)    return (b/1048576).toFixed(0)+' MB';
  return (b/1024).toFixed(0)+' KB';
}
// HTML-escape user-derived text (filenames can contain & < > " ') before it goes
// into innerHTML or an attribute, so odd names can't break the layout.
function esc(s) {
  return String(s == null ? '' : s)
    .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
    .replaceAll('"','&quot;').replaceAll("'",'&#39;');
}
let _statsTrash = 0, _statsSavings = 0, _statsCompress = 0, _statsCompressEst = 0;
const COMPRESS_RATIO = 0.6;   // typical H.265 reduction, for the estimate only

function rebuildStats() {
  _statsTrash = 0; _statsSavings = 0; _statsCompress = 0; _statsCompressEst = 0;
  for (const [u, d] of Object.entries(decisions)) {
    if (d === 'trash') { _statsTrash++; _statsSavings += photoMeta[u]?.size || 0; }
    else if (d === 'compress') { _statsCompress++; _statsCompressEst += (photoMeta[u]?.size || 0) * COMPRESS_RATIO; }
  }
  renderStats();
}
function renderStats() {
  document.getElementById('s-clusters').textContent = clusters.length.toLocaleString();
  // Marked = everything you've staged (trash + compress). Will save = the space
  // those choices reclaim (full size for trash, ~estimate for compress). Both
  // start at 0 when you keep everything and grow as you mark items.
  const marked  = _statsTrash + _statsCompress;
  const willSave = _statsSavings + _statsCompressEst;
  document.getElementById('s-trash').textContent    = marked.toLocaleString();
  document.getElementById('s-willsave').textContent = willSave ? fmtBytes(willSave) : '0 MB';
  const btn = document.getElementById('trash-btn');
  btn.disabled = !_statsTrash;
  btn.textContent = _statsTrash ? `Trash ${_statsTrash} marked` : 'Trash marked';
  const cbtn = document.getElementById('compress-btn');
  if (cbtn) {
    cbtn.disabled = !_statsCompress;
    cbtn.textContent = _statsCompress ? `Compress ${_statsCompress} (~${fmtBytes(_statsCompressEst)} saved)` : 'Compress marked';
  }
}
function updateProgress() {
  const pct = allClusters.length ? Math.round(reviewedSet.size / allClusters.length * 100) : 0;
  document.getElementById('prog-fill').style.width  = pct + '%';
  document.getElementById('prog-label').textContent = `${reviewedSet.size} of ${allClusters.length} reviewed`;
}

// ── Toggle (O(1) stats update) ────────────────────────────────────────────────
function toggle(uuid) {
  const prev = decisions[uuid];
  const next = prev === 'keep' ? 'trash' : 'keep';
  decisions[uuid] = next;
  const size = photoMeta[uuid]?.size || 0;
  if (next === 'trash') { _statsTrash++; _statsSavings += size; }
  else                  { _statsTrash--; _statsSavings -= size; }
  const cid = uuidToClusterId[uuid];
  if (cid) { reviewedSet.add(cid); updateProgress(); }
  refreshCard(uuid);
  renderStats();
  scheduleSave();
}

// ── Save / restore ────────────────────────────────────────────────────────────
let saveTimer = null;
function scheduleSave() {
  const dot = document.getElementById('save-dot');
  dot.className = 'save-dot saving';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    // Persist ONLY the photos marked to trash (keep is the default). The file
    // is effectively your delete list; untrashing removes it on next save.
    const marks = {};
    for (const u in decisions) if (decisions[u] === 'trash' || decisions[u] === 'compress') marks[u] = decisions[u];
    await fetch('/decisions', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(marks) });
    dot.className = 'save-dot saved';
  }, 800);
}

// ── Trash ─────────────────────────────────────────────────────────────────────
async function trashSelected() {
  const uuids = Object.entries(decisions).filter(([,d])=>d==='trash').map(([u])=>u);
  if (!uuids.length) return;
  const btn = document.getElementById('trash-btn');
  btn.disabled = true; btn.textContent = 'Trashing…';
  try {
    const res = await fetch('/trash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uuids})}).then(r=>r.json());
    if (res.error) throw new Error(res.error);
    const extra = (res.already_gone?` ${res.already_gone} already removed.`:'') + (res.failed?` ${res.failed} failed.`:'');
    toast(`Moved ${res.trashed} to trash.${extra}`, res.failed?'err':'ok');
    const done = new Set(res.trashed_uuids || []);
    done.forEach(u => { delete decisions[u]; delete photoMeta[u]; });
    allClusters = allClusters.map(c=>({...c,photos:c.photos.filter(p=>!done.has(p.uuid))})).filter(c=>c.photos.length>=2);
    allClusters.forEach(c=>c.photos.forEach((p,i)=>{
      if (!decisions[p.uuid]) decisions[p.uuid] = 'keep';
      photoMeta[p.uuid] = {size:p.size};
    }));
    // Drop trashed items from the Large Files list too.
    if (filesLoaded) { allFiles = allFiles.filter(f => !done.has(f.uuid)); }
    rebuildStats();
    applyFiltersAndSort();
    if (filesLoaded) applyFilesFilters();
  } catch(e) { toast('Error: '+e.message,'err'); } finally { btn.disabled=false; }
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast show '+type;
  setTimeout(()=>t.className='toast', 5000);
}

// ── Quit + running heartbeat ──────────────────────────────────────────────────
async function quitApp() {
  if (!confirm('Quit Photos Dedup? The server will stop. Your decisions are saved.')) return;
  try { await fetch('/quit', {method:'POST'}); } catch(e) {}
  markStopped();
}

function markStopped() {
  const pill = document.getElementById('run-pill');
  pill.className = 'run-pill stopped';
  pill.innerHTML = '● Stopped';
  document.getElementById('quit-btn').disabled = true;
}

// Heartbeat: if the server stops responding, flip the pill to Stopped.
setInterval(async () => {
  try {
    const r = await fetch('/status', {cache:'no-store'});
    if (r.ok) {
      const pill = document.getElementById('run-pill');
      if (pill.classList.contains('stopped')) {  // came back
        pill.className = 'run-pill';
        pill.innerHTML = '<span class="dot">●</span> Running';
        document.getElementById('quit-btn').disabled = false;
      }
    }
  } catch(e) { markStopped(); }
}, 3000);

// ── Init ──────────────────────────────────────────────────────────────────────
async function init(data) {
  allClusters = data;
  // Default everything to KEEP. Nothing is marked for deletion unless you
  // explicitly toggle it. The saved file only records your trash choices.
  data.forEach(c => c.photos.forEach((p) => {
    decisions[p.uuid] = 'keep';
    photoMeta[p.uuid] = {size: p.size};
    uuidToClusterId[p.uuid] = c.id;
  }));
  try {
    const saved = await fetch('/decisions').then(r=>r.json());
    let n = 0;
    Object.entries(saved).forEach(([u,d]) => {
      if (decisions[u]!==undefined && (d==='trash'||d==='compress')) { decisions[u]=d; n++; }
    });
    if (n) toast(`Restored ${n} marked photos`, 'ok');
  } catch(e) {}
  document.getElementById('loading').style.display = 'none';
  rebuildStats();
  applyFiltersAndSort();
}

// ── Boot: poll /status until data is ready, then load clusters ───────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

function bootScreen(s) {
  const el = document.getElementById('loading');
  let label = s.detail || 'Working…';
  let bar = '';
  if (s.phase === 'hashing') {
    const pct = Math.round((s.progress || 0) * 100);
    label = `Hashing ${ (s.total_photos||0).toLocaleString() } thumbnails…`;
    bar = `<div style="max-width:320px;margin:14px auto 0">
      <div style="height:5px;background:#1e2535;border-radius:3px;overflow:hidden">
        <div style="height:100%;width:${pct}%;background:#3b82f6;border-radius:3px;transition:width .4s"></div>
      </div>
      <div style="font-size:11px;color:#475569;margin-top:6px">${pct}%</div>
    </div>`;
  }
  el.innerHTML = `<span class="spinner"></span>${label}${bar}
    <div style="font-size:12px;color:#334155;margin-top:18px">First run hashes your whole library — later runs are instant.</div>`;
}

async function boot() {
  while (true) {
    let s;
    try { s = await fetch('/status').then(r=>r.json()); }
    catch(e) { await sleep(600); continue; }
    if (s.phase === 'error') {
      document.getElementById('loading').innerHTML =
        `<div style="color:#f87171">Error: ${s.error||'unknown'}</div>`;
      return;
    }
    if (s.ready) break;
    bootScreen(s);
    await sleep(500);
  }
  const data = await fetch('/clusters').then(r=>r.json());
  init(data);
  resumeCompressIfRunning();   // pick up an in-progress compression after a refresh
}

// ── Large Files tab ───────────────────────────────────────────────────────────
let allFiles = [], filesView = [], filesLoaded = false, filesMaxSize = 1;
const filesByUuid = new Map();   // uuid → file object (O(1) lookups for bulk actions)
let fFilters = { type: 'all', source: 'all' };
let fSort = 'size';
let filesObserver = null;

function switchTab(mode) {
  ['dupes','files','activity'].forEach(m => {
    document.getElementById('tab-btn-' + m).classList.toggle('active', m === mode);
    document.querySelectorAll('.mode-' + m).forEach(e => e.style.display = (m === mode) ? '' : 'none');
  });
  document.getElementById('empty-msg').style.display = 'none';
  if (mode === 'files') { if (!filesLoaded) loadFiles(); else applyFilesFilters(); }
  if (mode === 'activity') loadAudit();
}

// ── Activity / metrics ────────────────────────────────────────────────────────
const AUDIT_ICONS = { trash: '🗑️', compress: '🗜️', session: '▶️' };

// ── Weekly report ─────────────────────────────────────────────────────────────
async function loadReport() {
  let r;
  try { r = await fetch('/report', {cache:'no-store'}).then(x => x.json()); }
  catch(e) { return; }
  const a = r.activity || {};
  const did = a.reclaimed > 0
    ? `This week you reclaimed <b>${fmtBytes(a.reclaimed)}</b> — ${(a.deleted||0).toLocaleString()} deleted, ${(a.compressed||0).toLocaleString()} compressed.`
    : `No cleanup logged in the last 7 days.`;
  const label = ac => ac === 'files-videos' ? 'Compress' : 'Review';
  const recs = (r.recommendations || []).map(rec => `
    <div class="wr-rec">
      <span class="wr-ic">${rec.icon}</span>
      <div class="wr-txt"><div class="wr-t">${esc(rec.title)}</div><div class="wr-d">${esc(rec.detail)}</div></div>
      ${rec.save_bytes ? `<span class="wr-save">~${fmtBytes(rec.save_bytes)}</span>` : ''}
      <button class="wr-btn" onclick="applyRecommendation('${rec.action}')">${label(rec.action)}</button>
    </div>`).join('');
  document.getElementById('weekly-report').innerHTML = `
    <div class="wr">
      <div class="wr-head"><h2>Weekly report</h2><span class="wr-since">last 7 days · since ${r.since}</span></div>
      <div class="wr-summary">${did}</div>
      ${r.opportunity > 0 ? `<div class="wr-opp">You could still reclaim up to <b>${fmtBytes(r.opportunity)}</b>:</div>` : ''}
      ${recs || '<div class="wr-empty">✓ Nothing obvious to clean up right now — nice work.</div>'}
    </div>`;
}

function setChipActive(groupId, val) {
  document.querySelectorAll('#' + groupId + ' .fchip').forEach(c => c.classList.toggle('active', c.dataset.val === val));
}

// Jump from a recommendation straight to the relevant filtered view.
function applyRecommendation(action) {
  if (action === 'duplicates') {
    filters.match = 'confident'; setChipActive('match-filter', 'confident');
    switchTab('dupes'); applyFiltersAndSort();
  } else if (action === 'files-videos') {
    fFilters.type = 'videos'; fFilters.source = 'all';
    setChipActive('f-type-filter', 'videos'); setChipActive('f-source-filter', 'all');
    switchTab('files'); if (filesLoaded) applyFilesFilters();
  } else { // files (biggest)
    fFilters.type = 'all'; setChipActive('f-type-filter', 'all');
    switchTab('files'); if (filesLoaded) applyFilesFilters();
  }
  window.scrollTo(0, 0);
}

async function loadAudit() {
  loadReport();
  let a;
  try { a = await fetch('/audit', {cache:'no-store'}).then(r => r.json()); }
  catch(e) { return; }
  const t = a.totals || {};
  document.getElementById('m-reclaimed').textContent  = fmtBytes(t.reclaimed || 0);
  document.getElementById('m-reclaimed-sub').textContent = `from ${(t.deleted||0).toLocaleString()} deleted + ${(t.compressed||0).toLocaleString()} compressed`;
  document.getElementById('m-deleted').textContent    = (t.deleted || 0).toLocaleString();
  document.getElementById('m-compressed').textContent = (t.compressed || 0).toLocaleString();
  document.getElementById('m-compressed-sub').textContent = t.compress_saved ? fmtBytes(t.compress_saved) + ' saved' : '';
  document.getElementById('m-sessions').textContent   = (t.sessions || 0).toLocaleString();
  document.getElementById('m-since').textContent      = t.first ? t.first.slice(0,10) : '—';

  renderHeatmap(a.by_day || {});

  // Event log — richer detail per entry
  const list = document.getElementById('audit-list');
  list.innerHTML = (a.events || []).map(e => {
    const icon = AUDIT_ICONS[e.type] || '•';
    let text = '', sub = '', worth = '';
    if (e.type === 'trash') {
      const parts = [];
      if (e.videos) parts.push(`${e.videos} video${e.videos===1?'':'s'}`);
      if (e.photos) parts.push(`${e.photos} photo${e.photos===1?'':'s'}`);
      text = `Trashed ${e.count} item${e.count===1?'':'s'}` + (parts.length?` (${parts.join(', ')})`:'');
      worth = fmtBytes(e.bytes||0) + ' freed';
      if (e.items && e.items.length) sub = esc(e.items.slice(0,4).join(', ')) + (e.count>4?` +${e.count-4} more`:'');
    } else if (e.type === 'compress') {
      const pct = e.orig_bytes ? Math.round((e.saved_bytes/e.orig_bytes)*100) : 0;
      text = `Compressed ${esc(e.filename)}`;
      sub = `${fmtBytes(e.orig_bytes||0)} → ${fmtBytes(e.new_bytes||0)} (H.265)`;
      worth = `−${fmtBytes(e.saved_bytes||0)} (${pct}%)`;
    } else if (e.type === 'session') {
      text = `App opened`;
      sub = `${(e.total_photos||0).toLocaleString()} items · ${fmtBytes(e.library_bytes||0)} library · ${(e.clusters||0).toLocaleString()} dup groups · ${(e.total_dupes||0).toLocaleString()} dupes`;
    } else { text = e.type; }
    const when = (e.iso||'').replace('T',' ').slice(0,16);
    return `<div class="alog">
      <span class="ai">${icon}</span>
      <span class="at">${text}${sub?`<div style="font-size:11px;color:#475569;margin-top:2px">${sub}</div>`:''}</span>
      <span class="aw">${worth}</span>
      <span class="ad">${when}</span></div>`;
  }).join('') || '<div style="padding:24px;text-align:center;color:#334155">No activity logged yet.</div>';
}

// GitHub-style contribution heatmap of bytes reclaimed per day.
function renderHeatmap(byDay) {
  const WEEKS = 53;
  const today = new Date(); today.setHours(0,0,0,0);
  const start = new Date(today);
  start.setDate(start.getDate() - (WEEKS - 1) * 7 - today.getDay());   // back to a Sunday
  const lk = d => d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
  const max = Math.max(1, ...Object.values(byDay));
  const level = b => !b ? 0 : (b/max > 0.66 ? 4 : b/max > 0.33 ? 3 : b/max > 0.1 ? 2 : 1);
  const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  let cols = '', months = '', lastMonth = -1;
  for (let w = 0; w < WEEKS; w++) {
    // month label when a new month starts in this column's first day
    const colFirst = new Date(start); colFirst.setDate(start.getDate() + w*7);
    if (colFirst.getMonth() !== lastMonth) { months += `<span style="min-width:15px">${MON[colFirst.getMonth()]}</span>`; lastMonth = colFirst.getMonth(); }
    else months += `<span style="min-width:15px"></span>`;
    let cells = '';
    for (let dow = 0; dow < 7; dow++) {
      const d = new Date(start); d.setDate(start.getDate() + w*7 + dow);
      if (d > today) { cells += '<div class="hm-cell future"></div>'; continue; }
      const b = byDay[lk(d)] || 0;
      const tip = `${lk(d)} — ${b ? fmtBytes(b) + ' reclaimed' : 'no activity'}`;
      cells += `<div class="hm-cell lvl${level(b)}" data-tip="${tip}"></div>`;
    }
    cols += `<div class="hm-col">${cells}</div>`;
  }
  const chart = document.getElementById('audit-chart');
  chart.innerHTML =
    `<div class="hm-months">${months}</div>
     <div class="hm-body">
       <div class="hm-dows"><span></span><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span></div>
       <div class="hm-grid">${cols}</div>
     </div>
     <div class="hm-legend">Less
       <span class="hm-cell lvl0"></span><span class="hm-cell lvl1"></span><span class="hm-cell lvl2"></span><span class="hm-cell lvl3"></span><span class="hm-cell lvl4"></span>
     More</div>`;

  // Instant custom tooltip (native title is slow on 12px cells)
  const tip = document.getElementById('hm-tip');
  let tipTimer = null;
  const showTip = (cell, x, y) => {
    tip.textContent = cell.dataset.tip;
    tip.classList.add('show');
    tip.style.left = Math.min(x + 12, window.innerWidth - 160) + 'px';
    tip.style.top  = Math.max(y - 30, 8) + 'px';
  };
  chart.onmousemove = ev => {
    const cell = ev.target.closest && ev.target.closest('.hm-cell[data-tip]');
    if (cell) showTip(cell, ev.clientX, ev.clientY); else tip.classList.remove('show');
  };
  chart.onmouseleave = () => tip.classList.remove('show');
  // Touch / tap: show the detail near the tap, auto-hide after a moment.
  chart.onclick = ev => {
    const cell = ev.target.closest && ev.target.closest('.hm-cell[data-tip]');
    if (!cell) return;
    const r = cell.getBoundingClientRect();
    showTip(cell, r.left, r.top);
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => tip.classList.remove('show'), 2500);
  };
}

async function loadFiles() {
  document.getElementById('loading').style.display = '';
  document.getElementById('loading').innerHTML = '<span class="spinner"></span>Loading file list…';
  try {
    allFiles = await fetch('/files').then(r => r.json());
  } catch(e) { allFiles = []; }
  filesMaxSize = allFiles.reduce((m, f) => Math.max(m, f.size), 1);
  filesByUuid.clear();
  allFiles.forEach(f => {
    if (decisions[f.uuid] === undefined) decisions[f.uuid] = 'keep';
    photoMeta[f.uuid] = { size: f.size };
    filesByUuid.set(f.uuid, f);
  });
  filesLoaded = true;
  document.getElementById('loading').style.display = 'none';
  applyFilesFilters();
}

function setFFilter(key, val, el) {
  fFilters[key] = val;
  el.closest('.filter-group').querySelectorAll('.fchip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  applyFilesFilters();
}
function setFSort(v) { fSort = v; applyFilesFilters(); }

function applyFilesFilters() {
  let list = allFiles.filter(f => {
    const src = f.source || 'library';
    if (fFilters.source === 'all') { if (src !== 'library') return false; }
    else if (src !== fFilters.source) return false;
    if (fFilters.type === 'videos' && !f.is_video) return false;
    if (fFilters.type === 'photos' && (f.is_video || f.is_raw)) return false;
    if (fFilters.type === 'raw'    && !f.is_raw) return false;
    return true;
  });
  if      (fSort === 'size')     list.sort((a,b) => b.size - a.size);
  else if (fSort === 'size-asc') list.sort((a,b) => a.size - b.size);
  else if (fSort === 'date-new') list.sort((a,b) => (b.date||'').localeCompare(a.date||''));
  else if (fSort === 'date-old') list.sort((a,b) => (a.date||'').localeCompare(b.date||''));
  else if (fSort === 'name')     list.sort((a,b) => a.filename.localeCompare(b.filename));
  filesView = list;
  clusterMap['__files__'] = list;   // lightbox navigates this list
  renderFiles();
  renderFilesStats();
  if (typeof updateSelCount === 'function') updateSelCount();
}

function renderFilesStats() {
  const total = filesView.reduce((s,f) => s + f.size, 0);
  document.getElementById('s-files').textContent = filesView.length.toLocaleString();
  document.getElementById('s-files-size').textContent = fmtBytes(total);
}

function renderFiles() {
  const wrap = document.getElementById('files-wrap');
  const empty = document.getElementById('empty-msg');
  if (filesObserver) filesObserver.disconnect();
  if (!filesView.length) { wrap.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  const frag = document.createDocumentFragment();
  filesView.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'frow-ph'; el.dataset.idx = i; el.style.minHeight = '65px';
    frag.appendChild(el);
  });
  wrap.innerHTML = ''; wrap.appendChild(frag);
  filesObserver = new IntersectionObserver(ents => {
    ents.forEach(e => {
      if (e.isIntersecting && !e.target.dataset.done) {
        e.target.dataset.done = '1';
        { const i = +e.target.dataset.idx; paintFileRow(e.target, filesView[i], i); }
        filesObserver.unobserve(e.target);
      }
    });
  }, { rootMargin: '400px' });
  wrap.querySelectorAll('.frow-ph').forEach(el => filesObserver.observe(el));
}

function fileActionOf(uuid) {
  const d = decisions[uuid];
  return d === 'trash' ? 'delete' : d === 'compress' ? 'compress' : 'keep';
}

function paintFileRow(el, f, idx) {
  const act = fileActionOf(f.uuid);
  const src = f.source || 'library';
  const pct = Math.max(2, Math.round(f.size / filesMaxSize * 100));
  const barcls = f.is_video ? 'video' : f.is_raw ? 'raw' : '';
  const thumb = f.has_deriv
    ? `<img class="fthumb" src="/thumb/${f.uuid}" loading="lazy" onclick="openLightbox('${f.uuid}','__files__')">`
    : `<div class="fthumb-ph">no preview</div>`;
  const badges = [
    f.is_video ? '<span class="badge b-video">VIDEO</span>' : '',
    f.is_raw   ? '<span class="badge b-raw">RAW</span>' : '',
    src === 'shared' ? '<span class="badge b-shared">SHARED</span>' : '',
    src === 'hidden' ? '<span class="badge b-hidden">HIDDEN</span>' : '',
    `<span class="badge" style="background:#131720;color:#64748b;border:1px solid #1e2535">.${f.ext}</span>`,
  ].filter(Boolean).join('');
  // Compress only applies to your own library videos.
  const canCompress = f.is_video && (src === 'library');
  const compressBtn = canCompress
    ? `<button class="seg-btn seg-compress ${act==='compress'?'active':''}" data-act="compress" onclick="setFileAction('${f.uuid}','compress')">Compress</button>`
    : '';
  const sel = selectedFiles.has(f.uuid);
  el.className = 'frow src-' + src + (sel?' selected':'') + (act==='delete'?' r-delete':act==='compress'?' r-compress':'');
  el.dataset.uuid = f.uuid;
  el.dataset.idx = (idx !== undefined ? idx : filesView.indexOf(f));
  el.onclick = ev => onRowBodyClick(ev, f.uuid);   // tap row body to select (big touch target)
  el.innerHTML = `<input type="checkbox" class="fcheck" ${sel?'checked':''} onclick="onRowCheck(event,'${f.uuid}')">${thumb}
    <div class="fname"><div class="n" title="${esc(f.filename)}">${esc(f.filename)}</div><div class="d">${f.date}</div></div>
    <div class="fbar-wrap"><div class="fbar-track"><div class="fbar ${barcls}" style="width:${pct}%"></div></div></div>
    <div class="fsize">${f.size_str}</div>
    <div class="fbadges">${badges}</div>
    <div class="seg" data-uuid="${f.uuid}">
      <button class="seg-btn seg-keep ${act==='keep'?'active':''}" data-act="keep" onclick="setFileAction('${f.uuid}','keep')">Keep</button>
      ${compressBtn}
      <button class="seg-btn seg-delete ${act==='delete'?'active':''}" data-act="delete" onclick="setFileAction('${f.uuid}','delete')">Delete</button>
    </div>`;
}

// ── Multi-select for Large Files ──────────────────────────────────────────────
const selectedFiles = new Set();
let lastCheckedIdx = -1;
let rangeMode = false;   // touch-friendly range select (no shift key needed)

// Unified selection: range when shift-clicked (desktop) or rangeMode on (touch),
// otherwise toggle the single row.
function selectRow(uuid, idx, ev) {
  const wantRange = ((ev && ev.shiftKey) || rangeMode) && lastCheckedIdx >= 0;
  if (wantRange) {
    const [a, b] = [Math.min(lastCheckedIdx, idx), Math.max(lastCheckedIdx, idx)];
    for (let i = a; i <= b; i++) {
      selectedFiles.add(filesView[i].uuid);
      markRowSelected(filesView[i].uuid, true);
    }
  } else {
    const on = !selectedFiles.has(uuid);
    if (on) selectedFiles.add(uuid); else selectedFiles.delete(uuid);
    markRowSelected(uuid, on);
  }
  lastCheckedIdx = idx;
  updateSelCount();
}

function onRowCheck(ev, uuid) {
  ev.stopPropagation();
  const row = ev.target.closest('.frow');
  selectRow(uuid, row ? +row.dataset.idx : filesView.findIndex(f => f.uuid === uuid), ev);
}

// Tap anywhere on the row (except the thumbnail, action control, or checkbox) to select.
function onRowBodyClick(ev, uuid) {
  if (ev.target.closest('.seg') || ev.target.closest('.fthumb') || ev.target.classList.contains('fcheck')) return;
  selectRow(uuid, +ev.currentTarget.dataset.idx, ev);
}

function toggleRangeMode() {
  rangeMode = !rangeMode;
  const b = document.getElementById('range-btn');
  if (b) { b.classList.toggle('active', rangeMode); }
  toast(rangeMode ? 'Range mode on — tap two rows to select everything between them' : 'Range mode off', 'ok');
}

function markRowSelected(uuid, on) {
  const row = document.querySelector(`.frow[data-uuid="${uuid}"]`);
  if (row) { row.classList.toggle('selected', on); const cb = row.querySelector('.fcheck'); if (cb) cb.checked = on; }
}

function toggleSelectAll(on) {
  selectedFiles.clear();
  if (on) filesView.forEach(f => selectedFiles.add(f.uuid));
  filesView.forEach(f => markRowSelected(f.uuid, on));
  updateSelCount();
}

function clearSelection() {
  selectedFiles.forEach(u => markRowSelected(u, false));
  selectedFiles.clear();
  document.getElementById('sel-all-cb').checked = false;
  updateSelCount();
}

function updateSelCount() {
  document.getElementById('sel-count').textContent = selectedFiles.size.toLocaleString() + ' selected';
  const cb = document.getElementById('sel-all-cb');
  cb.checked = selectedFiles.size > 0 && selectedFiles.size === filesView.length;
}

function applyToSelected(action) {
  if (!selectedFiles.size) { toast('Select some files first (tap the rows).', 'err'); return; }
  let n = 0, skipped = 0;
  selectedFiles.forEach(uuid => {
    const f = filesByUuid.get(uuid);
    let a = action;
    if (a === 'compress' && !(f && f.is_video && (f.source||'library') === 'library')) { a = 'keep'; if (action==='compress') skipped++; }
    decisions[uuid] = a === 'delete' ? 'trash' : a;
    n++;
  });
  rebuildStats();
  renderFiles();
  scheduleSave();
  const verb = action === 'compress' ? 'to compress' : action === 'delete' ? 'to delete' : 'to keep';
  toast(`${n.toLocaleString()} ${verb}${skipped?` (${skipped} not compressible → kept)`:''}`, 'ok');
}

function setFileAction(uuid, action) {
  decisions[uuid] = action === 'delete' ? 'trash' : action;  // keep|compress|trash
  // update the row in place
  const row = document.querySelector(`.frow[data-uuid="${uuid}"]`);
  if (row) {
    row.classList.remove('r-delete', 'r-compress');
    if (action === 'delete') row.classList.add('r-delete');
    else if (action === 'compress') row.classList.add('r-compress');
    row.querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('active', b.dataset.act === action));
  }
  rebuildStats();
  scheduleSave();
}

// Replace a file row's action control with a live compression status.
function setRowStatus(uuid, cls, text, pct) {
  const row = document.querySelector(`.frow[data-uuid="${uuid}"]`);
  if (!row) return;
  let st = row.querySelector('.row-status');
  if (!st) {
    const seg = row.querySelector('.seg');
    st = document.createElement('span');
    st.className = 'row-status';
    if (seg) seg.replaceWith(st); else row.appendChild(st);
  }
  if (cls === 'done') { row.classList.remove('r-compressing'); row.classList.add('compressed-done'); }
  st.className = 'row-status ' + cls;
  st.innerHTML = (pct !== undefined)
    ? `${text}<span class="row-mini"><i style="width:${pct}%"></i></span>`
    : text;
}

// ── Compression job ───────────────────────────────────────────────────────────
let compressPoll = null;

async function startCompress() {
  const uuids = Object.entries(decisions).filter(([,d]) => d === 'compress').map(([u]) => u);
  if (!uuids.length) return;
  if (!confirm(`Compress ${uuids.length} video(s) to H.265? Each is re-encoded, the smaller copy is imported, and the original is moved to Photos trash. This can take a while.`)) return;
  const res = await fetch('/compress', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({uuids}) }).then(r=>r.json());
  if (res.error) { toast(res.error, 'err'); return; }
  document.getElementById('compress-bar').classList.add('on');
  document.getElementById('compress-btn').disabled = true;
  if (compressPoll) clearInterval(compressPoll);
  compressPoll = setInterval(pollCompress, 1000);
  pollCompress();
}

async function pollCompress() {
  let s;
  try { s = await fetch('/compress-status', {cache:'no-store'}).then(r=>r.json()); }
  catch(e) { return; }
  const cbtn = document.getElementById('compress-btn');
  if (s.running) {
    document.getElementById('compress-bar').classList.add('on');
    if (cbtn) { cbtn.disabled = true; cbtn.textContent = 'Compressing…'; }
  }
  const overall = s.total ? Math.round(((s.done + (s.current_pct||0)/100) / s.total) * 100) : 0;
  document.getElementById('cp-fill').style.width = overall + '%';
  document.getElementById('cp-label').textContent =
    s.current ? `Compressing ${s.current}  (${s.done}/${s.total}, ${s.current_pct||0}%)` : `Preparing… (${s.done}/${s.total})`;
  document.getElementById('cp-saved').textContent = s.saved_bytes ? 'saved ' + fmtBytes(s.saved_bytes) : '';

  // Reflect status on the individual file rows.
  (s.trashed_uuids || []).forEach(u => setRowStatus(u, 'done', '✓ compressed'));
  if (s.current_uuid && s.running) {
    const row = document.querySelector(`.frow[data-uuid="${s.current_uuid}"]`);
    if (row) row.classList.add('r-compressing');
    setRowStatus(s.current_uuid, 'doing', `Compressing ${s.current_pct||0}%`, s.current_pct||0);
  }
  if (s.finished && !s.running) {
    clearInterval(compressPoll); compressPoll = null;
    const done = new Set(s.trashed_uuids || []);
    done.forEach(u => { delete decisions[u]; delete photoMeta[u]; });
    allFiles = allFiles.filter(f => !done.has(f.uuid));
    rebuildStats();   // re-enables the compress button based on remaining marks
    if (filesLoaded) applyFilesFilters();
    setTimeout(() => document.getElementById('compress-bar').classList.remove('on'), 3000);
    const errs = (s.errors||[]).length;
    toast(`Compressed ${done.size} video(s), saved ${fmtBytes(s.saved_bytes||0)}.${errs?' '+errs+' failed.':''}`, errs?'err':'ok');
  }
}

// On page load, if a compression job is already running on the server, show the
// progress bar and resume polling (survives refresh / reopening the tab).
async function resumeCompressIfRunning() {
  try {
    const s = await fetch('/compress-status', {cache:'no-store'}).then(r=>r.json());
    if (s.running) {
      document.getElementById('compress-bar').classList.add('on');
      if (!compressPoll) compressPoll = setInterval(pollCompress, 1000);
      pollCompress();
    }
  } catch(e) {}
}

// ── Hidden-album toggle (off by default, persisted) ───────────────────────────
let hiddenEnabled = localStorage.getItem('hiddenEnabled') === '1';

function setHiddenEnabled(on) {
  hiddenEnabled = on;
  localStorage.setItem('hiddenEnabled', on ? '1' : '0');
  applyHiddenSetting();
}

function applyHiddenSetting() {
  const cb = document.getElementById('hidden-cb');
  if (cb) cb.checked = hiddenEnabled;
  document.querySelectorAll('.hidden-chip').forEach(el => el.style.display = hiddenEnabled ? '' : 'none');
  if (!hiddenEnabled) {
    // If a Hidden filter was active, fall back to All.
    if (filters.source === 'hidden') {
      filters.source = 'all';
      document.querySelectorAll('#source-filter .fchip').forEach(c => c.classList.toggle('active', c.dataset.val === 'all'));
      applyFiltersAndSort();
    }
    if (fFilters.source === 'hidden') {
      fFilters.source = 'all';
      document.querySelectorAll('#f-source-filter .fchip').forEach(c => c.classList.toggle('active', c.dataset.val === 'all'));
      if (filesLoaded) applyFilesFilters();
    }
  }
}

applyHiddenSetting();
boot();
// Deep-link via URL hash: #files / #activity open that tab; include "blur" to
// blur all thumbnails (privacy — for screenshots / screen-sharing).
// Examples: #files  ·  #activity  ·  #blur  ·  #files-blur
(function () {
  const h = (location.hash || '').toLowerCase();
  if (h.includes('files'))         switchTab('files');
  else if (h.includes('activity')) switchTab('activity');
  if (h.includes('blur')) document.body.classList.add('blur-thumbs');
})();
</script>
</body>
</html>"""


# ─── Thumbnail cache (bounded LRU) ────────────────────────────────────────────

from collections import OrderedDict

_thumb_cache: "OrderedDict[str, bytes]" = OrderedDict()
_thumb_lock = threading.Lock()
THUMB_MAX_PX = 320          # 2× the 160px card = retina-ready, ~4× smaller than raw derivative
THUMB_CACHE_MAX = 1500      # cap entries so the always-on process can't balloon (~300–400 MB max)


def get_thumb_bytes(uuid: str, photo) -> bytes | None:
    """Return cached resized JPEG bytes, generating on first call. LRU-bounded."""
    with _thumb_lock:
        data = _thumb_cache.get(uuid)
        if data is not None:
            _thumb_cache.move_to_end(uuid)   # mark most-recently-used
            return data

    derivs = photo.path_derivatives if photo else None
    if not derivs:
        return None
    path = Path(derivs[0])
    if not path.exists():
        return None

    try:
        from PIL import Image
        with Image.open(path) as img:
            img.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=72, optimize=True)
            data = buf.getvalue()
    except Exception:
        return None

    with _thumb_lock:
        _thumb_cache[uuid] = data
        _thumb_cache.move_to_end(uuid)
        while len(_thumb_cache) > THUMB_CACHE_MAX:
            _thumb_cache.popitem(last=False)   # evict least-recently-used
    return data


def prewarm_thumbnails():
    """Background thread: pre-generate thumbnails up to the cache cap so the most
    likely first views are instant without overfilling memory."""
    count = 0
    for cluster in CLUSTER_DATA:
        if count >= THUMB_CACHE_MAX:
            break
        for p in cluster["photos"]:
            if count >= THUMB_CACHE_MAX:
                break
            uuid = p["uuid"]
            if uuid not in _thumb_cache:
                photo = PHOTOS_BY_UUID.get(uuid)
                if photo:
                    get_thumb_bytes(uuid, photo)
                    count += 1
    print(f"  Thumbnail cache warmed: {count} (cap {THUMB_CACHE_MAX}, {len(_thumb_cache)} held)")


# ─── Shared processing state ──────────────────────────────────────────────────

STATE = {
    "phase": "starting",    # starting | loading | hashing | clustering | ready | error
    "detail": "Starting…",
    "progress": 0.0,        # 0..1 within current phase (hashing only)
    "ready": False,
    "total_photos": 0,
    "total_clusters": 0,
    "total_dupes": 0,
    "error": "",
}
STATE_LOCK = threading.Lock()


def set_state(**kw):
    with STATE_LOCK:
        STATE.update(kw)


def get_state():
    with STATE_LOCK:
        s = dict(STATE)
    # Augment with how many photos are currently marked for trash (from saved file)
    d = read_decisions()
    s["marked_trash"] = sum(1 for v in d.values() if v == "trash")
    return s


# ─── Server ───────────────────────────────────────────────────────────────────

CLUSTER_DATA = []
FILES_DATA = []
PHOTOS_BY_UUID = {}
_clusters_gzip: bytes = b""   # pre-compressed /clusters payload
_files_gzip: bytes = b""      # pre-compressed /files payload
PAYLOAD_LOCK = threading.Lock()   # guards in-place edits to the payloads above
LIBRARY_UUIDS = None          # UUIDs in your own library (deletable)
PK_ALL_UUIDS = None           # + shared-album + synced (openable, maybe not deletable)
HIDDEN_UUIDS = set()          # in the Hidden album (PhotoKit can't reach these at all)


def prune_payloads(removed):
    """Remove trashed UUIDs from the live cluster/file payloads and rebuild the
    gzip caches, so a refresh of an already-running server doesn't re-serve photos
    that were just deleted. Clusters that drop below 2 photos are dropped entirely
    (matching the client's own post-trash filtering)."""
    global CLUSTER_DATA, FILES_DATA, _clusters_gzip, _files_gzip
    removed = set(removed)
    if not removed:
        return
    with PAYLOAD_LOCK:
        new_clusters = []
        for c in CLUSTER_DATA:
            photos = [p for p in c["photos"] if p["uuid"] not in removed]
            if len(photos) >= 2:
                new_clusters.append({**c, "photos": photos})
        new_files = [f for f in FILES_DATA if f["uuid"] not in removed]
        CLUSTER_DATA = new_clusters
        FILES_DATA = new_files
        _clusters_gzip = gzip.compress(json.dumps(CLUSTER_DATA).encode(), compresslevel=6)
        _files_gzip = gzip.compress(json.dumps(FILES_DATA).encode(), compresslevel=6)


def photo_source(uuid):
    """library | shared | hidden | missing — drives badges, filters, actionability."""
    if LIBRARY_UUIDS is None:
        return "library"      # couldn't determine; treat as actionable
    if uuid in LIBRARY_UUIDS:
        return "library"
    if PK_ALL_UUIDS and uuid in PK_ALL_UUIDS:
        return "shared"
    if uuid in HIDDEN_UUIDS:
        return "hidden"       # in the Hidden album — manage it inside Photos
    return "missing"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?")[0]
        if not self._authed():
            if route == "/":
                self._send(401, "text/html; charset=utf-8", AUTH_PAGE.encode())
            else:
                self._send(401, "application/json", b'{"error":"unauthorized"}')
            return
        if route == "/":
            # If the token came via ?token=, drop a cookie so reloads work without it.
            extra = []
            if AUTH_TOKEN and self._query_token() == AUTH_TOKEN:
                extra = [("Set-Cookie", f"ps_token={AUTH_TOKEN}; Path=/; SameSite=Lax")]
            self._send(200, "text/html; charset=utf-8", HTML.encode(), extra_headers=extra)
        elif route == "/clusters":
            accepts_gz = "gzip" in self.headers.get("Accept-Encoding", "")
            if accepts_gz and _clusters_gzip:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", len(_clusters_gzip))
                self.end_headers()
                self.wfile.write(_clusters_gzip)
            else:
                self._send(200, "application/json", json.dumps(CLUSTER_DATA).encode())
        elif route == "/files":
            accepts_gz = "gzip" in self.headers.get("Accept-Encoding", "")
            if accepts_gz and _files_gzip:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", len(_files_gzip))
                self.end_headers()
                self.wfile.write(_files_gzip)
            else:
                self._send(200, "application/json", json.dumps(FILES_DATA).encode())
        elif route == "/status":
            self._send(200, "application/json", json.dumps(get_state()).encode())
        elif route == "/compress-status":
            with COMPRESS_LOCK:
                self._send(200, "application/json", json.dumps(dict(COMPRESS_STATE)).encode())
        elif route == "/audit":
            self._send(200, "application/json", json.dumps(audit_summary()).encode())
        elif route == "/report":
            self._send(200, "application/json", json.dumps(weekly_report()).encode())
        elif route == "/decisions":
            self._send(200, "application/json", json.dumps(read_decisions()).encode())
        elif route.startswith("/thumb/"):
            self._serve_thumb(route[7:])
        elif route.startswith("/open/"):
            uuid = route[6:]
            uuid = "".join(c for c in uuid if c in "0123456789ABCDEFabcdef-")
            # If PhotoKit has no handle on it, Photos can't navigate to it by id.
            if PK_ALL_UUIDS is not None and uuid not in PK_ALL_UUIDS:
                self._send(200, "application/json", json.dumps({
                    "ok": False,
                    "error": "Photos can't locate this item by ID (link-preview / "
                             "saved-from-Messages). Search by its date in Photos."
                }).encode())
                return
            # `spotlight media item id` is the correct Photos AppleScript verb
            # (same one osxphotos uses internally). `activate` pulls Photos to
            # the front even if it's already open but buried behind windows.
            script = (
                'tell application "Photos"\n'
                '  activate\n'
                f'  spotlight media item id "{uuid}"\n'
                'end tell'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                self._send(200, "application/json", b'{"ok":true}')
            else:
                print(f"/open failed: {result.stderr.strip()}")
                self._send(200, "application/json", json.dumps(
                    {"ok": False, "error": "Couldn't open this item in Photos."}).encode())
        elif route.startswith("/finddate/"):
            date = "".join(c for c in route[len("/finddate/"):] if c in "0123456789-")
            # Always copy the date to the clipboard (no permission needed) so the
            # user can paste into Photos' search even if GUI automation is blocked.
            try:
                subprocess.run(["pbcopy"], input=date.encode())
            except Exception:
                pass
            # Best-effort: drive Photos' search box to the date. Needs Accessibility
            # permission for the app; if not granted this fails and we fall back to
            # the clipboard.
            script = (
                'tell application "Photos" to activate\n'
                'delay 0.4\n'
                'tell application "System Events"\n'
                '  tell process "Photos"\n'
                '    keystroke "f" using {command down}\n'
                '    delay 0.3\n'
                f'    keystroke "{date}"\n'
                '    delay 0.3\n'
                '    key code 36\n'
                '  end tell\n'
                'end tell'
            )
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if result.returncode == 0:
                self._send(200, "application/json", json.dumps({"ok": True, "date": date}).encode())
            else:
                print(f"/finddate keystroke failed: {result.stderr.strip()}")
                self._send(200, "application/json", json.dumps({
                    "ok": False, "needsAccess": True, "date": date,
                    "error": "Couldn't drive Photos search (grant Accessibility to automate)."
                }).encode())
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        route = self.path.split("?")[0]
        if not self._authed():
            self._send(401, "application/json", b'{"error":"unauthorized"}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send(400, "application/json", b'{"error":"bad request"}')
            return
        if n > MAX_POST_BYTES:   # reject oversized bodies before reading (DoS guard)
            self._send(413, "application/json", b'{"error":"request too large"}')
            return
        try:
            body = json.loads(self.rfile.read(n)) if n else {}
        except (ValueError, json.JSONDecodeError):
            self._send(400, "application/json", b'{"error":"bad request body"}')
            return

        if route == "/trash":
            result = trash_photos(body.get("uuids", []))
            self._send(200, "application/json", json.dumps(result).encode())
        elif route == "/compress":
            result = start_compress(body.get("uuids", []))
            self._send(200, "application/json", json.dumps(result).encode())
        elif route == "/decisions":
            try:
                write_decisions(body)
                self._send(200, "application/json", b'{"ok":true}')
            except Exception as e:
                print(f"/decisions write failed: {e}")
                self._send(500, "application/json", b'{"error":"could not save"}')
        elif route == "/quit":
            self._send(200, "application/json", b'{"ok":true}')
            # Exit shortly after responding so the browser gets confirmation
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
        else:
            self._send(404, "application/json", b'{"error":"not found"}')

    def _send(self, code, ctype, body, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(body))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ── Optional token auth (active only when PHOTO_SAVER_TOKEN is set) ──
    def _query_token(self):
        if "?" in self.path:
            from urllib.parse import parse_qs
            return parse_qs(self.path.split("?", 1)[1]).get("token", [None])[0]
        return None

    def _authed(self):
        if not AUTH_TOKEN:
            return True
        if self._query_token() == AUTH_TOKEN:
            return True
        if self.headers.get("X-Auth-Token") == AUTH_TOKEN:
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith("ps_token=") and part[len("ps_token="):] == AUTH_TOKEN:
                return True
        return False

    def _serve_thumb(self, uuid):
        photo = PHOTOS_BY_UUID.get(uuid)
        if not photo:
            self._send(404, "text/plain", b"Not found"); return

        data = get_thumb_bytes(uuid, photo)
        if data:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "max-age=86400, immutable")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send(404, "text/plain", b"No thumbnail")

    def log_message(self, fmt, *args):
        pass


# ─── Library processing ───────────────────────────────────────────────────────

def process_library(args):
    """Load → hash → cluster → compress. Updates STATE throughout.
    Safe to call again (e.g. Rescan from the menu bar)."""
    global CLUSTER_DATA, FILES_DATA, PHOTOS_BY_UUID, _clusters_gzip, _files_gzip, _thumb_cache
    global LIBRARY_UUIDS, PK_ALL_UUIDS, HIDDEN_UUIDS
    try:
        import osxphotos
        set_state(phase="loading", detail="Loading Photos library…", progress=0, ready=False, error="")
        print("Loading Photos library…", flush=True)
        db = osxphotos.PhotosDB()
        photos = db.photos()

        # Classify each photo by how PhotoKit can reach it, instead of excluding:
        #   library = your own library          → open + delete
        #   shared  = shared album / synced      → open in Photos, can't delete here
        #   missing = PhotoKit has no handle     → can't open or delete (link-preview /
        #             saved-from-Messages orphans); shown so you can find them manually
        try:
            import Photos as PH
            def fetch_set(mask):
                opts = PH.PHFetchOptions.alloc().init()
                if mask is not None:
                    opts.setIncludeAssetSourceTypes_(mask)
                res = PH.PHAsset.fetchAssetsWithOptions_(opts)
                return {res.objectAtIndex_(i).localIdentifier().split("/")[0]
                        for i in range(res.count())}
            LIBRARY_UUIDS = fetch_set(None)        # PHAssetSourceTypeUserLibrary
            PK_ALL_UUIDS  = fetch_set(1 | 2 | 4)   # + CloudShared + iTunesSynced
            # Hidden-album photos are walled off from PhotoKit entirely (not even
            # fetchable by ID) — identify them via osxphotos so we can label them.
            HIDDEN_UUIDS = {p.uuid for p in photos if getattr(p, "hidden", False)}
            n_shared  = sum(1 for p in photos if p.uuid not in LIBRARY_UUIDS and p.uuid in PK_ALL_UUIDS)
            n_hidden  = sum(1 for p in photos if p.uuid not in PK_ALL_UUIDS and p.uuid in HIDDEN_UUIDS)
            n_missing = sum(1 for p in photos if p.uuid not in PK_ALL_UUIDS and p.uuid not in HIDDEN_UUIDS)
            print(f"  sources → library:{len(LIBRARY_UUIDS):,}  shared:{n_shared:,}  hidden:{n_hidden:,}  can't-locate:{n_missing:,}")
        except Exception as e:
            LIBRARY_UUIDS = PK_ALL_UUIDS = None
            print(f"  (PhotoKit source classification unavailable: {e})")

        PHOTOS_BY_UUID = {p.uuid: p for p in photos}
        # Heal stale marks: drop decisions for photos no longer in the library
        # (already trashed/emptied) so they can't resurrect on the next load.
        prune_decisions(PHOTOS_BY_UUID.keys())
        n_deriv = sum(1 for p in photos if p.path_derivatives)
        print(f"  {len(photos):,} photos/videos  ({n_deriv:,} have local thumbnails)")
        set_state(total_photos=len(photos))

        set_state(phase="hashing", detail=f"Hashing {len(photos):,} thumbnails…", progress=0)
        print(f"Hashing thumbnails (threshold={args.threshold}, window={args.window}s)…")
        cache = load_cache()
        hashes = hash_all_photos(photos, cache, rescan=args.rescan,
                                 progress_cb=lambda f: set_state(progress=f))
        save_cache(cache)
        print(f"  {len(hashes):,} hashed")

        set_state(phase="clustering", detail="Finding duplicate clusters…", progress=0)
        print("Finding duplicate clusters…")
        clusters = find_clusters(photos, hashes, args.threshold, args.window)
        CLUSTER_DATA = build_cluster_list(PHOTOS_BY_UUID, clusters, hashes)
        total_dupes = sum(len(c["photos"]) - 1 for c in CLUSTER_DATA)
        print(f"  {len(CLUSTER_DATA):,} clusters  ({total_dupes:,} potential duplicates)")

        raw = json.dumps(CLUSTER_DATA).encode()
        _clusters_gzip = gzip.compress(raw, compresslevel=6)
        print(f"  Cluster payload: {len(raw)//1024} KB → {len(_clusters_gzip)//1024} KB gzipped")

        # Large Files browser data
        FILES_DATA = build_files_list(photos)
        _files_gzip = gzip.compress(json.dumps(FILES_DATA).encode(), compresslevel=6)
        print(f"  Largest {len(FILES_DATA):,} files indexed for the Large Files browser")

        # Reset + re-warm thumbnail cache for the new cluster set
        with _thumb_lock:
            _thumb_cache.clear()
        threading.Thread(target=prewarm_thumbnails, daemon=True, name="thumb-prewarm").start()

        set_state(phase="ready", detail="Ready", progress=1.0, ready=True,
                  total_clusters=len(CLUSTER_DATA), total_dupes=total_dupes)
        lib_bytes = sum(p.original_filesize or 0 for p in photos)
        audit_log({"type": "session", "total_photos": len(photos),
                   "library_bytes": lib_bytes, "clusters": len(CLUSTER_DATA),
                   "total_dupes": total_dupes})
        print(f"\nReady → http://localhost:{PORT}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        set_state(phase="error", detail=f"Error: {e}", error=str(e), ready=False)


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rescan",    action="store_true", help="Ignore hash cache and recompute everything")
    parser.add_argument("--threshold", type=int, default=10, help="Max hamming distance to consider duplicate (default 10)")
    parser.add_argument("--window",    type=int, default=60, help="Time window in seconds for near-dup search (default 60)")
    parser.add_argument("--no-open",   action="store_true", help="Don't auto-open the browser")
    args = parser.parse_args()

    # When launched as a GUI app (the .app bundle), the framework Python would
    # otherwise show a stray icon in the Dock. Suppress it — the browser tab is
    # the "running" indicator. Harmless/no-op when run from a terminal.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyProhibited
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyProhibited
        )
    except Exception:
        pass

    # Start the web server immediately so the browser can show a live progress
    # screen (it polls /status). Library processing runs in the background.
    threading.Thread(target=process_library, args=(args,), daemon=True, name="process").start()
    if not args.no_open:
        subprocess.Popen(["open", f"http://localhost:{PORT}"])

    try:
        start_server()  # blocks, serving forever
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
