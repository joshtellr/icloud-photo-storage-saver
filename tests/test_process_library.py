"""Integration test for process_library (photo_saver:2517) — the load → hash →
cluster → index orchestration that backs every request.

osxphotos is faked (a stub PhotosDB returning FakePhotos with real derivative
images so the hashing pipeline genuinely runs); PhotoKit source classification
is left to fail naturally on Linux, exercising the graceful-fallback path where
LIBRARY_UUIDS is None.
"""
import random
import sys
import types
from types import SimpleNamespace

from PIL import Image
import pytest

import photo_saver as ps


def _make_image(path, seed):
    # Seeded noise so distinct seeds yield distinct perceptual hashes (a flat /
    # solid image would dhash to all-zero and collide with every other one).
    rnd = random.Random(seed)
    img = Image.new("L", (64, 64))
    img.putdata([rnd.randint(0, 255) for _ in range(64 * 64)])
    img.save(path, "JPEG")
    return str(path)


@pytest.fixture
def fake_osxphotos(monkeypatch):
    photos = {"list": []}

    class PhotosDB:
        def __init__(self, *a, **k):
            pass

        def photos(self):
            return photos["list"]

    mod = types.ModuleType("osxphotos")
    mod.PhotosDB = PhotosDB
    monkeypatch.setitem(sys.modules, "osxphotos", mod)
    return photos


@pytest.fixture(autouse=True)
def quiet_prewarm(monkeypatch):
    # The orchestration spawns a prewarm thread; the thumbnail layer has its own
    # tests, so stub it out to keep this test focused and deterministic.
    monkeypatch.setattr(ps, "prewarm_thumbnails", lambda: None)


def _args(**kw):
    return SimpleNamespace(threshold=kw.get("threshold", 10),
                           window=kw.get("window", 60),
                           rescan=kw.get("rescan", False))


def test_process_library_reaches_ready_and_finds_duplicates(
    fake_osxphotos, state_files, tmp_path, monkeypatch, make_photo
):
    # Two photos sharing the exact same derivative image → identical dhash →
    # they must land in one duplicate cluster.
    img = _make_image(tmp_path / "shared.jpg", seed=1)
    from datetime import datetime
    d = datetime(2026, 6, 8, 12, 0, 0)
    fake_osxphotos["list"] = [
        make_photo("a", original_filesize=900, path_derivatives=[img], date=d,
                   original_filename="a.jpg"),
        make_photo("b", original_filesize=300, path_derivatives=[img], date=d,
                   original_filename="b.jpg"),
        make_photo("c", original_filesize=100,
                   path_derivatives=[_make_image(tmp_path / "other.jpg", seed=2)],
                   date=d, original_filename="c.jpg"),
    ]

    ps.process_library(_args())

    assert ps.STATE["phase"] == "ready"
    assert ps.STATE["ready"] is True
    # a & b cluster; c is unique → exactly one cluster of two.
    assert len(ps.CLUSTER_DATA) == 1
    assert {p["uuid"] for p in ps.CLUSTER_DATA[0]["photos"]} == {"a", "b"}
    # Largest-files index holds all three.
    assert {f["uuid"] for f in ps.FILES_DATA} == {"a", "b", "c"}


def test_process_library_writes_session_audit_event(
    fake_osxphotos, state_files, tmp_path, monkeypatch, make_photo
):
    fake_osxphotos["list"] = [
        make_photo("a", original_filesize=500,
                   path_derivatives=[_make_image(tmp_path / "a.jpg", seed=3)]),
    ]
    ps.process_library(_args())
    sessions = [e for e in ps.read_audit() if e.get("type") == "session"]
    assert len(sessions) == 1
    assert sessions[0]["total_photos"] == 1


def test_process_library_handles_empty_library(
    fake_osxphotos, state_files, monkeypatch
):
    fake_osxphotos["list"] = []
    ps.process_library(_args())
    assert ps.STATE["phase"] == "ready"
    assert ps.CLUSTER_DATA == []
    assert ps.FILES_DATA == []


def test_process_library_records_error_on_failure(
    fake_osxphotos, state_files, monkeypatch
):
    # PhotosDB raising should land in the error state, not crash.
    def boom(*a, **k):
        raise RuntimeError("db unavailable")
    sys.modules["osxphotos"].PhotosDB = boom
    ps.process_library(_args())
    assert ps.STATE["phase"] == "error"
    assert "db unavailable" in ps.STATE["error"]
