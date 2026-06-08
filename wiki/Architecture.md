# Architecture

iCloud Photo Storage Saver is a **single-file** macOS app: `photo_saver.py` is a Python
stdlib HTTP server with an embedded HTML/CSS/JS frontend in one string. There's no
build step for the web UI and no runtime framework — by design, to stay dependency-light
and easy to audit.

```
Browser (localhost:8421)
        │  HTTP (GET/POST, optional token)
        ▼
ThreadingHTTPServer  ── Handler (routing, auth, sanitization)
        │
        ├── osxphotos ........ reads Photos DB + thumbnail derivatives (no iCloud download)
        ├── Pillow ........... decode + resize thumbnails (LRU-cached in memory)
        ├── PhotoKit (PyObjC)  classify source, move items to Photos trash
        ├── ffmpeg ........... H.265 re-encode (Compress)
        └── exiftool ......... copy metadata into re-encoded video
        │
        ▼
State files in $HOME (hash cache, decisions, audit log)
```

---

## Request flow

1. The browser loads the single embedded HTML page from `GET /`.
2. The page calls JSON endpoints (`/clusters`, `/files`, `/status`, `/audit`, …).
3. Destructive actions are `POST`s (`/trash`, `/compress`, …).
4. If `PHOTO_SAVER_TOKEN` is set, the `Handler` enforces the token on every request
   before routing (see [[Security]]).

### HTTP endpoints

**GET**

| Route | Purpose |
|-------|---------|
| `/` | The embedded single-page UI |
| `/clusters` | Duplicate clusters payload |
| `/files` | Large-files list payload |
| `/status` | Scan/processing status |
| `/compress-status` | Background compression progress |
| `/audit` | Audit-log entries |
| `/report` | Weekly report data |
| `/decisions` | Persisted keep/trash decisions |
| `/thumb/<uuid>` | A resized thumbnail (LRU-cached) |
| `/open/<uuid>` | Open an item in the Photos app (AppleScript, sanitized) |
| `/finddate/<…>` | Drive a date search in Photos (AppleScript, sanitized) |

**POST**

| Route | Purpose |
|-------|---------|
| `/trash` | Move selected items to the Photos trash |
| `/compress` | Start background H.265 compression of selected videos |
| `/decisions` | Persist keep/trash decisions |
| `/quit` | Shut the server down |

Routing lives in `Handler.do_GET` / `Handler.do_POST`; token checks in `_authed` /
`_query_token`.

---

## Key modules in `photo_saver.py`

### Duplicate detection
- `dhash(img, size=8)` — 64-bit difference hash of a thumbnail.
- `hamming(h1, h2)` — bit distance between two hashes.
- `find_clusters(photos, hashes, threshold, time_window_sec)` — union-find clustering
  over exact + near matches within the time window.
- `score_photo(photo)` — ranks items so the "keeper" can be suggested.
- `cluster_tightness(uuids, hashes)` + `tightness_label(t)` — produce the
  identical / near-identical / similar / loose confidence label.

### Payload builders
- `build_cluster_list(...)` — Duplicates tab payload.
- `build_files_list(photos)` — Large Files tab payload.
- `fmt_bytes(b)` — human-readable sizes.
- `prune_payloads(removed)` — keep payloads in sync after items are trashed.

### Source + actions (PhotoKit)
- `photo_source(uuid)` — classifies an item (your library / shared / hidden /
  unreachable); gates what can be deleted.
- `trash_photos(uuids)` — library-only filtering, already-gone healing, user-cancel and
  finalize handling.

### Compression
- `start_compress(uuids)` — kicks off background compression.
- `_video_duration(...)`, `_compress_one(...)` — export → re-encode (ffmpeg, H.265) →
  copy metadata (exiftool) → re-import smaller copy → trash original, with a
  "not actually smaller" guard.

### Persistence + metrics
- `load_cache()` / `save_cache(cache)` — perceptual-hash cache.
- `read_decisions()` / `write_decisions()` / `clear_decisions()` / `prune_decisions()` —
  keep/trash choices.
- `audit_log(event)` / `read_audit()` / `audit_summary()` — the activity log + totals.
- `weekly_report()` — the weekly rollup.

### Thumbnails
- `get_thumb_bytes(uuid, photo)` — resized bytes, LRU-cached in memory.
- `prewarm_thumbnails()` — warms the cache.

### Orchestration & server
- `process_library(args)` — top-level scan/build orchestration.
- `ThreadingHTTPServer(("127.0.0.1", PORT), Handler)` on `PORT = 8421`.

---

## Persistence files

All plain files in `$HOME`, never uploaded:

| File | Contents |
|------|----------|
| `~/.photos_dedup_*` | Perceptual-hash cache + dedup state |
| `~/.photo_saver_audit.jsonl` | Audit log (one JSON object per line) |
| (decisions file) | Persisted keep/trash decisions |

The HTTP test suite (`tests/conftest.py`) redirects `CACHE_FILE`, `DECISIONS_FILE`, and
`AUDIT_FILE` into a tmp dir so tests never touch your real state.

---

## Why it can't run on Linux / Docker

Analysis is portable, but **source classification and deletion go through PhotoKit**,
and the app reads a real **Apple Photos library**. Those are macOS-only. The macOS
imports (`Photos`, `AppKit`, `osxphotos`) are done **lazily**, which is what lets the
pure-logic unit tests import the module and run on Linux CI (see
[[Contributing and Development]]).

---

### See also

- [[Features]] — what each tool does for the user.
- [[Security]] — the trust model behind these endpoints.
- [[Contributing and Development]] — tests mapped to these functions.
