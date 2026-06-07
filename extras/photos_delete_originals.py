#!/usr/bin/env python3
"""
Delete original Photos after compressed versions have been imported.

Reads filenames from ~/Desktop/Compressed_Photos/, strips '_compressed',
finds the original UUIDs via osxphotos, and moves them to Photos trash.

Usage:
  # Dry run — shows what would be deleted:
  python3 ~/photos_delete_originals.py --dry-run

  # Actually move originals to Photos trash:
  python3 ~/photos_delete_originals.py

  # Then empty Photos trash to free iCloud storage:
  python3 ~/photos_delete_originals.py --empty-trash
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

COMPRESSED_DIR = Path.home() / "Desktop" / "Compressed_Photos"
OSXPHOTOS = Path.home() / ".local" / "bin" / "osxphotos"


def find_compressed_originals():
    """
    Return list of original filenames by stripping '_compressed' from
    files in COMPRESSED_DIR.
    """
    originals = []
    for f in sorted(COMPRESSED_DIR.iterdir()):
        if f.name.startswith("."):
            continue
        # Strip _compressed or _compressed_N suffix
        stem = re.sub(r"_compressed(_\d+)?$", "", f.stem)
        originals.append(stem)
    return originals


def lookup_uuids(filenames):
    """Query osxphotos for UUIDs matching these filenames."""
    print(f"Looking up {len(filenames)} files in Photos library...")
    result = subprocess.run(
        [OSXPHOTOS, "query", "--json"],
        capture_output=True, text=True
    )
    all_photos = json.loads(result.stdout)

    # Build a map: stem → list of (uuid, size)
    stem_map = {}
    for p in all_photos:
        stem = Path(p["original_filename"]).stem
        stem_map.setdefault(stem, []).append((p["uuid"], p["original_filesize"]))

    found = []
    missing = []
    for name in filenames:
        matches = stem_map.get(name, [])
        if matches:
            # If multiple matches, take the largest (most likely the original)
            uuid, size = max(matches, key=lambda x: x[1])
            found.append((name, uuid, size))
        else:
            missing.append(name)

    return found, missing


def fmt(size_bytes):
    if size_bytes >= 1024**3:
        return f"{size_bytes/1024**3:.2f} GB"
    return f"{size_bytes/1024**2:.1f} MB"


def trash_assets(uuids, dry_run):
    """Move PHAssets to Photos trash via osxphotos run (has PhotoKit access)."""
    if dry_run:
        return list(uuids), []

    inline = f"""
import json, sys
try:
    import Photos as PHPhotos
except ImportError:
    print(json.dumps({{"error": "PhotoKit not available"}}))
    sys.exit(1)

uuids = {json.dumps(list(uuids))}
trashed = []
failed = []
for uuid in uuids:
    result = PHPhotos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([uuid], None)
    asset = result.firstObject()
    if not asset:
        failed.append([uuid, "not found in Photos"])
        continue
    def do_delete():
        PHPhotos.PHAssetChangeRequest.deleteAssets_([asset])
    ok, err = PHPhotos.PHPhotoLibrary.sharedPhotoLibrary().performChangesAndWait_error_(do_delete, None)
    if ok:
        trashed.append(uuid)
    else:
        failed.append([uuid, str(err) if err else "unknown"])
print(json.dumps({{"trashed": trashed, "failed": failed}}))
"""

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(inline)
        tmp = Path(f.name)

    try:
        result = subprocess.run(
            [OSXPHOTOS, "run", str(tmp)],
            capture_output=True, text=True
        )
    finally:
        tmp.unlink(missing_ok=True)

    for line in reversed(result.stdout.splitlines()):
        try:
            data = json.loads(line)
            if "error" in data:
                print(f"Error: {data['error']}")
                return [], [(u, data["error"]) for u in uuids]
            if "trashed" in data:
                return data["trashed"], [(u, r) for u, r in data["failed"]]
        except (json.JSONDecodeError, KeyError):
            continue

    print(f"osxphotos run failed.\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return [], [(u, "osxphotos run failed") for u in uuids]


def empty_trash():
    """Tell Photos to empty its trash via AppleScript."""
    print("Emptying Photos trash...")
    result = subprocess.run(
        ["osascript", "-e", 'tell application "Photos"\nempty trash\nend tell'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Photos trash emptied — iCloud will sync deletions.")
    else:
        print(f"Failed: {result.stderr.strip()}")
        print("Open Photos → Photos menu → Empty Trash to do it manually.")


def main():
    parser = argparse.ArgumentParser(description="Delete Photos originals after compression")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without deleting")
    parser.add_argument("--empty-trash", action="store_true",
                        help="Empty Photos trash after deleting (frees iCloud storage)")
    args = parser.parse_args()

    if not COMPRESSED_DIR.exists() or not any(COMPRESSED_DIR.iterdir()):
        print(f"No compressed files found in {COMPRESSED_DIR}")
        print("Run photos_compress.py first.")
        return

    # Find what was compressed
    original_names = find_compressed_originals()
    if not original_names:
        print("No compressed files found.")
        return

    print(f"Found {len(original_names)} compressed file(s) in {COMPRESSED_DIR}")

    # Look up UUIDs
    found, missing = lookup_uuids(original_names)

    if missing:
        print(f"\nCould not find {len(missing)} file(s) in Photos (may have been deleted already):")
        for name in missing:
            print(f"  {name}")

    if not found:
        print("Nothing to delete.")
        return

    total_size = sum(size for _, _, size in found)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Will move {len(found)} original(s) to Photos trash")
    print(f"Space to reclaim: {fmt(total_size)}\n")
    print(f"  {'Filename':<40}  {'Size':>10}  UUID")
    print("  " + "─" * 70)
    for name, uuid, size in found:
        print(f"  {name:<40}  {fmt(size):>10}  {uuid}")
    print()

    if not args.dry_run:
        confirm = input(f"Move {len(found)} original(s) to Photos trash? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Cancelled.")
            return

    uuids = [uuid for _, uuid, _ in found]
    trashed, failed = trash_assets(uuids, args.dry_run)

    if args.dry_run:
        print(f"Dry run: would trash {len(trashed)} file(s).")
        print("Run without --dry-run to actually delete.")
        return

    print(f"\nMoved to trash: {len(trashed)}  |  Failed: {len(failed)}")
    if failed:
        for uuid, reason in failed:
            name = next((n for n, u, _ in found if u == uuid), uuid)
            print(f"  FAILED: {name} — {reason}")

    if trashed and args.empty_trash:
        empty_trash()
    elif trashed:
        print("\nOriginals are now in Photos trash.")
        print("To permanently delete and free iCloud storage, either:")
        print("  python3 ~/photos_delete_originals.py --empty-trash")
        print("  — or — open Photos → Photos menu → Empty Trash")


if __name__ == "__main__":
    main()
