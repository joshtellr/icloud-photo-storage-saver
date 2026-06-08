#!/bin/bash
#
# iCloud Photo Storage Saver — installer / app builder.
#
#   bash setup.sh               # build the self-contained .app in /Applications
#   bash setup.sh --build-only  # only (re)build the .app  (APP_DEST overrides the location)
#   bash setup.sh --deps-only   # install a USER-level dev environment (for `osxphotos run`)
#
# The distributed .app is fully self-contained: at BUILD time we stage a
# relocatable Python (with osxphotos / pillow / pillow-heif / PyObjC-Photos) plus
# static ffmpeg / ffprobe / exiftool into Contents/Resources. The end user installs
# nothing — no Terminal, no Homebrew, no uv. (Build machine = your Mac, arm64.)
#
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DEST="${APP_DEST:-/Applications}"
APP="$APP_DEST/iCloud Photo Storage Saver.app"
MODE="${1:-all}"

# Python version + static-binary sources. Override the URLs via env if they go
# stale (see BUILDING.md). arm64-only builds.
PYVER="${PS_PYVER:-3.12}"
FFMPEG_URL="${PS_FFMPEG_URL:-https://www.osxexperts.net/ffmpeg71arm.zip}"
FFPROBE_URL="${PS_FFPROBE_URL:-https://www.osxexperts.net/ffprobe71arm.zip}"
EXIFTOOL_VER="${PS_EXIFTOOL_VER:-}"   # empty → auto-detect current version (exiftool.org/ver.txt)

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python tool manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
}

# ─── Optional: a user-level dev environment, for running from source ──────────
# Only needed if you want `~/.local/bin/osxphotos run photo_saver.py` (no .app).
install_deps() {
  echo "── Installing user-level dev dependencies ─────────────────"
  ensure_uv
  echo "Installing osxphotos…"
  uv tool install --quiet osxphotos || uv tool upgrade osxphotos || true
  OSX="$HOME/.local/bin/osxphotos"; [ -x "$OSX" ] || OSX="$(command -v osxphotos || true)"
  if [ -z "$OSX" ]; then echo "✗ osxphotos install failed"; exit 1; fi
  echo "Installing image libraries (pillow, pillow-heif, PhotoKit)…"
  "$OSX" install pillow pillow-heif pyobjc-framework-Photos >/dev/null 2>&1 \
    || "$OSX" install pillow pillow-heif
  echo "Installing ffmpeg + exiftool (for video compression)…"
  if command -v brew >/dev/null 2>&1; then
    brew list ffmpeg   >/dev/null 2>&1 || brew install ffmpeg
    brew list exiftool >/dev/null 2>&1 || brew install exiftool
  else
    echo "⚠ Homebrew not found — install ffmpeg + exiftool manually for Compress."
  fi
  echo "✓ Dev dependencies ready"
}

# ─── Stage the self-contained runtime into the .app (build machine only) ──────
stage_runtime() {
  local RES="$1"
  echo "── Staging self-contained runtime (Python $PYVER, arm64) ──"
  ensure_uv

  # 1. Relocatable Python (python-build-standalone) copied into the bundle, then
  #    the pure-/binary-wheel deps installed into it. The copy makes it portable
  #    to any Mac — nothing references the build machine.
  echo "→ Python + packages…"
  uv python install "$PYVER"
  local PYBIN PYROOT PY
  PYBIN="$(uv python find "$PYVER")"
  PYROOT="$(cd "$(dirname "$PYBIN")/.." && pwd)"   # standalone layout: <root>/bin/python3
  rm -rf "$RES/python"
  cp -R "$PYROOT" "$RES/python"
  PY="$RES/python/bin/python3"
  # This is now OUR private copy to modify, so drop uv's PEP-668 "externally
  # managed" marker that otherwise makes pip refuse to install into it.
  find "$RES/python/lib" -name EXTERNALLY-MANAGED -delete 2>/dev/null || true
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet osxphotos pillow pillow-heif pyobjc-framework-Photos

  # 2. Relocatable osxphotos wrapper. pip's console script bakes in an absolute
  #    shebang (the build path), which breaks once the .app moves — so we call
  #    the module through the bundled python instead.
  mkdir -p "$RES/bin"
  cat > "$RES/bin/osxphotos" <<'WRAP'
#!/bin/bash
exec "$(cd "$(dirname "$0")/../python/bin" && pwd)/python3" -m osxphotos "$@"
WRAP
  chmod +x "$RES/bin/osxphotos"

  # 3. Static ffmpeg / ffprobe / exiftool.
  stage_binaries "$RES/bin"

  # 4. Verify everything the app shells out to is present + runnable.
  local ok=1
  for b in python/bin/python3 bin/osxphotos bin/ffmpeg bin/ffprobe bin/exiftool; do
    [ -x "$RES/$b" ] || { echo "✗ missing/!exec: $b"; ok=0; }
  done
  [ "$ok" = 1 ] || { echo "✗ runtime staging incomplete — see BUILDING.md"; exit 1; }
  echo "✓ Runtime staged ($(du -sh "$RES/python" "$RES/bin" 2>/dev/null | awk '{s=$1} END{print s}') incl. python)"
}

