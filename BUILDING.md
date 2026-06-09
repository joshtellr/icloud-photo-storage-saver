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
| `PS_EXIFTOOL_VER`  | auto (from `exiftool.org/ver.txt`)                   | pin a specific ExifTool version |
| `PS_EXIFTOOL_URL`  | exiftool.org, then GitHub tag mirror                 | ExifTool tarball            |
| `PS_THIN_BUILD=1`  | (off)                                                | skip staging — fast dev build that relies on a user-level `osxphotos` (see `setup.sh --deps-only`) |

If a download doesn't contain the expected binary the build fails loudly with the
env var to set.

## Running from source (no bundle)

```bash
bash setup.sh --deps-only                    # user-level osxphotos + libs via uv
~/.local/bin/osxphotos run photo_saver.py    # http://localhost:8421
```

## Signing & notarization

Release builds are **Developer ID signed + Apple notarized**, so they open with no Gatekeeper
warning (no right-click step). `build_dmg.sh` runs this automatically when a Developer ID cert
is in your keychain — it calls `sign_and_notarize.sh`, which:

1. Signs every nested Mach-O inside-out (bundled `ffmpeg`/`ffprobe`, the Python interpreter and
   its C-extension `.so`/`.dylib`) with the **hardened runtime** + secure timestamp, applying
   `entitlements.plist` to the executables. The entitlements include
   `com.apple.security.cs.disable-library-validation` (so the embedded Python can load
   third-party extensions) and `com.apple.security.automation.apple-events` (drive Photos).
2. Signs the `.app` last. The bundle's main executable is a tiny compiled Mach-O stub
   (`launcher.c` → `Contents/MacOS/launcher`, which execs `Resources/launch.sh`) — a shell
   script can't carry entitlements/hardened-runtime, which would block notarization.
3. `xcrun notarytool submit … --wait` then `xcrun stapler staple` — for both the app (before
   packaging) and the finished DMG, so the app is valid even on an offline first launch.

### One-time setup

1. Create a **Developer ID Application** certificate. Xcode's "Apple Accounts" pane may only
   offer "Apple Development" — if so, use the portal: generate a CSR
   (`openssl req -new -newkey rsa:2048 -nodes -keyout devid.key -out devid.csr -subj "/CN=Your Name/C=US"`),
   upload it at <https://developer.apple.com/account/resources/certificates/add> → *Developer ID
   Application*, download the `.cer`, then import it paired with the key (note OpenSSL 3 needs
   `-legacy`): `openssl pkcs12 -export -legacy -inkey devid.key -in devid.cer.pem -out id.p12`
   then `security import id.p12 -T /usr/bin/codesign`.
2. Store notary credentials once:
   `xcrun notarytool store-credentials icps-notary --apple-id <id> --team-id <TEAM> --password <app-specific-password>`
   (override the profile name via `NOTARY_PROFILE`).

Then every release is just `bash build_dmg.sh`. Verify with
`spctl -a -vvv -t install dist/iCloudPhotoStorageSaver.dmg` → *accepted, Notarized Developer ID*.

[python-build-standalone]: https://github.com/astral-sh/python-build-standalone
