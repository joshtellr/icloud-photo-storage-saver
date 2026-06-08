"""Tests for find_clusters (photo_saver:186) — the core dedup algorithm.

Two passes: exact-hash matches (any time) then near-matches inside a sliding
time window. Clusters need >=2 members to survive.
"""
from datetime import datetime, timedelta

import photo_saver as ps


BASE = datetime(2026, 6, 8, 12, 0, 0)


def _photos(*specs):
    """specs: (uuid, seconds_offset_or_None). Returns FakePhoto-ish list + hashes
    are supplied separately by the caller."""
    out = []
    for uuid, off in specs:
        date = None if off is None else BASE + timedelta(seconds=off)
        out.append(_P(uuid, date))
    return out


class _P:
    def __init__(self, uuid, date):
        self.uuid = uuid
        self.date = date


def _cluster_sets(clusters):
    """Normalize {root: [uuids]} into a set of frozensets for order-free compare."""
    return {frozenset(members) for members in clusters.values()}


def test_exact_match_clusters_regardless_of_time():
    # Identical hashes years apart still cluster (re-download / copy case).
    photos = _photos(("a", 0), ("b", 10_000_000))
    hashes = {"a": "00000000000000ff", "b": "00000000000000ff"}
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"})}


def test_near_match_within_window_clusters():
    photos = _photos(("a", 0), ("b", 30))
    hashes = {"a": "0000000000000000", "b": "0000000000000001"}  # hamming 1
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"})}


def test_near_match_outside_window_does_not_cluster():
    photos = _photos(("a", 0), ("b", 120))  # 120s apart, window 60
    hashes = {"a": "0000000000000000", "b": "0000000000000003"}  # hamming 2, not exact
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert clusters == {}


def test_threshold_boundary_is_inclusive():
    photos = _photos(("a", 0), ("b", 10))
    hashes = {"a": "0000000000000000", "b": "0000000000000003"}  # hamming exactly 2
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"})}


def test_above_threshold_does_not_cluster():
    photos = _photos(("a", 0), ("b", 10))
    hashes = {"a": "0000000000000000", "b": "0000000000000007"}  # hamming 3 > 2
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert clusters == {}


def test_transitive_chaining_within_window():
    photos = _photos(("a", 0), ("b", 30), ("c", 50))
    hashes = {
        "a": "0000000000000000",
        "b": "0000000000000001",  # a~b hamming 1
        "c": "0000000000000003",  # b~c hamming 1, a~c hamming 2
    }
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b", "c"})}


def test_photo_without_hash_is_excluded():
    photos = _photos(("a", 0), ("b", 30), ("c", 40))
    hashes = {"a": "0000000000000000", "b": "0000000000000001"}  # c has no hash
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"})}


def test_dateless_photo_still_clusters_on_exact_hash():
    # The near-match pass skips dateless photos, but the exact pass does not.
    photos = _photos(("a", 0), ("b", None))
    hashes = {"a": "00000000000000ab", "b": "00000000000000ab"}
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"})}


def test_singletons_are_not_returned():
    photos = _photos(("a", 0), ("b", 30))
    hashes = {"a": "0000000000000000", "b": "00000000000000ff"}  # unrelated
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert clusters == {}


def test_two_distinct_clusters():
    photos = _photos(("a", 0), ("b", 10), ("c", 1000), ("d", 1010))
    hashes = {
        "a": "0000000000000000", "b": "0000000000000001",   # pair 1
        "c": "00000000000000f0", "d": "00000000000000f1",   # pair 2 (far in time)
    }
    clusters = ps.find_clusters(photos, hashes, threshold=2, time_window_sec=60)
    assert _cluster_sets(clusters) == {frozenset({"a", "b"}), frozenset({"c", "d"})}
