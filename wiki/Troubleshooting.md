# Troubleshooting

Common issues and how to resolve them. If your problem isn't here, check the
[[FAQ]] or open an
[issue](https://github.com/joshtellr/icloud-photo-storage-saver/issues).

---

## Install & launch

### "The app can't be opened because it's from an unidentified developer"
It's not code-signed yet. **Right-click the app → Open → Open** (once). After that it
launches normally. See [[Installation#option-1--install-from-the-dmg-recommended]].

### First launch sits in Terminal for a few minutes
Expected — it's installing dependencies (`uv`, `osxphotos`, Pillow, `ffmpeg`,
`exiftool`). Let it finish; it only happens on first run.

### The browser didn't open
The server may still be up at `http://localhost:8421`. Open it manually. If you ran
with `--no-open`, that's why. To free the port if something's stuck, quit the app (the
UI has a quit action that hits `/quit`).

---

## Permissions

### Actions fail / nothing gets trashed
The app needs **Photos** access and **Automation** permission. Re-grant them in
**System Settings → Privacy & Security → Photos** and **→ Automation**, then restart
the app. Without **Photos** it can't read the library; without **Automation** it can't
drive the Photos app to open/trash items.

---

## Duplicates

### Too many / too few duplicates detected
Tune matching: lower `--threshold` for stricter (fewer) matches, raise it for looser
ones; adjust `--window` for how close in time items must be. See [[Configuration]].

### Trashed photos reappear after a refresh
This was a **known bug that's been fixed** (commit `4ed2cf6`, with regression tests in
`test_decisions.py` / `test_prune_payloads.py`). Update to the latest version. If it
still happens, open an issue with your version.

### A scan seems stale
Re-run with `--rescan` to ignore the hash cache and recompute from scratch.

---

## Compress

### Compress does nothing / errors
Compression needs **`ffmpeg`** and **`exiftool`** (installed by `setup.sh`, via
Homebrew). Verify they're on PATH: `which ffmpeg exiftool`.

### A video "compressed" but didn't shrink
By design: if the re-encoded file isn't actually smaller, the app **keeps your
original** rather than replacing it with a larger one. Some already-efficient videos
won't benefit from H.265 re-encoding.

### Compression is slow
H.265 re-encoding is CPU-intensive and runs in the background. Track progress via the
compress status; large libraries take time.

---

## Remote access

### `401 Unauthorized` from my phone / proxy
`PHOTO_SAVER_TOKEN` is set, so the request needs the token. Visit
`https://<host>/?token=YOUR_TOKEN` once (it sets the `ps_token` cookie), or send an
`X-Auth-Token` header. See [[Remote Access]].

### I can't reach it from another device at all
That's the default — it binds to `127.0.0.1` only. Put it behind a VPN, reverse proxy,
or SSH tunnel; don't change the bind address. See [[Remote Access]].

---

## Recovery

### I trashed something I wanted to keep
Open the **Photos** app → **Recently Deleted** → select → **Recover**. Items stay there
until you empty the album (or ~30 days pass). Nothing is permanently deleted by this app.

---

### Still stuck?

- Check [[FAQ]].
- Search existing
  [issues](https://github.com/joshtellr/icloud-photo-storage-saver/issues).
- For anything security-related, see [[Security]] (use a private advisory, not a public
  issue).
