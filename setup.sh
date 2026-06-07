#!/bin/bash
#
# iCloud Photo Storage Saver — installer / app builder.
#
#   bash setup.sh               # install dependencies AND build the .app in /Applications
#   bash setup.sh --deps-only   # only install dependencies (used by the app on first launch)
#   bash setup.sh --build-only  # only (re)build the .app  (APP_DEST overrides the location)
#
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DEST="${APP_DEST:-/Applications}"
APP="$APP_DEST/iCloud Photo Storage Saver.app"
MODE="${1:-all}"

install_deps() {
  echo "── Installing dependencies ───────────────────────────────"
  if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python tool manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
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
    echo "⚠ Homebrew not found — install ffmpeg + exiftool manually for the Compress feature:"
    echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  fi
  echo "✓ Dependencies ready"
}

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
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

  cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
# Self-locating launcher for iCloud Photo Storage Saver.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG="$HOME/.photos_dedup.log"

OSX="$HOME/.local/bin/osxphotos"; [ -x "$OSX" ] || OSX="$(command -v osxphotos || true)"

# First launch with no dependencies → run the bundled installer visibly in Terminal.
if [ -z "$OSX" ] && [ -f "$RES/setup.sh" ]; then
  osascript -e "tell application \"Terminal\" to do script \"bash '$RES/setup.sh' --deps-only; echo; echo 'Setup finished — reopen iCloud Photo Storage Saver.'\"" >/dev/null 2>&1
  osascript -e 'display alert "iCloud Photo Storage Saver" message "First-time setup is installing dependencies in Terminal (a few minutes). When it finishes, open this app again."' >/dev/null 2>&1
  exit 0
fi
if [ -z "$OSX" ]; then
  osascript -e 'display alert "iCloud Photo Storage Saver" message "osxphotos is not installed. Run setup.sh."' >/dev/null 2>&1
  exit 1
fi

# Pick the app script: bundled copy, else a source/dev copy.
SCRIPT="$RES/photo_saver.py"
[ -f "$SCRIPT" ] || SCRIPT="$HOME/photo_saver.py"
[ -f "$SCRIPT" ] || SCRIPT="$HOME/Developer/icloud-photo-storage-saver/photo_saver.py"
if [ ! -f "$SCRIPT" ]; then
  osascript -e 'display alert "iCloud Photo Storage Saver" message "photo_saver.py not found."' >/dev/null 2>&1
  exit 1
fi

# Already running? Just (re)open the browser.
if curl -s --max-time 1 http://localhost:8421/status >/dev/null 2>&1; then
  open "http://localhost:8421"; exit 0
fi
exec "$OSX" run "$SCRIPT" >> "$LOG" 2>&1
LAUNCHER
  chmod +x "$APP/Contents/MacOS/launcher"

  # Bundle the app script + this installer so the .app is self-contained.
  cp "$SRC_DIR/photo_saver.py" "$APP/Contents/Resources/photo_saver.py"
  cp "$SRC_DIR/setup.sh"       "$APP/Contents/Resources/setup.sh"
  chmod +x "$APP/Contents/Resources/setup.sh"

  xattr -cr "$APP" 2>/dev/null || true
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
  echo "✓ Built $APP"
}

case "$MODE" in
  --deps-only)  install_deps ;;
  --build-only) build_app ;;
  *)            install_deps; build_app
                echo ""
                echo "── Done ───────────────────────────────────────────────────"
                echo "Launch: open \"$APP\"  (or double-click in Applications)"
                echo "First run will ask for Photos access + Automation — approve both."
                echo "First scan hashes your whole library once (slow); later runs are instant." ;;
esac
