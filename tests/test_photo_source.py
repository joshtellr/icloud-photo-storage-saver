"""Tests for photo_source classification (photo_saver:2298)."""
import photo_saver as ps


def test_unknown_classification_treats_all_as_library(monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", None)
    assert ps.photo_source("anything") == "library"


def test_library_uuid(monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", {"a", "b"})
    monkeypatch.setattr(ps, "HIDDEN_UUIDS", set())
    assert ps.photo_source("a") == "library"


def test_shared_uuid(monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", {"a", "b"})
    monkeypatch.setattr(ps, "HIDDEN_UUIDS", set())
    assert ps.photo_source("b") == "shared"


def test_hidden_uuid(monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", {"a"})
    monkeypatch.setattr(ps, "HIDDEN_UUIDS", {"h"})
    assert ps.photo_source("h") == "hidden"


def test_missing_uuid(monkeypatch):
    monkeypatch.setattr(ps, "LIBRARY_UUIDS", {"a"})
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", {"a"})
    monkeypatch.setattr(ps, "HIDDEN_UUIDS", set())
    assert ps.photo_source("ghost") == "missing"
