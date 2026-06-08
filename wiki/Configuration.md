# Configuration

Everything is configured three ways: **CLI flags**, **environment variables**, and
**URL hashes** in the browser. There is no config file.

---

## CLI flags

Pass these when running directly (`~/.local/bin/osxphotos run photo_saver.py …`):

| Flag | Default | What it does |
|------|---------|--------------|
| `--rescan` | off | Ignore the hash cache and recompute everything from scratch |
| `--threshold N` | `10` | Max Hamming distance to consider two items duplicates |
| `--window S` | `60` | Time window (seconds) for near-duplicate grouping |
| `--no-open` | off | Don't auto-open the browser on start |

**Tuning duplicate detection**
- Lower `--threshold` → stricter matching (fewer, more-confident duplicates).
- Higher `--threshold` → looser matching (more candidates, more to review).
- `--window` groups items taken close together in time (e.g. burst shots); widen it if
  near-dups are being missed, narrow it to reduce false groupings.

---

## Environment variables

| Variable | Effect |
|----------|--------|
| `PHOTO_SAVER_TOKEN` | When set, **every** request must present this token (`?token=…`, `X-Auth-Token` header, or `ps_token` cookie) or get `401`. Set it **whenever the port leaves loopback**. See [[Security]] / [[Remote Access]]. |

```bash
export PHOTO_SAVER_TOKEN="$(openssl rand -hex 16)"; echo "$PHOTO_SAVER_TOKEN"
# or with the launch agent:
PHOTO_SAVER_TOKEN=yourtoken bash install-agent.sh
```

---

## Fixed values

| Setting | Value | Notes |
|---------|-------|-------|
| Bind address | `127.0.0.1` | Loopback only by design; expose via VPN/proxy, not by changing this |
| Port | `8421` | `http://localhost:8421` |

---

## URL hashes (browser)

Append these to the app URL to jump around or toggle display:

| Hash | Effect |
|------|--------|
| *(none)* | Duplicates tab (default) |
| `#files` | Large Files tab |
| `#activity` | Activity dashboard |
| `#blur` | Blur all thumbnails (handy for screenshots / screen-sharing) |

Example: `http://localhost:8421/#files` or `http://localhost:8421/?token=…#blur`.

---

### See also

- [[Remote Access]] — using the token in practice.
- [[Usage Guide]] — what each tab does.
- [[Architecture]] — where these settings are read in the code.
