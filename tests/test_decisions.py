"""Tests for the decisions persistence layer (photo_saver:57-95) and the cache
(118-128). Includes a regression guard for "trashed photos reappearing after
refresh" (commit 4ed2cf6): clearing/pruning must actually drop the UUIDs."""
import json

import photo_saver as ps


def test_read_missing_file_returns_empty(state_files):
    assert ps.read_decisions() == {}


def test_write_then_read_roundtrip(state_files):
    ps.write_decisions({"a": "trash", "b": "keep"})
    assert ps.read_decisions() == {"a": "trash", "b": "keep"}


def test_read_tolerates_corrupt_json(state_files):
    state_files["decisions"].write_text("{not valid json")
    assert ps.read_decisions() == {}


def test_write_is_atomic_no_tmp_left_behind(state_files):
    ps.write_decisions({"a": "trash"})
    leftovers = list(state_files["decisions"].parent.glob("*.tmp"))
    assert leftovers == []


def test_clear_decisions_drops_trashed_uuids(state_files):
    # Regression: a refresh must not resurrect a mark for an already-trashed photo.
    ps.write_decisions({"a": "trash", "b": "trash", "c": "keep"})
    ps.clear_decisions(["a", "b"])
    assert ps.read_decisions() == {"c": "keep"}


def test_clear_decisions_noop_on_empty_input(state_files):
    ps.write_decisions({"a": "trash"})
    ps.clear_decisions([])
    assert ps.read_decisions() == {"a": "trash"}


def test_prune_decisions_keeps_only_valid_uuids(state_files):
    ps.write_decisions({"a": "trash", "stale": "trash", "c": "keep"})
    ps.prune_decisions({"a", "c"})  # "stale" no longer in the library
    assert ps.read_decisions() == {"a": "trash", "c": "keep"}


def test_prune_decisions_leaves_file_untouched_when_all_valid(state_files):
    ps.write_decisions({"a": "trash"})
    before = state_files["decisions"].read_text()
    ps.prune_decisions({"a", "b", "c"})
    assert state_files["decisions"].read_text() == before


def test_cache_roundtrip_and_corruption_tolerance(state_files):
    assert ps.load_cache() == {}                 # missing file
    ps.save_cache({"uuid1": "00000000000000ff"})
    assert ps.load_cache() == {"uuid1": "00000000000000ff"}
    state_files["cache"].write_text("garbage")
    assert ps.load_cache() == {}


def test_written_decisions_are_valid_json(state_files):
    ps.write_decisions({"a": "trash"})
    json.loads(state_files["decisions"].read_text())  # raises if malformed
