# Building iCloud Photo Storage Saver

The distributed app is **self-contained**: a private Python and the
`ffmpeg` / `ffprobe` / `exiftool` / `osxphotos` binaries are staged *inside* the
`.app` at build time, so the end user installs nothing — no Terminal, no
Homebrew, no `uv`. They just drag the app to Applications and open it.

> **Build machine:** macOS on **Apple Silicon (arm64)**. The build shells out to
> macOS-only tools (`hdiutil`, `lsregister`, `osascript`) and stages arm64
> binaries, so it can't be produced on Linux or for Intel from this script as-is.

## Quick build

```bash
bash setup.sh              # build the self-contained .app into /Applications
# or, for the distributable disk image:
bash build_dmg.sh          # → dist/iCloudPhotoStorageSaver.dmg
```

`build_dmg.sh` calls `setup.sh --build-only`, which runs `stage_runtime` to
assemble `Contents/Resources/`:

- `python/` — a relocatable [python-build-standalone] interpreter (fetched via
  `uv`) with `osxphotos`, `pillow`, `pillow-heif`, `pyobjc-framework-Photos`
  pip-installed into it.
- `bin/osxphotos` — a small relocatable wrapper (`python -m osxphotos`); we avoid
  pip's console script because its shebang bakes in the build path.
- `bin/ffmpeg`, `bin/ffprobe` — static arm64 builds.
- `bin/exiftool` + `bin/lib/` — the ExifTool Perl distribution (uses the system
  `/usr/bin/perl`; nothing to install).

The launcher prepends `Resources/bin` to `PATH` and sets `PS_OSXPHOTOS_BIN`, so
`photo_saver.py`'s `ffmpeg` / `ffprobe` / `exiftool` calls and its osxphotos
exporter all resolve to the bundled copies.

Downloads are cached in `.build-cache/` (git-ignored). Bundle size is roughly
**150–300 MB**.

## Overriding the staged components

The static-binary URLs go stale as upstreams publish new versions. Override them
without editing the script:

| Env var            | Default                                              | What it sets                |
| ------------------ | ---------------------------------------------------- | --------------------------- |
| `PS_PYVER`         | `3.12`                                               | bundled Python version      |
| `PS_FFMPEG_URL`    | osxexperts.net static arm64 zip                      | `ffmpeg` source             |
| `PS_FFPROBE_URL`   | osxexperts.net static arm64 zip                      | `ffprobe` source            |
| `PS_EXIFTOOL_VER`  | `13.10`                                              | ExifTool version            |
| `PS_EXIFTOOL_URL`  | `https://exiftool.org/Image-ExifTool-$VER.tar.gz`    | ExifTool tarball            |
| `PS_THIN_BUILD=1`  | (off)                                                | skip staging — fast dev build that relies on a user-level `osxphotos` (see `setup.sh --deps-only`) |

If a download doesn't contain the expected binary the build fails loudly with the
env var to set.

## Running from source (no bundle)

```bash
bash setup.sh --deps-only                    # user-level osxphotos + libs via uv
~/.local/bin/osxphotos run photo_saver.py    # http://localhost:8421
```

## Signing & notarization (planned — Part B)

The app is **not yet code-signed**, so first launch still needs the
right-click → Open → Open Gatekeeper step. Removing it requires an Apple
**Developer ID Application** certificate and notarization:

1. Sign every nested Mach-O (the bundled `ffmpeg`/`ffprobe`, the Python dylibs
   and C-extension `.so` files), then the `.app`, with the **hardened runtime**
   (`codesign --options runtime`) and an entitlements plist that includes
   `com.apple.security.cs.disable-library-validation` (so the embedded Python can
   load third-party extensions).
2. `xcrun notarytool submit dist/…dmg --wait`, then `xcrun stapler staple`.

This step runs on your Mac with your Developer ID and an app-specific password
(read from env, never committed). It will land as a `sign_and_notarize.sh` script
wired into `build_dmg.sh`.

[python-build-standalone]: https://github.com/astral-sh/python-build-standalone
