# Installation

There are three ways to run iCloud Photo Storage Saver. The **`.dmg`** is the easiest;
the source install is best if you want to develop or audit the code.

> **Before you start:** macOS 11 (Big Sur) or newer, the Apple **Photos** app, and an
> active Photos library. See [[FAQ]] if you're unsure whether your setup qualifies.

---

## Option 1 — Install from the `.dmg` (recommended)

1. Download the latest **`.dmg`** from
   [Releases](https://github.com/joshtellr/icloud-photo-storage-saver/releases).
2. Open it and drag **iCloud Photo Storage Saver** onto **Applications**.
3. It's **not code-signed yet**, so the first time you must bypass Gatekeeper:
   **right-click the app → Open → Open** (once). After that it opens normally.
4. On first launch it installs its dependencies (a few minutes, shown in Terminal),
   then asks for **Photos access** and **Automation** permission — **approve both**.
   Without them the app can't read your library or move items to the trash.
5. It opens `http://localhost:8421`. That's the app.

> The unsigned right-click step is the only friction. A signed + notarized build needs
> an Apple Developer ID and is a planned future improvement — see [[FAQ]].

---

## Option 2 — Install from source (builds the `.app`)

```bash
git clone https://github.com/joshtellr/icloud-photo-storage-saver.git
cd icloud-photo-storage-saver
bash setup.sh        # installs deps + builds the .app in /Applications
open "/Applications/iCloud Photo Storage Saver.app"
```

`setup.sh` installs everything listed under [Dependencies](#dependencies) and produces
the same app bundle that ships in the DMG.

---

## Option 3 — Run it directly (no app bundle)

Best for development. Skips the bundle and just runs the script:

```bash
~/.local/bin/osxphotos run photo_saver.py      # then open http://localhost:8421
```

You can pass [[Configuration|CLI flags]] here, e.g. `--rescan` or `--no-open`.

---

## Run it always-on (optional)

Keep it running so your phone can reach it anytime the Mac is on. This installs a
launch agent that starts at login and stays running:

```bash
bash install-agent.sh                              # start at login, stay running
PHOTO_SAVER_TOKEN=yourtoken bash install-agent.sh  # …with an access token
bash install-agent.sh --uninstall                  # remove it
```

If you're exposing the app beyond your Mac, **always** set `PHOTO_SAVER_TOKEN` — see
[[Remote Access]] and [[Security]].

---

## Dependencies

`setup.sh` installs these automatically; listed here for transparency:

| Dependency | Purpose |
|------------|---------|
| [`uv`](https://github.com/astral-sh/uv) | Fast Python runner/installer |
| [`osxphotos`](https://github.com/RhetTbull/osxphotos) | Reads the Photos library DB + thumbnails |
| `pillow`, `pillow-heif` | Thumbnail decode/resize (incl. HEIC) |
| `pyobjc-framework-Photos` | PhotoKit access for classification + deletion |
| `ffmpeg` (via Homebrew) | H.265 re-encoding for the Compress feature |
| `exiftool` (via Homebrew) | Copies metadata into the re-encoded video |

---

## Permissions you'll be asked for

- **Photos** — to read your library (analysis) and move items to the Photos trash.
- **Automation** — to drive the Photos app (open an item, run a date search) via
  AppleScript. Input to those paths is sanitized (UUID/date only).

If you decline these, the app will load but most actions will fail. You can re-grant
them in **System Settings → Privacy & Security → Photos / Automation**.

---

### Next steps

- New here? Read [[Features]] then the [[Usage Guide]].
- Want it on your phone? [[Remote Access]].
- Hitting an error? [[Troubleshooting]].
