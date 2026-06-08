"""Tests for the HTTP layer (photo_saver:2311 Handler).

Covers the security-sensitive surface that the pure-logic suite can't reach:
  * token auth (_authed / _query_token) — query, header, cookie, and rejections
  * POST hardening — oversized body (413), malformed JSON (400), bad length (400)
  * routing basics (200 / 404)
  * input sanitization on /open/ and /finddate/ before they reach osascript

A real ThreadingHTTPServer is started on an ephemeral port. The osascript /
pbcopy subprocess calls (macOS-only) are stubbed so the sanitization paths can
run on any platform.
"""
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import photo_saver as ps


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Record subprocess.run calls and return a successful result, so /open and
    /finddate don't actually shell out to macOS-only tools."""
    calls = []

    def _run(cmd, *args, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ps.subprocess, "run", _run)
    return calls


@pytest.fixture
def server(state_files, monkeypatch):
    """Start the Handler on 127.0.0.1:<ephemeral> with empty, deterministic
    module state. Yields the base URL."""
    monkeypatch.setattr(ps, "AUTH_TOKEN", "")          # auth off unless a test sets it
    monkeypatch.setattr(ps, "CLUSTER_DATA", [])
    monkeypatch.setattr(ps, "FILES_DATA", [])
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID", {})
    monkeypatch.setattr(ps, "_clusters_gzip", b"")
    monkeypatch.setattr(ps, "_files_gzip", b"")
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", None)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _request(url, method="GET", headers=None, data=None):
    """Return (status, headers, body_bytes), treating 4xx/5xx as normal results."""
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ── Routing ────────────────────────────────────────────────────────────────

def test_root_serves_html(server):
    status, headers, body = _request(server + "/")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert b"<!DOCTYPE html>" in body


def test_clusters_returns_json_payload(server, monkeypatch):
    monkeypatch.setattr(ps, "CLUSTER_DATA", [{"id": "x", "photos": []}])
    status, headers, body = _request(server + "/clusters")
    assert status == 200
    assert json.loads(body) == [{"id": "x", "photos": []}]


def test_unknown_route_is_404(server):
    status, _, _ = _request(server + "/nope")
    assert status == 404


