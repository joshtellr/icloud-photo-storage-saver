# Wiki source

These markdown files are the source for the project's **GitHub Wiki**. They use GitHub
wiki conventions:

- `Home.md` is the landing page.
- `_Sidebar.md` and `_Footer.md` render on every page.
- Page filenames are hyphenated; links use `[[Page Name]]` (spaces map to hyphens),
  e.g. `[[Usage Guide]]` → `Usage-Guide.md`.

## Pages

| File | Page |
|------|------|
| `Home.md` | Home / landing |
| `Installation.md` | Installation |
| `Features.md` | Features |
| `Usage-Guide.md` | Usage Guide |
| `Remote-Access.md` | Remote Access |
| `Configuration.md` | Configuration |
| `Security.md` | Security |
| `Architecture.md` | Architecture |
| `Contributing-and-Development.md` | Contributing and Development |
| `Troubleshooting.md` | Troubleshooting |
| `FAQ.md` | FAQ |
| `_Sidebar.md`, `_Footer.md` | Nav chrome (every page) |

## Publishing to the GitHub Wiki

The GitHub Wiki is a **separate git repository** (`<repo>.wiki.git`). To publish:

1. On GitHub, open the repo's **Wiki** tab and click **Create the first page** (this
   initializes the wiki repo). Save any placeholder.
2. Clone the wiki repo and copy these files in:

   ```bash
   git clone https://github.com/joshtellr/icloud-photo-storage-saver.wiki.git
   cp wiki/*.md icloud-photo-storage-saver.wiki/
   cd icloud-photo-storage-saver.wiki
   git add .
   git commit -m "Populate wiki"
   git push
   ```

3. Refresh the Wiki tab — the pages and sidebar will be live.

Keeping the source here (under version control alongside the code) means wiki changes
can be reviewed in PRs before being mirrored to the live wiki.
