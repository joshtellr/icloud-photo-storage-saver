"""Tests for trash_photos (photo_saver:356) — the PhotoKit deletion flow.

This is the highest-stakes path in the app (it deletes photos), so the
library-only filtering, all-or-nothing batching, "already gone" healing, and
user-cancel behavior all get pinned. PhotoKit is faked via the `fake_photos`
fixture; the persistence side effects are redirected to a tmp dir.
"""
import pytest

import photo_saver as ps


@pytest.fixture(autouse=True)
def isolate_payloads(monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [])
    monkeypatch.setattr(ps, "FILES_DATA", [])
    monkeypatch.setattr(ps, "_clusters_gzip", b"")
    monkeypatch.setattr(ps, "_files_gzip", b"")
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {})


def test_empty_input_is_a_noop():
    assert ps.trash_photos([]) == {"trashed": 0, "failed": 0, "trashed_uuids": []}


def test_returns_error_when_photokit_unavailable(monkeypatch):
    # No fake_photos fixture here, so `import Photos` fails as it would on Linux.
    import sys
    monkeypatch.setitem(sys.modules, "Photos", None)  # force ImportError
    result = ps.trash_photos(["a"])
    assert "error" in result


def test_deletes_live_library_asset(fake_photos, monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    fake_photos.add_asset("a")
    result = ps.trash_photos(["a"])
    assert result["trashed"] == 1
    assert result["trashed_uuids"] == ["a"]
    assert len(fake_photos.deleted) == 1


def test_skips_non_library_photos(fake_photos, monkeypatch):
    # "b" is shared / not in your library → must not be batched for deletion.
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    fake_photos.add_asset("a")
    fake_photos.add_asset("b")
    result = ps.trash_photos(["a", "b"])
    assert result["trashed"] == 1
    assert result["failed"] == 1            # "b" skipped
    assert "a" in result["trashed_uuids"]
    assert "b" not in result["trashed_uuids"]


def test_already_gone_photo_is_healed_not_failed(fake_photos, monkeypatch):
    # In the library but no live asset (already in Recently Deleted).
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    # no add_asset("a") → firstObject() returns None → "gone"
    result = ps.trash_photos(["a"])
    assert result["trashed"] == 0
    assert result["already_gone"] == 1
    assert result["trashed_uuids"] == ["a"]
    assert fake_photos.deleted == []        # nothing live to delete


def test_user_cancel_deletes_nothing(fake_photos, monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    fake_photos.add_asset("a")
    fake_photos.set_ok(False)               # "Don't Allow"
    result = ps.trash_photos(["a"])
    assert result["trashed"] == 0
    assert result["failed"] == 1
    assert fake_photos.deleted == []


def test_success_clears_decisions_and_prunes_payloads(fake_photos, state_files, monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "FILES_DATA", [{"uuid": "a"}, {"uuid": "z"}])
    fake_photos.add_asset("a")
    ps.write_decisions({"a": "trash", "z": "keep"})

    ps.trash_photos(["a"])

    # Mark for the trashed photo is forgotten (regression: no reappear on refresh)
    assert ps.read_decisions() == {"z": "keep"}
    # And it's dropped from the live payload
    assert [f["uuid"] for f in ps.FILES_DATA] == ["z"]


def test_success_writes_audit_event(fake_photos, state_files, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {"a": make_photo("a", original_filesize=1234)})
    fake_photos.add_asset("a")

    ps.trash_photos(["a"])

    events = ps.read_audit()
    assert len(events) == 1
    assert events[0]["type"] == "trash"
    assert events[0]["count"] == 1
    assert events[0]["bytes"] == 1234


def test_unclassified_library_does_not_skip(fake_photos, monkeypatch):
    # LIBRARY_UUIDS None → classification unavailable → everything is actionable.
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", None)
    fake_photos.add_asset("a")
    fake_photos.add_asset("b")
    result = ps.trash_photos(["a", "b"])
    assert result["trashed"] == 2
