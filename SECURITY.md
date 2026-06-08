# Security

## Model

iCloud Photo Storage Saver is a local tool. By default the server binds to
**`127.0.0.1` (loopback) only** — it is not reachable from your network or the
internet unless *you* deliberately expose it.

The web UI talks to local endpoints, some of which are **destructive** (moving
photos to the Photos trash, re-encoding videos, deleting). On loopback these are
unauthenticated by design — the same trust model as any local desktop app.

## Exposing it beyond your Mac

This is a **single-user personal tool**, not a hardened multi-user server. It has
no user accounts, no TLS of its own, and no rate limiting. If you want to reach it
from another device, front it with your own access layer — the same way you'd
expose any self-hosted service:

- **Mesh VPN** — Tailscale, WireGuard, Netbird (private, no public exposure).
- **Reverse proxy with TLS** — Caddy, nginx, or Traefik terminating HTTPS in front
  of `127.0.0.1:8421`.
- **SSH tunnel** — `ssh -L 8421:localhost:8421 your-mac` for occasional access.

Whenever the port leaves `localhost`, **set an access token** as defense-in-depth
on top of (never instead of) that layer:

```bash
export PHOTO_SAVER_TOKEN="$(openssl rand -hex 16)"
# then launch the app, or: PHOTO_SAVER_TOKEN=… bash install-agent.sh
```

When `PHOTO_SAVER_TOKEN` is set, **every** request must present it via
`?token=…` (the cookie is then set for the session), an `X-Auth-Token` header, or
the `ps_token` cookie. Requests without it get `401`. The token is a plaintext
shared secret compared on each request — treat it as a thin gate, not real auth,
and only ever send it over a connection you already trust (VPN or TLS).

**Never put this on the raw public internet** — no port-forward, no Tailscale
Funnel. The endpoints are destructive and there's no account model to fall back on.

## What it can do

- Move photos/videos to the **Photos trash** (recoverable from Recently Deleted
  until you empty it).
- Re-encode videos to H.265 and trash the originals.
- Run AppleScript to open items in Photos / drive a date search (sanitized,
  UUID/date-only input).

It does **not** make external network calls at runtime, collect analytics, or
upload anything. See [Privacy](README.md#privacy).

## Reporting a vulnerability

Please open a **private security advisory** on GitHub (Security → Report a
vulnerability) rather than a public issue. I'll respond as soon as I can.