stage_binaries() {
  local BIN="$1"
  local CACHE="$SRC_DIR/.build-cache"; mkdir -p "$CACHE"
  local tmp; tmp="$(mktemp -d)"

  echo "→ ffmpeg / ffprobe…"
  _fetch_unzip_one "$FFMPEG_URL"  "$CACHE/ffmpeg.zip"  "$tmp" "ffmpeg"  "$BIN/ffmpeg"
  _fetch_unzip_one "$FFPROBE_URL" "$CACHE/ffprobe.zip" "$tmp" "ffprobe" "$BIN/ffprobe"
  chmod +x "$BIN/ffmpeg" "$BIN/ffprobe"

  # exiftool: pin via PS_EXIFTOOL_VER, else auto-detect the current production
  # version (exiftool.org serves only the latest at the predictable path, so a
  # hard-pinned version 404s once a newer one ships).
  local EVER="$EXIFTOOL_VER"
  [ -n "$EVER" ] || EVER="$(curl -fsSL https://exiftool.org/ver.txt 2>/dev/null | tr -d '[:space:]')"
  [ -n "$EVER" ] || { echo "✗ couldn't determine exiftool version — set PS_EXIFTOOL_VER"; exit 1; }
  echo "→ exiftool $EVER…"
  local ETAR="$CACHE/exiftool-$EVER.tar.gz"
  if [ ! -s "$ETAR" ]; then
    # exiftool.org (current only) first, then the GitHub tag mirror (all versions).
    curl -fsSL "${PS_EXIFTOOL_URL:-https://exiftool.org/Image-ExifTool-$EVER.tar.gz}" -o "$ETAR" \
      || curl -fsSL "https://github.com/exiftool/exiftool/archive/refs/tags/$EVER.tar.gz" -o "$ETAR" \
      || { echo "✗ exiftool $EVER download failed — set PS_EXIFTOOL_VER or PS_EXIFTOOL_URL"; rm -f "$ETAR"; exit 1; }
  fi
  rm -rf "$tmp/exiftool-src"; mkdir -p "$tmp/exiftool-src"
  # exiftool is a perl script that finds its `lib` next to itself; ship both.
  # Both tarballs nest one top dir (Image-ExifTool-* or exiftool-*); strip it.
  tar -xzf "$ETAR" -C "$tmp/exiftool-src" --strip-components=1
  cp "$tmp/exiftool-src/exiftool" "$BIN/exiftool"
  rm -rf "$BIN/lib"; cp -R "$tmp/exiftool-src/lib" "$BIN/lib"
  chmod +x "$BIN/exiftool"

  rm -rf "$tmp"
}

