# Contributing

Thanks for your interest! This is a single-file macOS app (`photo_saver.py`) —
Python HTTP server + an embedded HTML/CSS/JS frontend in one string.

## Dev setup

```bash
git clone https://github.com/joshtellr/icloud-photo-storage-saver.git
cd icloud-photo-storage-saver
bash setup.sh --deps-only         # user-level dev deps (uv, osxphotos, pillow, ffmpeg, exiftool)
~/.local/bin/osxphotos run photo_saver.py     # open http://localhost:8421
```

`bash setup.sh` (no flag) builds the self-contained `.app`; `bash build_dmg.sh`
builds the distributable DMG. See [BUILDING.md](BUILDING.md). Build-time art
generators live in `tools/`.

Useful flags: `--rescan`, `--threshold N`, `--window S`, `--no-open`.
URL hashes: `#files`, `#activity`, `#blur` (blurs thumbnails for screenshots).

## Guidelines

- Keep it dependency-light and **local-only** — no telemetry, no external calls
  at runtime. Privacy is a feature.
- It only acts on the user's **own** library (shared/hidden/unreachable photos
  are read-only). Don't loosen that.
- Destructive actions go through the Photos trash, never permanent deletion.
- Match the existing style in `photo_saver.py`. Test on a real Photos library.
- `bash build_dmg.sh` builds the distributable app/DMG.

## Reporting bugs / ideas

Open an issue. For anything security-related, see [SECURITY.md](SECURITY.md).

By contributing you agree your work is licensed under **GPL-3.0**.
