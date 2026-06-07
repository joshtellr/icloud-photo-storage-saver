#!/bin/bash
#
# Build a distributable DMG containing iCloud Photo Storage Saver.app.
# The app is self-contained (bundles photo_saver.py + setup.sh) and installs
# its Python/ffmpeg dependencies on first launch.
#
#   bash build_dmg.sh   →   dist/iCloudPhotoStorageSaver.dmg
#
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="iCloud Photo Storage Saver"
STAGE="$(mktemp -d)/dmg"
DIST="$SRC_DIR/dist"
DMG="$DIST/iCloudPhotoStorageSaver.dmg"

mkdir -p "$STAGE" "$DIST"

# Build the .app straight into the staging folder.
APP_DEST="$STAGE" bash "$SRC_DIR/setup.sh" --build-only

# Drag-to-install affordance + a short readme inside the DMG.
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/READ ME FIRST.txt" <<'TXT'
iCloud Photo Storage Saver
==========================
1. Drag "iCloud Photo Storage Saver" onto the Applications folder.
2. The app is not code-signed, so the first time:
   right-click it → Open → Open (this is only needed once).
3. On first launch it installs its dependencies in Terminal (a few minutes),
   then asks for Photos access + Automation permission — approve both.
4. It opens http://localhost:8421 in your browser. That's the app.

Everything runs locally on your Mac. No photo leaves the machine.
Source & docs: https://github.com/joshtellr/icloud-photo-storage-saver
TXT

rm -f "$DMG"
hdiutil create -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$(dirname "$STAGE")"
echo ""
echo "✓ Built $DMG"