# Download a zip (cached), extract the named binary out of it (it may sit in a
# subdir), and drop it at the destination path.
_fetch_unzip_one() {
  local url="$1" cache="$2" tmp="$3" name="$4" dest="$5"
  [ -f "$cache" ] || curl -fsSL "$url" -o "$cache"
  local d="$tmp/$name"; rm -rf "$d"; mkdir -p "$d"
  unzip -qo "$cache" -d "$d"
  local found; found="$(find "$d" -type f -name "$name" -perm -u+x 2>/dev/null | head -1)"
  [ -n "$found" ] || found="$(find "$d" -type f -name "$name" 2>/dev/null | head -1)"
  [ -n "$found" ] || { echo "✗ '$name' not found in $url — set PS_${name^^}_URL"; exit 1; }
  cp "$found" "$dest"
}

# ─── Build the .app bundle ────────────────────────────────────────────────────
build_app() {
  echo "── Building $APP ─────────────────────"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>iCloud Photo Storage Saver</string>
  <key>CFBundleDisplayName</key><string>iCloud Photo Storage Saver</string>
  <key>CFBundleIdentifier</key><string>com.local.icloudphotostoragesaver</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

  cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
# Self-locating launcher for iCloud Photo Storage Saver.
# The bundle is self-contained: bundled Python + ffmpeg/ffprobe/exiftool/osxphotos.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG="$HOME/.photos_dedup.log"
PY="$RES/python/bin/python3"
SCRIPT="$RES/photo_saver.py"

# Bundled tools win on PATH; point the compressor's exporter at our osxphotos.
export PATH="$RES/bin:$PATH"
export PS_OSXPHOTOS_BIN="$RES/bin/osxphotos"

# Already running? Just (re)open the browser.
if curl -s --max-time 1 http://localhost:8421/status >/dev/null 2>&1; then
  open "http://localhost:8421"; exit 0
fi

if [ -x "$PY" ]; then
  exec "$PY" "$SCRIPT" >> "$LOG" 2>&1
fi

# Fallback for a thin/dev build (runtime not staged): use a user-level osxphotos.
OSX="$HOME/.local/bin/osxphotos"; [ -x "$OSX" ] || OSX="$(command -v osxphotos || true)"
[ -f "$SCRIPT" ] || SCRIPT="$HOME/photo_saver.py"
if [ -z "$OSX" ] || [ ! -f "$SCRIPT" ]; then
  osascript -e 'display alert "iCloud Photo Storage Saver" message "This build is missing its bundled runtime. Rebuild it with setup.sh."' >/dev/null 2>&1
  exit 1
fi
exec "$OSX" run "$SCRIPT" >> "$LOG" 2>&1
LAUNCHER
  chmod +x "$APP/Contents/MacOS/launcher"

  # Bundle the app script.
  cp "$SRC_DIR/photo_saver.py" "$APP/Contents/Resources/photo_saver.py"

  # App icon (regenerate if a fresh checkout is missing it).
  if [ ! -f "$SRC_DIR/docs/AppIcon.icns" ] && [ -f "$SRC_DIR/make_app_icon.py" ]; then
    PY_ICON="$(ls "$HOME"/.local/share/uv/tools/osxphotos/bin/python* 2>/dev/null | head -1)"
    [ -x "$PY_ICON" ] || PY_ICON="python3"
    "$PY_ICON" "$SRC_DIR/make_app_icon.py" || true
  fi
  [ -f "$SRC_DIR/docs/AppIcon.icns" ] && cp "$SRC_DIR/docs/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

  # Stage the self-contained runtime (skip with PS_THIN_BUILD=1 for a fast dev build).
  if [ "${PS_THIN_BUILD:-0}" = "1" ]; then
    echo "→ PS_THIN_BUILD=1 — skipping runtime staging (dev build, needs osxphotos on PATH)"
  else
    stage_runtime "$APP/Contents/Resources"
  fi

  xattr -cr "$APP" 2>/dev/null || true
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
  echo "✓ Built $APP"
}

case "$MODE" in
  --deps-only)  install_deps ;;
  --build-only) build_app ;;
  *)            build_app
                echo ""
                echo "── Done ───────────────────────────────────────────────────"
                echo "Launch: open \"$APP\"  (or double-click in Applications)"
                echo "First run asks for Photos access + Automation — approve both."
                echo "First scan hashes your whole library once (slow); later runs are instant." ;;
esac
