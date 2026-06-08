"""Tests for the payload builders build_cluster_list (296) and build_files_list (333)."""
import pytest

import photo_saver as ps


@pytest.fixture(autouse=True)
def library_unknown(monkeypatch):
    # With no PhotoKit classification, photo_source() returns "library" for all.
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", None)


def test_cluster_keeps_best_and_computes_removable(make_photo):
    a = make_photo("a", original_filesize=100, original_filename="a.jpg")
    b = make_photo("b", original_filesize=300, original_filename="b.jpg")
    by_uuid = {"a": a, "b": b}
    clusters = {"root": ["a", "b"]}
    hashes = {"a": "0000000000000000", "b": "0000000000000001"}

    out = ps.build_cluster_list(by_uuid, clusters, hashes)

    assert len(out) == 1
    c = out[0]
    assert c["keep_uuid"] == "b"                 # larger file wins by default
    assert [p["uuid"] for p in c["photos"]] == ["b", "a"]  # sorted best-first
    assert c["removable_bytes"] == 100           # everything but the kept photo
    assert c["match"] == "near-identical"        # tightness 1
    assert c["tightness"] == 1


def test_cluster_with_fewer_than_two_valid_photos_is_dropped(make_photo):
    a = make_photo("a", original_filesize=100)
    by_uuid = {"a": a}                            # "b" missing from the library map
    clusters = {"root": ["a", "b"]}
    assert ps.build_cluster_list(by_uuid, clusters, {}) == []


def test_clusters_sorted_tightest_first(make_photo):
    photos = {u: make_photo(u, original_filesize=100) for u in ("a", "b", "c", "d")}
    clusters = {"loose": ["a", "b"], "tight": ["c", "d"]}
    hashes = {
        "a": "0000000000000000", "b": "00000000000000ff",  # tightness 8
        "c": "0000000000000000", "d": "0000000000000001",  # tightness 1
    }
    out = ps.build_cluster_list(photos, clusters, hashes)
    assert [c["id"] for c in out] == ["tight", "loose"]


def test_cluster_missing_hashes_defaults_to_loose(make_photo):
    photos = {u: make_photo(u, original_filesize=100) for u in ("a", "b")}
    out = ps.build_cluster_list(photos, {"root": ["a", "b"]}, hashes=None)
    assert out[0]["tightness"] == 99
    assert out[0]["match"] == "similar"


def test_files_list_sorted_by_size_desc(make_photo):
    photos = [
        make_photo("a", original_filesize=100, original_filename="a.jpg"),
        make_photo("b", original_filesize=900, original_filename="b.mov", ismovie=True),
        make_photo("c", original_filesize=500, original_filename="c.png"),
    ]
    out = ps.build_files_list(photos)
    assert [f["uuid"] for f in out] == ["b", "c", "a"]
    assert out[0]["is_video"] is True


def test_files_list_detects_raw_extensions(make_photo):
    photos = [
        make_photo("raw", original_filesize=10, original_filename="shot.DNG"),
        make_photo("jpg", original_filesize=5, original_filename="snap.jpg"),
        make_photo("none", original_filesize=1, original_filename="noext"),
    ]
    by_uuid = {f["uuid"]: f for f in ps.build_files_list(photos)}
    assert by_uuid["raw"]["is_raw"] is True
    assert by_uuid["raw"]["ext"] == "dng"
    assert by_uuid["jpg"]["is_raw"] is False
    assert by_uuid["none"]["ext"] == ""


def test_files_list_truncates_to_limit(monkeypatch, make_photo):
    monkeypatch.setattr(ps, "FILES_LIMIT", 3)
    photos = [make_photo(str(i), original_filesize=i) for i in range(10)]
    out = ps.build_files_list(photos)
    assert len(out) == 3
    # The three largest survive.
    assert [f["uuid"] for f in out] == ["9", "8", "7"]
