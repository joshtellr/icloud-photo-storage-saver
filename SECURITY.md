# Security

## Model

iCloud Photo Storage Saver is a local tool. By default the server binds to
**`127.0.0.1` (loopback) only** — it is not reachable from your network or the
internet unless *you* deliberately expose it.

The web UI talks to local endpoints, some of which are **destructive** (moving
photos to the Photos trash, re-encoding videos, deleting). On loopback these are
unauthenticated by design — the same trust model as any local desktop app.

## Exposing it beyond your Mac (Tailscale, etc.)

If you expose port 8421 beyond `localhost` (e.g. `tailscale serve`), **set an
access token** so other devices/processes can't trigger destructive actions:

```bash
export PHOTO_SAVER_TOKEN="$(openssl rand -hex 16)"
# then launch the app, or: PHOTO_SAVER_TOKEN=… bash install-agent.sh
```

When `PHOTO_SAVER_TOKEN` is set, **every** request must present it via
`?token=…` (the cookie is then set for the session), an `X-Auth-Token` header, or
the `ps_token` cookie. Requests without it get `401`.

Even so, prefer a **private network** like [Tailscale](https://tailscale.com)
(tailnet-only, HTTPS) over public exposure. Do not put this behind a public
`tailscale funnel` or a port-forward without a token, and ideally not at all.

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
