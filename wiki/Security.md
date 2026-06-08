# Security

This page summarizes the project's security model. The authoritative source is
[`SECURITY.md`](https://github.com/joshtellr/icloud-photo-storage-saver/blob/main/SECURITY.md)
in the repo — if the two ever disagree, that file wins.

---

## Model

iCloud Photo Storage Saver is a **local tool**. By default the server binds to
**`127.0.0.1` (loopback) only** — it is not reachable from your network or the internet
unless *you* deliberately expose it.

The web UI talks to local endpoints, **some of which are destructive** (moving photos
to the Photos trash, re-encoding videos, deleting). On loopback these are
**unauthenticated by design** — the same trust model as any local desktop app.

---

## Exposing it beyond your Mac

This is a **single-user personal tool**, not a hardened multi-user server. It has:

- no user accounts,
- no TLS of its own,
- no rate limiting.

If you want to reach it from another device, front it with your own access layer (see
[[Remote Access]]): a **mesh VPN** (Tailscale/WireGuard/Netbird), a **reverse proxy
with TLS** (Caddy/nginx/Traefik), or an **SSH tunnel**.

### The access token

Whenever the port leaves `localhost`, set an access token as defense-in-depth on top
of (never instead of) that layer:

```bash
export PHOTO_SAVER_TOKEN="$(openssl rand -hex 16)"
# then launch the app, or: PHOTO_SAVER_TOKEN=… bash install-agent.sh
```

When `PHOTO_SAVER_TOKEN` is set, **every** request must present it via `?token=…` (the
cookie is then set for the session), an `X-Auth-Token` header, or the `ps_token`
cookie. Requests without it get **`401`**.

The token is a **plaintext shared secret** compared on each request — treat it as a
**thin gate, not real auth**, and only ever send it over a connection you already trust
(VPN or TLS).

> **Never put this on the raw public internet** — no port-forward, no Tailscale Funnel.
> The endpoints are destructive and there's no account model to fall back on.

---

## What it can do

- Move photos/videos to the **Photos trash** (recoverable from **Recently Deleted**
  until you empty it).
- **Re-encode** videos to H.265 and trash the originals.
- Run **AppleScript** to open items in Photos / drive a date search. This input is
  **sanitized** — UUID/date-only — so it can't be used for command injection.

## What it does **not** do

- It does **not** make external network calls at runtime.
- It does **not** collect analytics or telemetry.
- It does **not** upload anything. (Only the first-run dependency installer touches the
  network.)
- It does **not** act on photos you don't own — shared / hidden / unreachable items are
  read-only.
- It does **not** permanently delete — everything goes through the Photos trash.

---

## Hardening notes (from the code & tests)

- The server binds explicitly to `("127.0.0.1", 8421)` — not `0.0.0.0`.
- Token checks cover both GET and POST; POST handlers are hardened and tested
  (`tests/test_http.py`).
- The `/open` and `/finddate` AppleScript paths sanitize input to UUID/date only.
- See [[Architecture]] for where these live in `photo_saver.py`.

---

## Reporting a vulnerability

Please open a **private security advisory** on GitHub
(**Security → Report a vulnerability**) rather than a public issue:
[Report a vulnerability](https://github.com/joshtellr/icloud-photo-storage-saver/security/advisories/new).
You'll get a response as soon as possible.

---

### See also

- [[Remote Access]] — concrete recipes for exposing it safely.
- [[Configuration]] — `PHOTO_SAVER_TOKEN` details.
- [Privacy section of the README](https://github.com/joshtellr/icloud-photo-storage-saver#privacy).
