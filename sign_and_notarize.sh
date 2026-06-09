#!/bin/bash
#
# sign-and-notarize.sh — Developer ID sign + Apple notarize + staple.
#
# Works on a .app (inside-out code-signing with hardened runtime + entitlements)
# or a .dmg (sign the disk image). In both cases it then notarizes and staples,
# UNLESS the signing identity is not a "Developer ID Application" cert (e.g. an
# "Apple Development" cert), in which case it signs only and warns — handy for a
# local dry-run of the signing logic before the real cert exists.
#
#   bash sign-and-notarize.sh ["/Applications/iCloud Photo Storage Saver.app"]
#   bash sign-and-notarize.sh dist/iCloudPhotoStorageSaver.dmg
#
# Config (env):
#   CODESIGN_IDENTITY   signing identity; auto-detects Developer ID if unset.
#   NOTARY_PROFILE      notarytool keychain profile name (default: icps-notary).
#                       Create once with:
#                         xcrun notarytool store-credentials icps-notary \
#                           --key AuthKey_XXX.p8 --key-id KEYID --issuer ISSUER_UUID
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-/Applications/iCloud Photo Storage Saver.app}"
ENTITLEMENTS="$SRC_DIR/entitlements.plist"
NOTARY_PROFILE="${NOTARY_PROFILE:-icps-notary}"

[ -e "$TARGET" ] || { echo "✗ no such target: $TARGET"; exit 1; }

# --- resolve a signing identity ----------------------------------------------
IDENTITY="${CODESIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning \
    | awk -F'"' '/Developer ID Application/{print $2; exit}')"
fi
if [ -z "$IDENTITY" ]; then
  # Fall back to whatever single identity exists, for a sign-only dry run.
  IDENTITY="$(security find-identity -v -p codesigning \
    | awk -F'"' '/[A-Za-z]/{print $2; exit}')"
fi
[ -n "$IDENTITY" ] || { echo "✗ no code-signing identity in the keychain"; exit 1; }

SIGN_ONLY="${SIGN_ONLY:-0}"
CAN_NOTARIZE=0
case "$IDENTITY" in
  "Developer ID Application"*) CAN_NOTARIZE=1 ;;
  *) echo "⚠ '$IDENTITY' is not a Developer ID Application cert — will SIGN ONLY."
     echo "  Create a Developer ID Application cert to notarize (see plan/README)." ;;
esac
# Caller asked to sign without notarizing (e.g. the DMG build signs the app, then
# notarizes the DMG that contains it — one notary round-trip instead of two).
[ "$SIGN_ONLY" = 1 ] && CAN_NOTARIZE=0
echo "→ identity: $IDENTITY"

sign()     { codesign --force --options runtime --timestamp -s "$IDENTITY" "$@"; }
sign_ent() { codesign --force --options runtime --timestamp \
               --entitlements "$ENTITLEMENTS" -s "$IDENTITY" "$@"; }

sign_app() {
  local app="$1"
  echo "→ signing nested Mach-O (inside-out)…"
  # Sign every nested Mach-O. Dylibs/.so don't use entitlements; the Python
  # interpreter (and other executables) get the entitlements so the embedded
  # runtime can load C-extensions and drive Photos under the hardened runtime.
  local n=0
  while IFS= read -r -d '' f; do
    local kind; kind="$(file -b "$f")"
    case "$kind" in
      *Mach-O*executable*) sign_ent "$f"; n=$((n+1)) ;;
      *Mach-O*)            sign "$f";     n=$((n+1)) ;;
    esac
  done < <(find "$app" -type f -print0)
  echo "  signed $n Mach-O objects"

  echo "→ signing the app bundle with entitlements + hardened runtime…"
  sign_ent "$app"

  echo "→ verifying signature…"
  codesign --verify --deep --strict --verbose=2 "$app"
}

notarize() {            # $1 = path to submit (zip or dmg)
  echo "→ notarizing $1 (this can take a few minutes)…"
  xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait
}

case "$TARGET" in
  *.app|*.app/)
    sign_app "$TARGET"
    if [ "$CAN_NOTARIZE" = 1 ]; then
      TMP="$(mktemp -d)"
      ditto -c -k --keepParent "$TARGET" "$TMP/app.zip"
      notarize "$TMP/app.zip"
      echo "→ stapling the app…"
      xcrun stapler staple "$TARGET"
      rm -rf "$TMP"
      echo "✓ app signed, notarized, stapled"
    elif [ "$SIGN_ONLY" = 1 ]; then
      echo "✓ app signed (notarization deferred — SIGN_ONLY)"
    else
      echo "✓ app signed (not notarized — non–Developer-ID identity)"
    fi
    ;;
  *.dmg)
    echo "→ signing the DMG…"
    codesign --force --timestamp -s "$IDENTITY" "$TARGET"
    if [ "$CAN_NOTARIZE" = 1 ]; then
      notarize "$TARGET"
      echo "→ stapling the DMG…"
      xcrun stapler staple "$TARGET"
      echo "✓ DMG signed, notarized, stapled"
    else
      echo "✓ DMG signed (not notarized — non–Developer-ID identity)"
    fi
    ;;
  *)
    echo "✗ target must be a .app or .dmg"; exit 1 ;;
esac

# --- final assessment ---------------------------------------------------------
echo "── Gatekeeper assessment ──"
case "$TARGET" in
  *.dmg) spctl -a -vvv -t install "$TARGET" 2>&1 || true
         xcrun stapler validate "$TARGET" 2>&1 || true ;;
  *)     spctl -a -vvv "$TARGET" 2>&1 || true ;;
esac
