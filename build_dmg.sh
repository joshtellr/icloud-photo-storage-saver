#!/bin/bash
#
# Build a polished, distributable DMG containing iCloud Photo Storage Saver.app.
# The DMG opens to a styled Finder window: a background with an arrow, the app
# on the left, the Applications folder on the right — drag across to install.
#
# The app is self-contained (bundles photo_saver.py + setup.sh) and installs its
# Python/ffmpeg dependencies on first launch.
#
#   bash build_dmg.sh   →   dist/iCloudPhotoStorageSaver.dmg
#
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="iCloud Photo Storage Saver"
APP="$NAME.app"
VOL="$NAME"
DIST="$SRC_DIR/dist"
DMG="$DIST/iCloudPhotoStorageSaver.dmg"
BG_TIFF="$SRC_DIR/docs/dmg-background.tiff"

# Window + icon geometry — must match tools/make_dmg_background.py.
WIN_W=620; WIN_H=430
APP_X=165; APPS_X=455; ICON_Y=205
ICON_SIZE=128

mkdir -p "$DIST"

# Regenerate the background art if it's missing (contributors / fresh checkout).
if [ ! -f "$BG_TIFF" ]; then
  PY="$(ls "$HOME"/.local/share/uv/tools/osxphotos/bin/python* 2>/dev/null | head -1)"
  [ -x "$PY" ] || PY="python3"
  echo "→ generating DMG background…"
  "$PY" "$SRC_DIR/tools/make_dmg_background.py"
fi

# Stage the app into a fresh read-write DMG we can style, then compress at the end.
STAGE="$(mktemp -d)/stage"
mkdir -p "$STAGE"
APP_DEST="$STAGE" bash "$SRC_DIR/setup.sh" --build-only
mkdir -p "$STAGE/.background"
cp "$BG_TIFF" "$STAGE/.background/background.tiff"
ln -s /Applications "$STAGE/Applications"

RW="$(mktemp -d)/rw.dmg"
SIZE_MB=$(( $(du -sm "$STAGE" | cut -f1) + 40 ))
rm -f "$RW"
hdiutil create -srcfolder "$STAGE" -volname "$VOL" -fs HFS+ \
  -format UDRW -size "${SIZE_MB}m" "$RW" >/dev/null

# Mount it (no Finder window). Use the ACTUAL device + mount point it reports —
# if another volume of the same name is already mounted, ours lands at "… 1".
MOUNT_OUT="$(hdiutil attach -readwrite -noverify -nobrowse "$RW")"
DEV="$(echo "$MOUNT_OUT" | grep -Eo '^/dev/disk[0-9]+' | head -1)"
MNT="$(echo "$MOUNT_OUT" | sed -n 's/.*	\(\/Volumes\/.*\)$/\1/p' | head -1)"
VOLNAME="$(basename "$MNT")"
# Detach the staging volume. Finder keeps the volume busy for a few seconds after
# it closes the styled window, so be patient: retry a plain detach, then -force,
# then fall back to `diskutil eject`. (A silently-failed detach leaves a stray
# mount + Finder window around.)
cleanup() {
  for _ in $(seq 1 12); do
    hdiutil detach "$DEV" >/dev/null 2>&1 && return 0
    sleep 1
  done
  hdiutil detach "$DEV" -force >/dev/null 2>&1 && return 0
  diskutil eject "$DEV" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sleep 1

# Style the Finder window: icon view, background, icon size, positions, no chrome.
osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOLNAME"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 140, 200 + $WIN_W, 140 + $WIN_H}
    set vo to the icon view options of container window
    set arrangement of vo to not arranged
    set icon size of vo to $ICON_SIZE
    set text size of vo to 13
    set background picture of vo to POSIX file "$MNT/.background/background.tiff"
    set position of item "$APP" of container window to {$APP_X, $ICON_Y}
    set position of item "Applications" of container window to {$APPS_X, $ICON_Y}
    update without registering applications
    delay 1
    close
  end tell
end tell
APPLESCRIPT

# Persist the view settings, then detach.
sync
cleanup
trap - EXIT

# Compress to the final read-only DMG.
rm -f "$DMG"
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
rm -rf "$(dirname "$STAGE")" "$(dirname "$RW")"

echo ""
echo "✓ Built $DMG"
