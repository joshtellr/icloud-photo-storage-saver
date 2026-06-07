#!/usr/bin/env python3
"""
Find, review, and compress large photos/videos in Apple Photos.

Usage:
  python3 photos_compress.py              # interactive review of top 50 files >= 50MB
  python3 photos_compress.py --top 100    # show top 100
  python3 photos_compress.py --min 100    # only files >= 100 MB
  python3 photos_compress.py --auto       # skip review, compress all selected
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

COMPRESSED_DIR = Path.home() / "Desktop" / "Compressed_Photos"
OSXPHOTOS = Path.home() / ".local" / "bin" / "osxphotos"
IS_TTY = sys.stdout.isatty()


def fmt(size_bytes):
    if size_bytes >= 1024**3:
        return f"{size_bytes/1024**3:.2f} GB"
    return f"{size_bytes/1024**2:.1f} MB"


def query_large_files(min_mb, top_n):
    print(f"Scanning Photos library for files >= {min_mb} MB...")
    result = subprocess.run(
        [OSXPHOTOS, "query", f"--min-size", f"{min_mb}MB", "--json"],
        capture_output=True, text=True
    )
    stderr_lines = [l for l in result.stderr.splitlines() if not l.startswith("Processing") and not l.startswith("Using") and not l.startswith("Photos database") and not l.startswith("Done")]
    if stderr_lines:
        print("\n".join(stderr_lines), file=sys.stderr)

    data = json.loads(result.stdout)
    data.sort(key=lambda p: p["original_filesize"], reverse=True)
    return data[:top_n]


def query_by_uuids(uuids):
    args = []
    for u in uuids:
        args += ["--uuid", u]
    result = subprocess.run(
        [OSXPHOTOS, "query"] + args + ["--json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)


def open_quicklook(path):
    subprocess.Popen(
        ["qlmanage", "-p", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def close_quicklook():
    subprocess.run(["pkill", "qlmanage"], capture_output=True)


PHOTOS_LIB = Path.home() / "Pictures" / "Photos Library.photoslibrary"


def find_lib_file(uuid):
    """Locate the file in the Photos library originals folder (any extension)."""
    folder = PHOTOS_LIB / "originals" / uuid[0].upper()
    if folder.exists():
        matches = list(folder.glob(f"{uuid}.*")) + list(folder.glob(f"{uuid.lower()}.*"))
        if matches:
            return matches[0]
    return None


def progress_bar(pct, width=20):
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def eta_str(elapsed, pct):
    if pct < 1:
        return ""
    remaining = elapsed / pct * (100 - pct)
    m, s = divmod(int(remaining), 60)
    return f"  ETA {m}m{s:02d}s" if m else f"  ETA {s}s"


def export_original(uuid, dest_dir, orig_size):
    """Export original from Photos, showing live download/copy progress."""
    process = subprocess.Popen(
        [OSXPHOTOS, "export", "--uuid", uuid, "--download-missing",
         "--skip-edited", str(dest_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    start = time.time()
    print()  # newline after "Downloading..." label

    last_log_pct = -1
    while process.poll() is None:
        lib_file = find_lib_file(uuid)
        if lib_file and lib_file.exists() and orig_size > 0:
            current = lib_file.stat().st_size
            pct = min(current / orig_size * 100, 100)
        else:
            pct = 0

        elapsed = time.time() - start
        if IS_TTY:
            bar = progress_bar(pct)
            print(f"\r      [{bar}] {pct:5.1f}%{eta_str(elapsed, pct)}   ", end="", flush=True)
        else:
            # Log every 1% milestone when redirected to file
            milestone = int(pct)
            if milestone > last_log_pct:
                print(f"      download {milestone}%  elapsed {elapsed:.0f}s", flush=True)
                last_log_pct = milestone
        time.sleep(0.75)

    elapsed = time.time() - start
    exported = [f for f in Path(dest_dir).iterdir()
                if not f.name.startswith(".") and f.suffix.lower() != ".db"]

    if exported:
        m, s = divmod(int(elapsed), 60)
        took = f"{m}m{s:02d}s" if m else f"{s}s"
        if IS_TTY:
            bar = progress_bar(100)
            print(f"\r      [{bar}] 100.0%  took {took}   ")
        else:
            print(f"      download 100%  took {took}")
        return exported[0]
    else:
        print(f"      export failed after {elapsed:.0f}s")
        return None


def get_duration(path):
    """Return video duration in seconds via ffprobe, or 0 if unknown."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def copy_metadata(src, dst):
    """Copy all metadata tags from src to dst using exiftool."""
    subprocess.run(
        ["exiftool", "-TagsFromFile", str(src), "-all:all",
         "-overwrite_original", "-q", str(dst)],
        capture_output=True
    )


