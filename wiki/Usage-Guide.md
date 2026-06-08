# Usage Guide

This page walks through day-to-day use of each screen. If you haven't installed yet,
start with [[Installation]]; for what each tool does conceptually, see [[Features]].

The app opens at **`http://localhost:8421`**. Switch between tools with the tabs, or
jump directly with a URL hash:

- `http://localhost:8421/#files` → Large Files
- `http://localhost:8421/#activity` → Activity
- (default) → Duplicates
- Append `#blur` to **blur thumbnails** for screenshots / screen-sharing.

---

## First run

1. On first launch the app scans your library: it reads the Photos database and the
   local thumbnail derivatives, computes perceptual hashes, and caches them so future
   loads are fast. Re-run with `--rescan` to ignore the cache (see [[Configuration]]).
2. Grant **Photos** and **Automation** permission when prompted — required for reading
   and for moving items to the trash.
3. The Duplicates tab loads first.

> ⚠️ Everything you trash goes to the Photos **Recently Deleted** album — recoverable
> until *you* empty it in the Photos app. Review before emptying.

---

## Duplicates

1. Clusters are shown grouped, each with a **confidence label** (identical →
   near-identical → similar → loose). **Start with "identical"** — these are safest.
2. Within a group the app highlights the suggested **keeper** (best-scored item).
3. Confirm or change which item to keep, then **trash the rest** of that group.
4. Trashed items disappear from the list. They are moved to Photos' Recently Deleted.

**Tips**
- Adjust matching strictness with `--threshold` (max Hamming distance) and `--window`
  (seconds for near-dup grouping). See [[Configuration]].
- If trashed items ever reappear after a refresh, that was a fixed bug — update to the
  latest version (see [[Troubleshooting]]).

---

## Large Files

1. The list shows every item **sorted largest-first**, each with a size bar.
2. For each row choose **Keep**, **Delete**, or **Compress** (videos only).
3. Use **multi-select** to apply a batch action to many rows at once.
4. Watch the **"Will save"** estimate update as you queue actions.
5. Commit the actions. Deletes go to the Photos trash; compresses run in the
   background (watch progress via the compress status).

**About Compress**
- Only makes sense for videos. It re-encodes to **H.265/HEVC** at the **same
  resolution**, copies metadata across with `exiftool`, imports the smaller copy, and
  trashes the original.
- If the re-encoded file isn't actually smaller, the app keeps your original (guarded).
- Requires `ffmpeg` and `exiftool` (installed by `setup.sh`).

---

## Activity

1. See **total space reclaimed**, **photos deleted**, and **videos compressed**.
2. The **heatmap** shows your cleanup activity by day (GitHub-contribution style).
3. Scroll the **audit log** for a record of individual actions.

Nothing here leaves your Mac — it's read from `~/.photo_saver_audit.jsonl`.

---

## Recovering something you trashed

Open the **Photos** app → **Recently Deleted** album → select the item → **Recover**.
Items stay there until you empty that album (or ~30 days pass, per macOS).

---

### See also

- [[Configuration]] — all CLI flags and URL hashes.
- [[Remote Access]] — using the app from your phone.
- [[Troubleshooting]] — when something doesn't behave.
