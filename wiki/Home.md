<p align="center">
  <img src="https://raw.githubusercontent.com/joshtellr/icloud-photo-storage-saver/main/docs/app-icon.png" alt="iCloud Photo Storage Saver icon" width="128">
</p>

# iCloud Photo Storage Saver Wiki

A free, **local** macOS app to reclaim storage in your **Apple Photos / iCloud**
library — find duplicates, browse and shrink your biggest videos, and track what
you've saved over time.

It runs a small web app on your Mac (`http://localhost:8421`) that you use from any
browser on your Mac — or your **phone**, if you put it behind your own VPN or reverse
proxy. **Everything runs locally; no photo or metadata ever leaves your machine.**

> **macOS only.** It reads your Apple Photos library through PhotoKit, so it can't be
> Dockerized or run on Linux — it's a native macOS app you self-host on your own Mac.

---

## Quick links

| I want to… | Go to |
|------------|-------|
| Install the app | [[Installation]] |
| Understand the three tools | [[Features]] |
| Learn how to use each screen | [[Usage Guide]] |
| Reach it from my phone | [[Remote Access]] |
| Understand the safety / threat model | [[Security]] |
| See how it works under the hood | [[Architecture]] |
| Tweak flags, env vars, URL options | [[Configuration]] |
| Fix a problem | [[Troubleshooting]] |
| Get a quick answer | [[FAQ]] |
| Hack on it / run the tests | [[Contributing and Development]] |

---

## What it does at a glance

- **Duplicates** — perceptual-hash clustering of near-identical photos/videos, with a
  confidence label (identical / near-identical / similar / loose) so you tackle the
  safe ones first. Keep the best of each group, trash the rest.
- **Large Files** — every item sorted by size with a visual size bar. Keep, **Delete**,
  or **Compress** (re-encode videos to H.265/HEVC — typically 50–80% smaller, same
  resolution). Multi-select, batch actions, and a live "Will save" estimate.
- **Activity** — a persistent audit log and dashboard: total space reclaimed, photos
  deleted, videos compressed, and a GitHub-style contribution heatmap of what you
  cleaned up by day.

Photos are classified by source (your library / shared albums / hidden album /
unreachable) so the app **only ever acts on photos you actually own and can delete**.

---

## Safety in one paragraph

This app moves photos to your Photos **trash** and re-encodes videos. It's careful —
originals go to the **Recently Deleted** album, not permanently erased, and it only
touches items in your own library — but you are deleting things. Review before you
empty Photos' trash. See [[Security]] for the full model.

---

## Privacy in one paragraph

100% local. No analytics, no telemetry, and **no external network calls at runtime** —
only the dependency installer (first run) touches the network. The audit log, hash
cache, and your keep/trash choices are plain files in your home folder
(`~/.photos_dedup_*`, `~/.photo_saver_audit.jsonl`).

---

*This wiki mirrors the project's README, `SECURITY.md`, `CONTRIBUTING.md`, and the
`photo_saver.py` source. If something here drifts from the code, the code wins —
please open an issue or PR.*