def compress_video(src, dst):
    """Re-encode to H.265/HEVC at CRF 28 — typically 50-70% smaller."""
    dst = dst.with_suffix(".mp4")
    duration = get_duration(src)

    cmd = [
        "ffmpeg", "-i", str(src),
        "-c:v", "libx265", "-crf", "28", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-tag:v", "hvc1",
        "-map_metadata", "0",       # copy all input metadata streams
        "-movflags", "+faststart+use_metadata_tags",
        "-progress", "pipe:1",
        "-nostats",
        "-y", str(dst)
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    start = time.time()
    pct = 0.0
    last_log_pct = -1

    print()  # newline after "Compressing..."
    for line in process.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                out_us = int(line.split("=")[1])
                if duration > 0:
                    pct = min(out_us / (duration * 1_000_000) * 100, 100)
                    elapsed = time.time() - start
                    if IS_TTY:
                        eta = eta_str(elapsed, pct)
                        bar = progress_bar(pct)
                        print(f"\r      [{bar}] {pct:5.1f}%{eta}   ", end="", flush=True)
                    else:
                        milestone = int(pct)  # 1% resolution
                        if milestone > last_log_pct:
                            print(f"      compress {milestone}%  elapsed {elapsed:.0f}s", flush=True)
                            last_log_pct = milestone
            except (ValueError, IndexError):
                pass

    process.wait()
    elapsed = time.time() - start
    m, s = divmod(int(elapsed), 60)
    took = f"{m}m{s:02d}s" if m else f"{s}s"
    if process.returncode == 0:
        if IS_TTY:
            print(f"\r      [{'█'*20}] 100.0%  took {took}   ")
        else:
            print(f"      compress 100%  took {took}")
        copy_metadata(src, dst)
    else:
        print(f"      compress failed after {took}")
        return None
    return dst


def compress_photo(src, dst):
    """Reduce JPEG/PNG quality with sips. HEIC files are already efficient."""
    suffix = src.suffix.lower()
    if suffix in (".heic", ".heif"):
        # HEIC is already well-compressed; convert to JPEG at 85% for max compatibility
        dst = dst.with_suffix(".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                        str(src), "--out", str(dst)], capture_output=True)
    elif suffix in (".jpg", ".jpeg"):
        shutil.copy2(src, dst)
        subprocess.run(["sips", "-s", "formatOptions", "75", str(dst)], capture_output=True)
    elif suffix in (".png",):
        dst = dst.with_suffix(".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                        str(src), "--out", str(dst)], capture_output=True)
    elif suffix in (".tif", ".tiff", ".raw", ".dng", ".cr2", ".nef", ".arw"):
        # RAW/TIFF → JPEG at high quality
        dst = dst.with_suffix(".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90",
                        str(src), "--out", str(dst)], capture_output=True)
    else:
        shutil.copy2(src, dst)
    if dst.exists() and dst.stat().st_size > 0:
        copy_metadata(src, dst)
        return dst
    return None


def import_to_photos(path):
    """Import a file into Photos.app using AppleScript."""
    script = f'tell application "Photos" to import POSIX file "{path}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0


def show_table(photos):
    print(f"\n{'#':<5} {'Size':>10}  {'Type':<6}  {'Local':<6}  {'Date':<12}  Filename")
    print("─" * 75)
    for i, p in enumerate(photos, 1):
        ftype = "VIDEO" if p["ismovie"] else "PHOTO"
        date = p["date"][:10] if p["date"] else "unknown"
        local = "yes" if p["path"] else "iCloud"
        name = p["original_filename"]
        if len(name) > 35:
            name = name[:32] + "..."
        print(f"{i:<5} {fmt(p['original_filesize']):>10}  {ftype:<6}  {local:<6}  {date:<12}  {name}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Compress large Photos library files")
    parser.add_argument("--top", type=int, default=50, help="Show top N files (default 50)")
    parser.add_argument("--min", type=float, default=50.0, help="Min size in MB (default 50)")
    parser.add_argument("--auto", action="store_true", help="No prompts — compress and import everything selected")
    parser.add_argument("--all", dest="select_all", action="store_true", help="Auto-select all files (use with --auto for fully unattended run)")
    parser.add_argument("--uuid", action="append", metavar="UUID", dest="uuids",
                        help="Target specific photo(s) by UUID; may be repeated. Skips size query.")
    args = parser.parse_args()

    if args.uuids:
        photos = query_by_uuids(args.uuids)
        if not photos:
            print("No photos found for the given UUID(s).")
            return
        args.select_all = True  # auto-select when targeting by UUID
    else:
        photos = query_large_files(args.min, args.top)

    if not photos:
        print(f"No files >= {args.min} MB found.")
        return

    videos = sum(1 for p in photos if p["ismovie"])
    in_cloud = sum(1 for p in photos if not p["path"])
    total_size = sum(p["original_filesize"] for p in photos)

    print(f"\nFound {len(photos)} files  ({videos} videos, {len(photos)-videos} photos)")
    print(f"Total size: {fmt(total_size)}  |  In iCloud (not local): {in_cloud}")

    show_table(photos)

    # Select which to compress
    if args.select_all:
        selected = list(range(len(photos)))
    else:
        print("Enter numbers to compress (e.g. 1,3,5-10), 'all', or 'q' to quit:")
        selection = input("> ").strip().lower()
        if selection == 'q':
            return
        if selection == 'all':
            selected = list(range(len(photos)))
        else:
            selected = []
            for part in selection.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-")
                    selected.extend(range(int(a) - 1, int(b)))
                elif part.isdigit():
                    selected.append(int(part) - 1)
            selected = [i for i in selected if 0 <= i < len(photos)]

    if not selected:
        print("Nothing selected.")
        return

    COMPRESSED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nCompressing {len(selected)} file(s) → {COMPRESSED_DIR}\n")

    results = []

    for idx in selected:
        photo = photos[idx]
        name = photo["original_filename"]
        uuid = photo["uuid"]
        orig_size = photo["original_filesize"]
        is_video = photo["ismovie"]

        print(f"  [{idx+1}] {name}  ({fmt(orig_size)})")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Export / download from iCloud
            if not photo["path"]:
                print("      Downloading from iCloud...", end="", flush=True)
            else:
                print("      Exporting...", end="", flush=True)

            src = export_original(uuid, tmp_path, orig_size)
            if not src:
                results.append((name, orig_size, None, "export failed"))
                continue

            # Quick Look review (unless --auto)
            if not args.auto:
                open_quicklook(src)
                choice = input("      Compress this file? [y/n/q]: ").strip().lower()
                close_quicklook()
                if choice == 'q':
                    break
                if choice != 'y':
                    print("      Skipped.")
                    results.append((name, orig_size, None, "skipped"))
                    continue

            # Compress
            out_stem = Path(name).stem
            out_ext = ".mp4" if is_video else Path(name).suffix
            out_path = COMPRESSED_DIR / f"{out_stem}_compressed{out_ext}"
            counter = 1
            while out_path.exists():
                out_path = COMPRESSED_DIR / f"{out_stem}_compressed_{counter}{out_ext}"
                counter += 1

            print("      Compressing...", end="", flush=True)
            if is_video:
                result_path = compress_video(src, out_path)
            else:
                result_path = compress_photo(src, out_path)

            if not result_path or not result_path.exists():
                print(" FAILED")
                results.append((name, orig_size, None, "compression failed"))
                continue

            new_size = result_path.stat().st_size
            ratio = (1 - new_size / orig_size) * 100
            print(f"      {fmt(orig_size)} → {fmt(new_size)}  (saved {ratio:.0f}%)")
            results.append((name, orig_size, new_size, str(result_path)))

            # Import back to Photos
            do_import = args.auto
            if not args.auto:
                imp = input("      Import compressed version to Photos? [y/n]: ").strip().lower()
                do_import = imp == 'y'
            if do_import:
                print("      Importing...", end="", flush=True)
                if import_to_photos(result_path):
                    print(" done  (delete the original in Photos to reclaim space)")
                else:
                    print(" failed (file is in Compressed_Photos folder)")

    # Summary
    print("\n" + "═" * 55)
    print("SUMMARY")
    print("═" * 55)
    compressed = [(n, o, c, p) for n, o, c, p in results if c is not None]
    skipped = [(n, o, c, p) for n, o, c, p in results if p == "skipped"]
    failed = [(n, o, c, p) for n, o, c, p in results if p in ("export failed", "compression failed")]

    if compressed:
        saved = sum(o - c for n, o, c, p in compressed)
        print(f"Compressed: {len(compressed)} files,  space saved: {fmt(saved)}")
        print(f"Output: {COMPRESSED_DIR}")
    if skipped:
        print(f"Skipped:    {len(skipped)} files")
    if failed:
        print(f"Failed:     {len(failed)} files")

    if compressed and not args.auto:
        print("\nNext steps to reclaim space in Photos:")
        print("  1. In Photos, find and verify the imported compressed versions look good")
        print("  2. Delete the originals from Photos (right-click → Delete)")
        print("  3. Empty Photos trash (Photos menu → Empty Trash)")
        print("  4. iCloud will sync the deletions and free cloud storage")


if __name__ == "__main__":
    main()
