#!/bin/bash
#
# Install (or remove) a LaunchAgent so iCloud Photo Storage Saver runs in the
# background and starts at login — so you can reach it from your phone anytime
# the Mac is on.
#
#   bash install-agent.sh                 # install / update the agent
#   PHOTO_SAVER_TOKEN=secret bash install-agent.sh   # install with an access token
#   bash install-agent.sh --uninstall     # remove the agent
#
set -e

LABEL="com.local.icloudphotostoragesaver"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_N="$(id -u)"

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✓ Removed the always-on agent."
  exit 0
fi

# Find the app script: bundled app first, then a source checkout.
SCRIPT=""
for c in \
  "/Applications/iCloud Photo Storage Saver.app/Contents/Resources/photo_saver.py" \
  "$HOME/Applications/iCloud Photo Storage Saver.app/Contents/Resources/photo_saver.py" \
  "$(cd "$(dirname "$0")" && pwd)/photo_saver.py" \
  "$HOME/photo_saver.py"; do
  [ -f "$c" ] && { SCRIPT="$c"; break; }
done
if [ -z "$SCRIPT" ]; then
  echo "✗ photo_saver.py not found. Install the app (setup.sh) or run from the repo."
  exit 1
fi

# Prefer the self-contained app's bundled runtime; fall back to a user-level
# osxphotos for source/dev installs. Build the right ProgramArguments + env.
RES="$(cd "$(dirname "$SCRIPT")" && pwd)"
BUNDLED_PY="$RES/python/bin/python3"
ENV_ENTRIES=""
[ -n "$PHOTO_SAVER_TOKEN" ] && ENV_ENTRIES="$ENV_ENTRIES<key>PHOTO_SAVER_TOKEN</key><string>$PHOTO_SAVER_TOKEN</string>"
[ -n "$PHOTO_SAVER_TOKEN" ] && echo "→ Agent will require token: $PHOTO_SAVER_TOKEN"

if [ -x "$BUNDLED_PY" ]; then
  PROG_ARGS="    <string>$BUNDLED_PY</string><string>$SCRIPT</string><string>--no-open</string>"
  # Bundled tools on PATH + point the compressor's exporter at bundled osxphotos.
  ENV_ENTRIES="$ENV_ENTRIES<key>PATH</key><string>$RES/bin:/usr/bin:/bin:/usr/sbin:/sbin</string><key>PS_OSXPHOTOS_BIN</key><string>$RES/bin/osxphotos</string>"
  echo "→ Using bundled runtime: $BUNDLED_PY"
else
  OSX="$HOME/.local/bin/osxphotos"; [ -x "$OSX" ] || OSX="$(command -v osxphotos || true)"
  if [ -z "$OSX" ]; then echo "✗ osxphotos not found — install the app (setup.sh) or run setup.sh --deps-only."; exit 1; fi
  PROG_ARGS="    <string>$OSX</string><string>run</string><string>$SCRIPT</string><string>--no-open</string>"
  echo "→ Using user-level osxphotos: $OSX"
fi

ENV_BLOCK=""
[ -n "$ENV_ENTRIES" ] && ENV_BLOCK="  <key>EnvironmentVariables</key>
  <dict>$ENV_ENTRIES</dict>"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
$PROG_ARGS
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/.photos_dedup.log</string>
  <key>StandardErrorPath</key><string>$HOME/.photos_dedup.log</string>
$ENV_BLOCK
</dict>
</plist>
PLIST

launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$PLIST"
echo "✓ Always-on agent installed. It starts at login and serves $SCRIPT --no-open"
echo "  Open the app at http://localhost:8421 . Remove it with: bash install-agent.sh --uninstall"
