"""Tests for the audit log + metrics (photo_saver:563-613)."""
import json

import photo_saver as ps


def _write_audit(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_audit_log_appends_and_read_parses(state_files):
    ps.audit_log({"type": "trash", "bytes": 100, "count": 2})
    ps.audit_log({"type": "session", "total_photos": 10})
    events = ps.read_audit()
    assert [e["type"] for e in events] == ["trash", "session"]
    assert events[0]["bytes"] == 100
    assert "ts" in events[0] and "iso" in events[0]


def test_read_audit_missing_file_is_empty(state_files):
    assert ps.read_audit() == []


def test_read_audit_skips_malformed_lines(state_files):
    state_files["audit"].write_text(
        '{"type": "trash", "bytes": 5}\n'
        "this is not json\n"
        '\n'
        '{"type": "session"}\n'
    )
    events = ps.read_audit()
    assert [e["type"] for e in events] == ["trash", "session"]


def test_audit_summary_aggregates_by_type(state_files):
    _write_audit(state_files["audit"], [
        {"type": "trash", "iso": "2026-06-01T10:00:00", "bytes": 1000, "count": 3},
        {"type": "compress", "iso": "2026-06-01T11:00:00", "saved_bytes": 500},
        {"type": "trash", "iso": "2026-06-02T10:00:00", "bytes": 2000, "count": 1},
        {"type": "session", "iso": "2026-06-02T12:00:00"},
    ])
    s = ps.audit_summary()
    t = s["totals"]
    assert t["reclaimed"] == 1000 + 2000 + 500   # trash bytes + compress saved
    assert t["deleted"] == 4                      # 3 + 1
    assert t["compressed"] == 1
    assert t["compress_saved"] == 500
    assert t["sessions"] == 1
    assert t["event_count"] == 4


def test_audit_summary_groups_reclaimed_by_day(state_files):
    _write_audit(state_files["audit"], [
        {"type": "trash", "iso": "2026-06-01T10:00:00", "bytes": 1000, "count": 1},
        {"type": "compress", "iso": "2026-06-01T11:00:00", "saved_bytes": 500},
        {"type": "trash", "iso": "2026-06-02T10:00:00", "bytes": 2000, "count": 1},
    ])
    by_day = ps.audit_summary()["by_day"]
    assert by_day == {"2026-06-01": 1500, "2026-06-02": 2000}


def test_audit_summary_events_most_recent_first(state_files):
    _write_audit(state_files["audit"], [
        {"type": "trash", "iso": "2026-06-01T10:00:00", "bytes": 1, "count": 1},
        {"type": "trash", "iso": "2026-06-02T10:00:00", "bytes": 1, "count": 1},
    ])
    events = ps.audit_summary()["events"]
    assert events[0]["iso"] == "2026-06-02T10:00:00"


def test_audit_summary_empty(state_files):
    s = ps.audit_summary()
    assert s["totals"]["event_count"] == 0
    assert s["totals"]["first"] is None
    assert s["by_day"] == {}
