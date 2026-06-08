"""Tests for prune_payloads (photo_saver:2276) — keeps the live payloads and
their gzip caches in sync after a trash, so a refresh can't re-serve deleted
photos. Part of the same reappearing-after-refresh fix as the decisions tests."""
import gzip
import json

import photo_saver as ps


def _setup(monkeypatch, clusters, files):
    monkeypatch.setattr(ps, "CLUSTER_DATA", clusters)
    monkeypatch.setattr(ps, "FILES_DATA", files)
    monkeypatch.setattr(ps, "_clusters_gzip", b"")
    monkeypatch.setattr(ps, "_files_gzip", b"")


def test_removes_trashed_uuid_from_files(monkeypatch):
    _setup(monkeypatch, [], [{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}])
    ps.prune_payloads({"b"})
    assert [f["uuid"] for f in ps.FILES_DATA] == ["a", "c"]


def test_cluster_dropping_below_two_photos_is_removed(monkeypatch):
    clusters = [
        {"id": "keep", "photos": [{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}]},
        {"id": "drop", "photos": [{"uuid": "x"}, {"uuid": "y"}]},
    ]
    _setup(monkeypatch, clusters, [])
    ps.prune_payloads({"y"})  # "drop" falls to a single photo → cluster removed
    ids = [c["id"] for c in ps.CLUSTER_DATA]
    assert ids == ["keep"]
    assert [p["uuid"] for p in ps.CLUSTER_DATA[0]["photos"]] == ["a", "b", "c"]


def test_cluster_loses_only_the_trashed_photo(monkeypatch):
    clusters = [{"id": "k", "photos": [{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}]}]
    _setup(monkeypatch, clusters, [])
    ps.prune_payloads({"b"})
    assert [p["uuid"] for p in ps.CLUSTER_DATA[0]["photos"]] == ["a", "c"]


def test_gzip_caches_are_rebuilt_to_match(monkeypatch):
    _setup(monkeypatch, [{"id": "k", "photos": [{"uuid": "a"}, {"uuid": "b"}]}],
           [{"uuid": "a"}, {"uuid": "b"}])
    ps.prune_payloads({"a"})
    # files cache reflects the prune; cluster (now 1 photo) was dropped
    assert json.loads(gzip.decompress(ps._files_gzip)) == ps.FILES_DATA
    assert json.loads(gzip.decompress(ps._clusters_gzip)) == ps.CLUSTER_DATA
    assert ps.CLUSTER_DATA == []


def test_empty_removed_set_is_a_noop(monkeypatch):
    files = [{"uuid": "a"}]
    _setup(monkeypatch, [], files)
    ps.prune_payloads(set())
    assert ps.FILES_DATA is files          # untouched, not rebuilt
    assert ps._files_gzip == b""
