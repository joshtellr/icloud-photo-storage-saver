# Features

The app has three tools, reachable as tabs in the web UI. All three operate only on
photos and videos in **your own** Photos library — shared, hidden, and unreachable
items are read-only (see [Source classification](#source-classification)).

---

## 1. Duplicates

Finds near-identical photos and videos by **perceptual hashing** and groups them into
clusters so you can keep the best one and trash the rest.

- Each thumbnail is reduced to a **64-bit dHash** (difference hash).
- Items are clustered with **union-find** over exact hash matches plus near-matches
  within a configurable time window (default 60s; see [[Configuration]]).
- Every cluster gets a **confidence label** from its internal "tightness" score:

  | Label | Meaning |
  |-------|---------|
  | **identical** | Hashes match exactly / extremely tight cluster |
  | **near-identical** | Very small perceptual distance |
  | **similar** | Visibly related but looser |
  | **loose** | Weakest grouping — review carefully |

- The app suggests **keeping the best** of each group (ranked by a scoring heuristic),
  and lets you trash the others. Tackle **identical** groups first — they're the safest.

See [[Usage Guide#duplicates]] for the workflow.

---

## 2. Large Files

Every item in your library, **sorted by size**, with a visual size bar so the biggest
space hogs jump out. Per row you can:

- **Keep** — leave it alone.
- **Delete** — move it to the Photos trash.
- **Compress** — re-encode a video to **H.265/HEVC** at the **same resolution**,
  typically **50–80% smaller**. The original is trashed after a smaller copy is
  imported.

Extras:

- **Multi-select** and **batch actions** across many rows at once.
- A live **"Will save"** estimate that totals the space your pending actions will
  reclaim before you commit.

See [[Usage Guide#large-files]] and [[Architecture#compression]] for how Compress works.

---

## 3. Activity

A persistent **audit log** and dashboard so you can see your progress over time:

- **Total space reclaimed**, **photos deleted**, and **videos compressed**.
- A **GitHub-style contribution heatmap** of what you cleaned up, by day.
- An **audit log** of individual actions.

The log lives at `~/.photo_saver_audit.jsonl` (one JSON object per line) and is never
uploaded anywhere. See [[Architecture#persistence-files]].

---

## Source classification

Before acting, every photo is classified by **source**:

- **Your library** — owned by you; eligible for delete/compress.
- **Shared albums** — read-only.
- **Hidden album** — read-only.
- **Unreachable** — e.g. not downloaded / unavailable; read-only.

This guarantees the app **only ever acts on photos you actually own and can delete**.
This boundary is intentional and contributors are asked not to loosen it (see
[[Contributing and Development]]).

---

### See also

- [[Usage Guide]] — step-by-step for each tool.
- [[Configuration]] — thresholds, time window, URL options like `#blur`.
- [[Security]] — what destructive actions can and can't do.
