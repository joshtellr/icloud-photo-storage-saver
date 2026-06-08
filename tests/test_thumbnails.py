"""Tests for the thumbnail cache: get_thumb_bytes (photo_saver:2180) and
prewarm_thumbnails (2215). Exercises real JPEG generation plus the LRU bound
that keeps the always-on process from ballooning in memory."""
from PIL import Image

import pytest

import photo_saver as ps


def _make_image(path, size=(400, 300), color=(120, 30, 200), mode="RGB"):
    Image.new(mode, size, color if mode != "L" else 120).save(path, "JPEG")
    return path


@pytest.fixture(autouse=True)
def clear_thumb_cache():
    with ps._thumb_lock:
        ps._thumb_cache.clear()
    yield
    with ps._thumb_lock:
        ps._thumb_cache.clear()


def test_generates_jpeg_bytes(tmp_path, make_photo):
    img = _make_image(tmp_path / "d.jpg")
    photo = make_photo("a", path_derivatives=[str(img)])
    data = ps.get_thumb_bytes("a", photo)
    assert data is not None
    assert data[:2] == b"\xff\xd8"        # JPEG SOI marker


def test_thumbnail_is_bounded_to_max_px(tmp_path, make_photo):
    img = _make_image(tmp_path / "big.jpg", size=(2000, 1500))
    photo = make_photo("a", path_derivatives=[str(img)])
    data = ps.get_thumb_bytes("a", photo)
    import io
    w, h = Image.open(io.BytesIO(data)).size
    assert max(w, h) <= ps.THUMB_MAX_PX


def test_result_is_cached_and_served_without_the_source(tmp_path, make_photo):
    img = _make_image(tmp_path / "d.jpg")
    photo = make_photo("a", path_derivatives=[str(img)])
    first = ps.get_thumb_bytes("a", photo)
    img.unlink()                          # source gone after first generation
    second = ps.get_thumb_bytes("a", photo)
    assert second == first                # served from cache, not regenerated
    assert "a" in ps._thumb_cache


def test_none_when_no_derivatives(make_photo):
    assert ps.get_thumb_bytes("a", make_photo("a")) is None


def test_none_when_derivative_missing(make_photo):
    photo = make_photo("a", path_derivatives=["/no/such/file.jpg"])
    assert ps.get_thumb_bytes("a", photo) is None


def test_none_on_unreadable_image(tmp_path, make_photo):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"this is not an image")
    photo = make_photo("a", path_derivatives=[str(bad)])
    assert ps.get_thumb_bytes("a", photo) is None


def test_non_rgb_mode_is_converted(tmp_path, make_photo):
    img = tmp_path / "rgba.png"
    Image.new("RGBA", (200, 200), (10, 20, 30, 128)).save(img, "PNG")
    photo = make_photo("a", path_derivatives=[str(img)])
    data = ps.get_thumb_bytes("a", photo)
    assert data is not None and data[:2] == b"\xff\xd8"


def test_lru_evicts_least_recently_used(tmp_path, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "THUMB_CACHE_MAX", 2)
    photos = {}
    for u in ("a", "b", "c"):
        photos[u] = make_photo(u, path_derivatives=[str(_make_image(tmp_path / f"{u}.jpg"))])
    ps.get_thumb_bytes("a", photos["a"])
    ps.get_thumb_bytes("b", photos["b"])
    ps.get_thumb_bytes("c", photos["c"])   # over cap → evict "a"
    assert set(ps._thumb_cache) == {"b", "c"}


def test_access_refreshes_recency(tmp_path, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "THUMB_CACHE_MAX", 2)
    photos = {}
    for u in ("a", "b", "c"):
        photos[u] = make_photo(u, path_derivatives=[str(_make_image(tmp_path / f"{u}.jpg"))])
    ps.get_thumb_bytes("a", photos["a"])
    ps.get_thumb_bytes("b", photos["b"])
    ps.get_thumb_bytes("a", photos["a"])   # touch "a" → now "b" is the LRU
    ps.get_thumb_bytes("c", photos["c"])   # evict "b"
    assert set(ps._thumb_cache) == {"a", "c"}


def test_prewarm_populates_cache(tmp_path, monkeypatch, make_photo):
    photos, cluster_photos = {}, []
    for u in ("a", "b"):
        photos[u] = make_photo(u, path_derivatives=[str(_make_image(tmp_path / f"{u}.jpg"))])
        cluster_photos.append({"uuid": u})
    monkeypatch.setattr(ps, "CLUSTER_DATA", [{"photos": cluster_photos}])
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", photos)
    ps.prewarm_thumbnails()
    assert set(ps._thumb_cache) == {"a", "b"}


def test_prewarm_respects_cap(tmp_path, monkeypatch, make_photo):
    monkeypatch.setattr(ps, "THUMB_CACHE_MAX", 1)
    photos, cluster_photos = {}, []
    for u in ("a", "b", "c"):
        photos[u] = make_photo(u, path_derivatives=[str(_make_image(tmp_path / f"{u}.jpg"))])
        cluster_photos.append({"uuid": u})
    monkeypatch.setattr(ps, "CLUSTER_DATA", [{"photos": cluster_photos}])
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", photos)
    ps.prewarm_thumbnails()
    assert len(ps._thumb_cache) == 1
