# Remote Access (optional, advanced)

By default the app binds to **`127.0.0.1` (loopback) only** — it is **not reachable**
off your Mac. To use it from your phone or another machine, front it with **your own
access layer**, the same way you'd expose any self-hosted service. It's deliberately
**access-layer agnostic**: pick whichever you already run.

> 🔒 **Read [[Security]] before exposing anything.** The endpoints are destructive and
> there is no user-account model. Whenever the port leaves loopback, set a token first.

---

## Choose an access layer

| Approach | Examples | Notes |
|----------|----------|-------|
| **Mesh VPN** | Tailscale, WireGuard, Netbird | Tailnet-only, no public exposure. Recommended. |
| **Reverse proxy with TLS** | Caddy, nginx, Traefik | Terminates HTTPS in front of `127.0.0.1:8421`. |
| **SSH tunnel** | `ssh -L 8421:localhost:8421 your-mac` | Great for one-off access. |

**Never put it on the raw public internet** — no port-forward, no Tailscale Funnel.

---

## Always set an access token when leaving loopback

The token is a thin **shared-secret gate** — defense-in-depth on top of (never instead
of) your VPN/TLS.

```bash
export PHOTO_SAVER_TOKEN="$(openssl rand -hex 16)"; echo "$PHOTO_SAVER_TOKEN"
# launch the app (or install-agent.sh) with that env var set, then visit:
#   https://<your-host>/?token=YOUR_TOKEN   (sets a cookie; later visits don't need it)
```

When `PHOTO_SAVER_TOKEN` is set, **every** request must present the token one of three
ways, or it gets **`401`**:

1. `?token=…` in the URL (this then sets the `ps_token` cookie for the session),
2. an `X-Auth-Token` request header, or
3. the `ps_token` cookie.

The token is a plaintext secret compared on each request — only ever send it over a
connection you already trust (VPN or TLS).

---

## Running it always-on for phone access

```bash
PHOTO_SAVER_TOKEN=yourtoken bash install-agent.sh   # start at login, with a token
bash install-agent.sh --uninstall                   # remove it
```

This keeps the server running whenever your Mac is on, so your phone (on the same
tailnet / behind the same proxy) can reach it anytime.

---

## Quick recipes

**Tailscale (simplest private option)**
1. Install Tailscale on the Mac and the phone; join the same tailnet.
2. Start the app with `PHOTO_SAVER_TOKEN` set.
3. From the phone visit `http://<mac-tailscale-name>:8421/?token=YOUR_TOKEN`.
   *(Do not use Tailscale **Funnel** — that's public exposure.)*

**Caddy reverse proxy with automatic HTTPS**
```caddyfile
photos.example-internal.ts.net {
    reverse_proxy 127.0.0.1:8421
}
```
Then visit `https://photos.example-internal.ts.net/?token=YOUR_TOKEN`.

**One-off SSH tunnel (nothing left running)**
```bash
ssh -L 8421:localhost:8421 your-mac
# now open http://localhost:8421 on the local machine
```

---

### See also

- [[Security]] — the full threat model and what the token does / doesn't protect.
- [[Configuration]] — `PHOTO_SAVER_TOKEN` and other settings.
- [[Installation#run-it-always-on-optional]] — launch-agent setup.
