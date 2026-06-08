"""Tests for weekly_report (photo_saver:616) — 7-day activity + recommendations."""
import json
import time

import photo_saver as ps

MB = 1024 * 1024


def _write_audit(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_activity_counts_only_last_7_days(state_files, monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [])
    monkeypatch.setattr(ps, "FILES_DATA", [])
    now = time.time()
    _write_audit(state_files["audit"], [
        {"type": "trash", "ts": now - 3600, "bytes": 1000, "count": 2},       # recent
        {"type": "compress", "ts": now - 2 * 86400, "saved_bytes": 500},       # recent
        {"type": "trash", "ts": now - 10 * 86400, "bytes": 9999, "count": 9},  # too old
    ])
    rep = ps.weekly_report()
    assert rep["activity"] == {"reclaimed": 1500, "deleted": 2, "compressed": 1}
    assert rep["period_days"] == 7


def test_recommends_clearing_confident_duplicates(state_files, monkeypatch):
    monkeypatch.setattr(ps, "FILES_DATA", [])
    monkeypatch.setattr(ps, "CLUSTER_DATA", [{
        "match": "identical",
        "photos": [
            {"source": "library", "size": 300},
            {"source": "library", "size": 200},   # removable
            {"source": "shared", "size": 999},     # ignored (not yours)
        ],
    }])
    _write_audit(state_files["audit"], [])
    rep = ps.weekly_report()
    keys = {r["key"]: r for r in rep["recommendations"]}
    assert "dupes" in keys
    assert keys["dupes"]["save_bytes"] == 200      # all library copies but the best
    assert rep["opportunity"] >= 200


def test_recommends_compressing_large_library_videos(state_files, monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [])
    monkeypatch.setattr(ps, "FILES_DATA", [
        {"is_video": True, "source": "library", "size": 500 * MB},
        {"is_video": True, "source": "shared", "size": 500 * MB},   # not yours → skip
        {"is_video": True, "source": "library", "size": 50 * MB},    # under 200MB → skip
        {"is_video": False, "source": "library", "size": 500 * MB},  # not a video → skip
    ])
    _write_audit(state_files["audit"], [])
    rep = ps.weekly_report()
    keys = {r["key"]: r for r in rep["recommendations"]}
    assert "compress" in keys
    assert keys["compress"]["save_bytes"] == int(500 * MB * 0.6)


def test_no_recommendations_when_nothing_actionable(state_files, monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [])
    monkeypatch.setattr(ps, "FILES_DATA", [])
    _write_audit(state_files["audit"], [])
    rep = ps.weekly_report()
    assert rep["recommendations"] == []
    assert rep["opportunity"] == 0


def test_recommendations_sorted_by_savings_desc(state_files, monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [{
        "match": "near-identical",
        "photos": [{"source": "library", "size": 10}, {"source": "library", "size": 5}],
    }])
    monkeypatch.setattr(ps, "FILES_DATA", [
        {"is_video": True, "source": "library", "size": 1000 * MB},
    ])
    _write_audit(state_files["audit"], [])
    rep = ps.weekly_report()
    saves = [r["save_bytes"] for r in rep["recommendations"]]
    assert saves == sorted(saves, reverse=True)
