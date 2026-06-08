# Contributing and Development

Thanks for your interest! This is a **single-file** macOS app — `photo_saver.py` is a
Python HTTP server plus an embedded HTML/CSS/JS frontend in one string. The
authoritative source is
[`CONTRIBUTING.md`](https://github.com/joshtellr/icloud-photo-storage-saver/blob/main/CONTRIBUTING.md).

---

## Dev setup

```bash
git clone https://github.com/joshtellr/icloud-photo-storage-saver.git
cd icloud-photo-storage-saver
bash setup.sh                                 # installs deps (uv, osxphotos, pillow, ffmpeg, exiftool)
~/.local/bin/osxphotos run photo_saver.py     # open http://localhost:8421
```

Useful while developing:
- **Flags:** `--rescan`, `--threshold N`, `--window S`, `--no-open` ([[Configuration]]).
- **URL hashes:** `#files`, `#activity`, `#blur` (blurs thumbnails for screenshots).

---

## Guidelines

- Keep it **dependency-light** and **local-only** — no telemetry, no external calls at
  runtime. Privacy is a feature.
- It must only act on the user's **own** library (shared / hidden / unreachable photos
  are read-only). **Don't loosen that.**
- Destructive actions go through the **Photos trash**, never permanent deletion.
- Match the existing style in `photo_saver.py`. Test on a real Photos library.
- `bash build_dmg.sh` builds the distributable app/DMG.
- By contributing you agree your work is licensed under **GPL-3.0**.

---

## Tests

Unit + persistence tests live in `tests/`. They run **anywhere — no macOS or Photos
library required** — because `photo_saver.py` imports its macOS-only modules (`Photos`,
`AppKit`, `osxphotos`) **lazily**, so the module imports cleanly and its pure logic can
be exercised directly. The macOS-integration paths (trash/compress, HTTP) are tested by
faking PhotoKit and the `ffmpeg`/`exiftool`/`osascript` subprocess calls.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

### Coverage map

| Area | File | Functions under test |
|------|------|----------------------|
| Perceptual hashing | `test_hashing.py` | `dhash`, `hamming` |
| Duplicate clustering | `test_clustering.py` | `find_clusters` |
| Keep-the-best ranking | `test_scoring.py` | `score_photo`, `cluster_tightness`, `tightness_label` |
| Byte formatting | `test_formatting.py` | `fmt_bytes` |
| Payload builders | `test_build_lists.py` | `build_cluster_list`, `build_files_list` |
| Decisions + cache | `test_decisions.py` | `read/write/clear/prune_decisions`, `load/save_cache` |
| Audit log + metrics | `test_audit.py` | `audit_log`, `read_audit`, `audit_summary` |
| Weekly report | `test_weekly_report.py` | `weekly_report` |
| Source classification | `test_photo_source.py` | `photo_source` |
| Post-trash payload sync | `test_prune_payloads.py` | `prune_payloads` |
| HTTP layer | `test_http.py` | `Handler` routing, `_authed`/`_query_token`, POST hardening, `/open` & `/finddate` sanitization |
| Trash flow | `test_trash.py` | `trash_photos` (library-only filtering, already-gone healing, user-cancel, finalize) |
| Compress flow | `test_compress.py` | `start_compress`, `_video_duration`, `_compress_one` |

`test_decisions.py` and `test_prune_payloads.py` include regression coverage for the
"trashed photos reappearing after refresh" fix (commit `4ed2cf6`).

### Fixtures (`tests/conftest.py`)
- **`make_photo`** — factory for `FakePhoto`, a duck-typed stand-in for an osxphotos
  `PhotoInfo`.
- **`state_files`** — redirects the module's home-dir state files (`CACHE_FILE`,
  `DECISIONS_FILE`, `AUDIT_FILE`) into a tmp dir so tests never touch real state.
- **`fake_photos`** — installs a stub `Photos` module in `sys.modules` so PhotoKit paths
  run on Linux CI.

### Not yet covered (good first contributions)
- `get_thumb_bytes` LRU eviction and `prewarm_thumbnails` cache behavior.
- `process_library` orchestration (needs a fake osxphotos `PhotosDB`).
- The client-side JS in the embedded `HTML` string.

---

## Building a release

```bash
bash build_dmg.sh   # produces dist/iCloudPhotoStorageSaver.dmg
```

Attach the resulting DMG to a **GitHub Release**. A signed + notarized build (so the app
opens without the right-click step) needs an Apple Developer ID — a planned future
improvement.

---

## Repo layout

| Path | What it is |
|------|------------|
| `photo_saver.py` | The whole app (server + embedded UI) |
| `tests/` | Pytest suite (runs on Linux CI) |
| `extras/` | Standalone scripts: `photos_compress.py`, `photos_delete_originals.py`, `photos_monitor.py` |
| `setup.sh` | Installs deps + builds the `.app` |
| `install-agent.sh` | Launch-agent install/uninstall for always-on |
| `build_dmg.sh` | Builds the distributable DMG |
| `make_app_icon.py`, `make_dmg_background.py` | Asset generation |
| `docs/` | Screenshots + icons used by the README/wiki |

---

### See also

- [[Architecture]] — how the pieces fit together.
- [[Security]] — invariants contributors must preserve.
- [[Configuration]] — flags and env vars you'll use while developing.
