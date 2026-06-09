"""Tests for the compression flow: start_compress (544), _video_duration (441),
and _compress_one (451).

The real pipeline shells out to osxphotos/ffmpeg/exiftool/osascript and imports
PhotoKit; here those are faked so the guard logic and safety checks run on any
platform. We focus on the decisions that protect user data:
  * only library videos are eligible, and only one job runs at a time
  * a corrupt/empty export aborts
  * a re-encode that isn't actually smaller is rejected (never trashes the good one)
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import photo_saver as ps


# ── start_compress ───────────────────────────────────────────────────────────

@pytest.fixture
def no_thread(monkeypatch):
    """Capture Thread construction instead of running the worker."""
    created = []

    class FakeThread:
        def __init__(self, target=None, args=(), **kw):
            self.target, self.args = target, args
            created.append(self)

        def start(self):
            pass

    monkeypatch.setattr(ps.threading, "Thread", FakeThread)
    return created


def test_start_compress_rejects_when_already_running(monkeypatch):
    monkeypatch.setitem(ps.COMPRESS_STATE, "running", True)
    result = ps.start_compress(["a"])
    assert "error" in result


def test_start_compress_selects_only_library_videos(no_thread, monkeypatch, make_photo):
    monkeypatch.setitem(ps.COMPRESS_STATE, "running", False)
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"vid", "shared_vid"})
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {
        "vid": make_photo("vid", ismovie=True),
        "photo": make_photo("photo", ismovie=False),          # not a video
        "shared_vid": make_photo("shared_vid", ismovie=True),  # video but...
    })
    # Mark shared_vid as outside the library to prove the library filter.
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"vid"})

    result = ps.start_compress(["vid", "photo", "shared_vid"])

    assert result == {"ok": True, "count": 1}
    assert no_thread[-1].args == (["vid"],)


def test_start_compress_errors_when_no_eligible_videos(no_thread, monkeypatch, make_photo):
    monkeypatch.setitem(ps.COMPRESS_STATE, "running", False)
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", None)
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {"photo": make_photo("photo", ismovie=False)})
    result = ps.start_compress(["photo"])
    assert "error" in result
    assert no_thread == []   # never spawned a worker


# ── _video_duration ──────────────────────────────────────────────────────────

def test_video_duration_parses_ffprobe_output(monkeypatch):
    monkeypatch.setattr(ps.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="12.5\n", returncode=0))
    assert ps._video_duration("/x.mov") == 12.5


def test_video_duration_handles_garbage(monkeypatch):
    monkeypatch.setattr(ps.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="", returncode=1))
    assert ps._video_duration("/x.mov") == 0


# ── _compress_one ────────────────────────────────────────────────────────────

@pytest.fixture
def compress_env(monkeypatch, fake_photos):
    """Fake out the export/ffmpeg/exiftool/import pipeline.

    cfg drives behavior:
      create_source: whether the osxphotos export produces a file
      dst_size:      bytes the fake ffmpeg writes for the re-encoded output
      import_rc:     return code of the Photos import step
    """
    cfg = {"create_source": True, "dst_size": 10, "import_rc": 0}
    monkeypatch.setattr(ps, "_video_duration", lambda p: 10.0)

    def fake_run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "export" in cmd:
            if cfg["create_source"]:
                (Path(cmd[-1]) / "orig.mov").write_bytes(b"x" * 1000)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == "osascript":
            return SimpleNamespace(returncode=cfg["import_rc"], stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="", stderr="")  # exiftool, etc.

    class FakePopen:
        def __init__(self, cmd, *a, **k):
            Path(str(cmd[-1])).write_bytes(b"y" * cfg["dst_size"])
            self.stdout = iter([])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    monkeypatch.setattr(ps.subprocess, "Popen", FakePopen)
    return cfg


def test_compress_one_returns_bytes_saved(compress_env, fake_photos, make_photo):
    compress_env["dst_size"] = 400          # re-encode smaller than the 1000-byte original
    fake_photos.add_asset("v")
    photo = make_photo("v", original_filesize=1000, ismovie=True, original_filename="v.mov")
    saved = ps._compress_one("v", photo)
    assert saved == 600
    assert len(fake_photos.deleted) == 1    # original trashed after a successful re-encode


def test_compress_one_aborts_on_empty_export(compress_env, make_photo):
    compress_env["create_source"] = False
    photo = make_photo("v", original_filesize=1000, ismovie=True)
    with pytest.raises(RuntimeError, match="export failed"):
        ps._compress_one("v", photo)


def test_compress_one_rejects_when_not_smaller(compress_env, fake_photos, make_photo):
    compress_env["dst_size"] = 2000         # bigger than the original → must be rejected
    fake_photos.add_asset("v")
    photo = make_photo("v", original_filesize=1000, ismovie=True)
    with pytest.raises(RuntimeError, match="not smaller"):
        ps._compress_one("v", photo)
    assert fake_photos.deleted == []        # original must NOT be trashed


def test_compress_one_raises_on_failed_import(compress_env, make_photo):
    compress_env["dst_size"] = 400
    compress_env["import_rc"] = 1
    photo = make_photo("v", original_filesize=1000, ismovie=True)
    with pytest.raises(RuntimeError, match="import failed"):
        ps._compress_one("v", photo)


# ── compress_worker ──────────────────────────────────────────────────────────

@pytest.fixture
def reset_compress_state():
    snapshot = dict(ps.COMPRESS_STATE)
    yield
    ps.COMPRESS_STATE.clear()
    ps.COMPRESS_STATE.update(snapshot)


def test_compress_worker_aggregates_results(reset_compress_state, state_files, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {
        "ok": make_photo("ok", original_filesize=1000, original_filename="ok.mov"),
        "bad": make_photo("bad", original_filesize=500, original_filename="bad.mov"),
        # "missing" is intentionally absent from PHOTOS_BY_UUID
    })

    def fake_compress_one(uuid, photo):
        if uuid == "bad":
            raise RuntimeError("ffmpeg failed")
        return 600

    monkeypatch.setattr(ps, "_compress_one", fake_compress_one)

    ps.compress_worker(["ok", "bad", "missing"])

    st = ps.COMPRESS_STATE
    assert st["running"] is False and st["finished"] is True
    assert st["done"] == 3
    assert st["saved_bytes"] == 600
    assert st["trashed_uuids"] == ["ok"]
    assert len(st["errors"]) == 2          # "bad" failed + "missing" not found


def test_compress_worker_logs_audit_for_success(reset_compress_state, state_files, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID",
                        {"ok": make_photo("ok", original_filesize=1000, original_filename="ok.mov")})
    monkeypatch.setattr(ps, "_compress_one", lambda u, p: 700)

    ps.compress_worker(["ok"])

    events = [e for e in ps.read_audit() if e.get("type") == "compress"]
    assert len(events) == 1
    assert events[0]["saved_bytes"] == 700
    assert events[0]["orig_bytes"] == 1000