def test_clusters_served_gzipped_when_accepted(server, monkeypatch):
    import gzip
    payload = [{"id": "x", "photos": [{"uuid": "a"}, {"uuid": "b"}]}]
    monkeypatch.setattr(ps, "CLUSTER_DATA", payload)
    monkeypatch.setattr(ps, "_clusters_gzip", gzip.compress(json.dumps(payload).encode()))
    status, headers, body = _request(server + "/clusters",
                                     headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers.get("Content-Encoding") == "gzip"
    assert json.loads(gzip.decompress(body)) == payload


def test_files_served_gzipped_when_accepted(server, monkeypatch):
    import gzip
    payload = [{"uuid": "a", "size": 1}]
    monkeypatch.setattr(ps, "FILES_DATA", payload)
    monkeypatch.setattr(ps, "_files_gzip", gzip.compress(json.dumps(payload).encode()))
    status, headers, body = _request(server + "/files",
                                     headers={"Accept-Encoding": "gzip"})
    assert headers.get("Content-Encoding") == "gzip"
    assert json.loads(gzip.decompress(body)) == payload


def test_metric_routes_return_json(server):
    for route in ("/status", "/compress-status", "/audit", "/report", "/decisions"):
        status, headers, body = _request(server + route)
        assert status == 200, route
        assert "application/json" in headers["Content-Type"], route
        json.loads(body)  # parses


def test_thumb_serves_jpeg_for_known_photo(server, tmp_path, monkeypatch, make_photo):
    from PIL import Image
    deriv = tmp_path / "d.jpg"
    Image.new("RGB", (200, 150), (10, 120, 200)).save(deriv, "JPEG")
    monkeypatch.setattr(ps, "PHOTOS_BY_UUID",
                        {"u": make_photo("u", path_derivatives=[str(deriv)])})
    with ps._thumb_lock:
        ps._thumb_cache.clear()
    status, headers, body = _request(server + "/thumb/u")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body[:2] == b"\xff\xd8"


def test_thumb_unknown_uuid_is_404(server):
    status, _, _ = _request(server + "/thumb/does-not-exist")
    assert status == 404


# ── Auth ───────────────────────────────────────────────────────────────────

def test_no_token_configured_allows_all(server):
    assert _request(server + "/status")[0] == 200


def test_missing_token_is_rejected(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, _, body = _request(server + "/status")
    assert status == 401
    assert b"unauthorized" in body


def test_root_without_token_serves_auth_page(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, headers, body = _request(server + "/")
    assert status == 401
    assert b"Token required" in body


def test_query_token_is_accepted_and_sets_cookie(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, headers, _ = _request(server + "/?token=secret")
    assert status == 200
    assert "ps_token=secret" in headers.get("Set-Cookie", "")


def test_header_token_is_accepted(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, _, _ = _request(server + "/status", headers={"X-Auth-Token": "secret"})
    assert status == 200


def test_cookie_token_is_accepted(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, _, _ = _request(server + "/status", headers={"Cookie": "ps_token=secret"})
    assert status == 200


def test_wrong_token_is_rejected(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    assert _request(server + "/status?token=nope")[0] == 401
    assert _request(server + "/status", headers={"X-Auth-Token": "nope"})[0] == 401


def test_post_requires_auth(server, monkeypatch):
    monkeypatch.setattr(ps, "AUTH_TOKEN", "secret")
    status, _, _ = _request(server + "/decisions", method="POST", data=b"{}")
    assert status == 401


# ── POST hardening ─────────────────────────────────────────────────────────

def test_oversized_post_is_rejected_413(server, monkeypatch):
    monkeypatch.setattr(ps, "MAX_POST_BYTES", 10)
    status, _, _ = _request(server + "/decisions", method="POST", data=b"x" * 50)
    assert status == 413


def test_malformed_json_is_rejected_400(server):
    status, _, _ = _request(server + "/decisions", method="POST", data=b"not json")
    assert status == 400


def test_valid_decisions_post_persists(server, state_files):
    body = json.dumps({"a": "trash"}).encode()
    status, _, _ = _request(server + "/decisions", method="POST", data=body)
    assert status == 200
    assert ps.read_decisions() == {"a": "trash"}


def test_unknown_post_route_is_404(server):
    status, _, _ = _request(server + "/bogus", method="POST", data=b"{}")
    assert status == 404


def test_non_integer_content_length_is_400(server):
    # urllib won't send a bogus Content-Length, so craft the request by hand.
    host = server.split("//")[1]
    ip, port = host.split(":")
    raw = (
        "POST /decisions HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Length: notanumber\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    with socket.create_connection((ip, int(port)), timeout=5) as s:
        s.sendall(raw)
        resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
    assert b" 400 " in resp.split(b"\r\n", 1)[0]


# ── Input sanitization (osascript injection guards) ─────────────────────────

def test_open_strips_injection_chars_before_osascript(server, fake_subprocess, monkeypatch):
    # PK_ALL_UUIDS None → the route proceeds to build + run the osascript.
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", None)
    _request(server + '/open/AABB%22%3B%20rm%20-rf%20%2F')  # AABB"; rm -rf /
    assert fake_subprocess, "osascript should have been invoked"
    script = " ".join(fake_subprocess[-1]["cmd"])
    # Only hex/dash characters survive in the interpolated id.
    assert '"; rm' not in script
    assert ";" not in script.split("spotlight media item id")[1]
    assert "AABB" in script


def test_finddate_strips_injection_chars_before_osascript(server, fake_subprocess):
    _request(server + '/finddate/2026-06-08%22%3Brm')  # 2026-06-08";rm
    scripts = [" ".join(c["cmd"]) for c in fake_subprocess]
    osa = [s for s in scripts if "keystroke" in s]
    assert osa, "osascript keystroke should have been invoked"
    assert "2026-06-08" in osa[-1]
    assert '"' not in osa[-1].split("keystroke")[-1] or ";rm" not in osa[-1]


def test_open_unreachable_uuid_returns_error_without_shelling_out(server, fake_subprocess, monkeypatch):
    # When PhotoKit has no handle on the id, the route short-circuits.
    monkeypatch.setattr(ps, "PK_ALL_UUIDS", {"KNOWN"})
    status, _, body = _request(server + "/open/UNKNOWN")
    assert status == 200
    assert json.loads(body)["ok"] is False
    assert fake_subprocess == []  # never reached osascript
