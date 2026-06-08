"""Tests for fmt_bytes (photo_saver:261) — the unit thresholds."""
import photo_saver as ps


def test_gigabytes():
    assert ps.fmt_bytes(1024 ** 3) == "1.00 GB"
    assert ps.fmt_bytes(int(1.5 * 1024 ** 3)) == "1.50 GB"


def test_megabytes():
    assert ps.fmt_bytes(1024 ** 2) == "1.0 MB"
    assert ps.fmt_bytes(5 * 1024 ** 2) == "5.0 MB"


def test_kilobytes():
    assert ps.fmt_bytes(1024) == "1 KB"
    assert ps.fmt_bytes(500) == "0 KB"


def test_boundary_just_below_one_megabyte_stays_in_kb():
    assert ps.fmt_bytes(1024 ** 2 - 1) == "1024 KB"


def test_boundary_just_below_one_gigabyte_stays_in_mb():
    assert ps.fmt_bytes(1024 ** 3 - 1).endswith(" MB")
