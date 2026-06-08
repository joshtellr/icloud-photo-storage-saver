# FAQ

Short answers to common questions. See the linked pages for detail.

---

### Is it really free?
Yes. It's open source under **[GPL-3.0](https://github.com/joshtellr/icloud-photo-storage-saver/blob/main/LICENSE)**.
Forks and derivatives must stay open source under the GPL.

### Does it upload my photos anywhere?
No. It's **100% local** — no analytics, no telemetry, and **no external network calls at
runtime**. The only network access is the first-run dependency installer. See the
[Privacy section](https://github.com/joshtellr/icloud-photo-storage-saver#privacy).

### Will it permanently delete my photos?
No. Everything goes to the Photos **trash** (Recently Deleted), recoverable until **you**
empty it. Compression trashes the original only after a smaller copy is imported. See
[[Security]].

### Can it touch shared albums or other people's photos?
No. Every item is classified by source (your library / shared / hidden / unreachable),
and the app **only acts on items in your own library**. The rest are read-only. See
[[Features#source-classification]].

### Does it run on Linux or in Docker?
No — **macOS only**. It reads a real Apple Photos library and uses **PhotoKit** for
classification and deletion, which don't exist off macOS. See
[[Architecture#why-it-cant-run-on-linux--docker]].

### What macOS version do I need?
macOS **11 (Big Sur) or newer**, with the Apple **Photos** app and an active library.

### Why do I have to right-click to open it the first time?
It's **not code-signed/notarized yet**, so Gatekeeper blocks a normal double-click once.
**Right-click → Open → Open** clears it. A signed + notarized build needs an Apple
Developer ID and is a planned future improvement.

### How does it find duplicates?
Perceptual hashing: a **64-bit dHash** per thumbnail, clustered with union-find over
exact matches plus near-matches within a time window. Each cluster gets a confidence
label. See [[Features#1-duplicates]] and [[Architecture]].

### How much smaller will Compress make my videos?
Re-encoding to **H.265/HEVC** at the **same resolution** is typically **50–80% smaller**.
If a re-encode isn't actually smaller, the app keeps your original.

### How do I use it from my phone?
Put it behind your own **VPN, reverse proxy, or SSH tunnel**, and set
`PHOTO_SAVER_TOKEN`. **Never** expose it on the raw public internet. See
[[Remote Access]].

### What's the access token for?
A thin shared-secret gate for when you expose the port beyond loopback — defense in
depth **on top of**, not instead of, your VPN/TLS. See [[Security]].

### Where does it store its data?
Plain files in your home folder: `~/.photos_dedup_*` (hash cache + state) and
`~/.photo_saver_audit.jsonl` (audit log). See [[Architecture#persistence-files]].

### How do I change duplicate sensitivity?
Use `--threshold` (stricter/looser matching) and `--window` (time grouping). See
[[Configuration]].

### How do I report a bug or a security issue?
Bugs/ideas: open an
[issue](https://github.com/joshtellr/icloud-photo-storage-saver/issues). Security:
open a **private advisory** (Security → Report a vulnerability), not a public issue. See
[[Security]] and [[Contributing and Development]].

---

### See also

- [[Home]] · [[Installation]] · [[Usage Guide]] · [[Troubleshooting]]
