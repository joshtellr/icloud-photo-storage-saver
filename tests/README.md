# Tests

Unit + persistence tests for `photo_saver.py`. They run anywhere — no macOS or
Photos library required — because the app imports its macOS-only modules
(`Photos`, `AppKit`, `osxphotos`) lazily, so the module imports cleanly and its
pure logic can be exercised directly.

## Run

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## What's covered

| Area | File | Function(s) under test |
|------|------|------------------------|
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

`test_decisions.py` and `test_prune_payloads.py` include regression coverage for
the "trashed photos reappearing after refresh" fix (commit 4ed2cf6).

## Fixtures

`conftest.py` provides:

- **`make_photo`** — a factory for `FakePhoto`, a duck-typed stand-in for an
  osxphotos `PhotoInfo` exposing only the attributes the code reads.
- **`state_files`** — redirects the module's home-directory state files
  (`CACHE_FILE`, `DECISIONS_FILE`, `AUDIT_FILE`) into a tmp dir.

The HTTP tests start a real `ThreadingHTTPServer` on an ephemeral port and stub
the macOS-only `osascript`/`pbcopy` subprocess calls so the auth and
sanitization paths run on any platform.

## Not yet covered (good next steps)

- macOS integration (`trash_photos`, `start_compress`, `_compress_one`) — needs
  `subprocess`/PhotoKit mocking; mark such tests `@pytest.mark.macos`.
- `get_thumb_bytes` LRU eviction and `prewarm_thumbnails` cache behavior.
