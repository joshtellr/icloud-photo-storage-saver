"""Shared fixtures for the photo_saver test suite.

photo_saver.py is a single module with macOS-only imports done lazily, so it
imports fine on Linux and we can exercise its pure logic and persistence layer
directly. The fixtures here provide:

  * a lightweight stand-in for an osxphotos PhotoInfo object (`FakePhoto`)
  * redirection of the module's home-directory state files into a tmp dir
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Make the repo root importable so `import photo_saver` works no matter where
# pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import photo_saver  # noqa: E402


class FakePhoto:
    """Minimal duck-typed replacement for osxphotos PhotoInfo.

    Only the attributes photo_saver actually reads are implemented (see the
    attribute audit in build_cluster_list / build_files_list / score_photo).
    """

    def __init__(
        self,
        uuid,
        *,
        original_filesize=0,
        favorite=False,
        path=None,
        path_derivatives=None,
        date=None,
        ismovie=False,
        original_filename="IMG.jpg",
    ):
        self.uuid = uuid
        self.original_filesize = original_filesize
        self.favorite = favorite
        self.path = path
        self.path_derivatives = path_derivatives or []
        self.date = date
        self.ismovie = ismovie
        self.original_filename = original_filename


@pytest.fixture
def make_photo():
    """Factory fixture so tests can spin up FakePhotos tersely."""
    def _make(uuid, **kw):
        return FakePhoto(uuid, **kw)
    return _make


@pytest.fixture
def dt():
    """Helper to build datetimes: dt(2026, 6, 8, 12, 0, 30)."""
    return datetime


@pytest.fixture
def state_files(tmp_path, monkeypatch):
    """Redirect the module's home-dir JSON/JSONL state files into a tmp dir.

    Returns the paths so a test can assert directly against them.
    """
    cache = tmp_path / "cache.json"
    decisions = tmp_path / "decisions.json"
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(photo_saver, "CACHE_FILE", cache)
    monkeypatch.setattr(photo_saver, "DECISIONS_FILE", decisions)
    monkeypatch.setattr(photo_saver, "AUDIT_FILE", audit)
    return {"cache": cache, "decisions": decisions, "audit": audit}
