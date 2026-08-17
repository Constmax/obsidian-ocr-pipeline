# Review View (Stage 3, v0.1)

Three-column Obsidian view for inspecting OCR preview files from
Stage 2: Original PDF and generated Markdown file coupled page by page, with
**Accept / Reject** and Undo. The plugin is named `ocr-vorschau` and
is located in `plugin/`.

What this is about: 15% of pages derail (repetition loops or aborts)
and drag accuracy down from 98.2% to 93.3% (measured against `ddf69e9`) — see `README.md`,
"Status". This view is the tool used to locate exactly those pages when
reviewing before anything moves into the wiki.

## The Three Columns

| Column | Content |
|---|---|
| **Previews** | File list with status filter (Open · Accepted · Rejected · All), text filter, refresh. Below each line `14 p. · 9 OCR · 2 Diagram`, colored side marking by status, yellow dot on OCR pages. Three separate empty states: Folder missing (→ Settings), Folder empty (→ copyable pdf2md command), Filter empty. |
| **Original PDF** | Pages of the original PDF, lazy-rendered. Header with filename, `p. n / m`, zoom −/+, "Open in PDF viewer". |
| **Markdown** | The generated `.md`, page by page, with provenance badge (`Textlayer` / `OCR` / `Diagram`) and layout info. Toggle **Rendered \| Source**. Action buttons at top. |

Clicking a list entry opens both panes. Scrolling is linked:
scrolling the PDF causes the Markdown to follow (and vice versa), fractionally instead of
rounding to page starts. Reading progress (`reviewed-up-to`) is recorded and restored upon re-opening.

## Opening

- Ribbon icon (column icon) or command palette: **"Open OCR Review View"**
- File menu on a preview `.md` or on a PDF with a matching stem:
  "Open in OCR Review"
- Second command: **"Jump to next preview entry"** (customizable shortcut)

The view survives `Cmd+R`: the last opened file is restored.

## Keyboard Shortcuts

Applies only when the view has focus:

| Key | Action |
|---|---|
| `j` / `k` | Next / previous list entry |
| `a` | **Accept** (move to `_akzeptiert/`) |
| `x` | **Reject** (move to `_abgelehnt/`) |
| `t` | Rendered ⇄ Source |
| `Space` | Advance both columns by one page |
| `g` | Go to page |
| `s` | Toggle scroll synchronization |
| `Esc` | Return to list |

## Buttons

- **Accept** (green) / **Reject** (red): Move the file, update
  the manifest, show a **6-second Notice with Undo**, and automatically jump
  to the next matching entry.
- **Open in Obsidian**: Opens the `.md` in the normal editor.
- **⋯**: Note… · Replace old version (only when `re-generated`) · Reset status · Copy path.
- **Assign PDF…**: Appears in error banner if no original was found;
  opens a suggestion list of all vault PDFs. The assignment lands
  in the manifest (`quelle-pdf-manuell`), never in frontmatter — the `.md` is
  generated output.

## Folder & Manifest Model

The state is defined by the three folders (`_ocr-vorschau/`, `_akzeptiert/`,
`_abgelehnt/`); `review-status.json` is a cache with notes and can be
deleted. The rule is: **the filesystem wins, always.** The plugin never moves
a file to match JSON — doing so would silently undo a deliberate manual move.

Six reconciliation rules (triggered on open, settings change, and debounced vault events):

1. **Exact `parent.path` comparison** during listing — no `startsWith`:
   `_akzeptiert` lives *inside* `_ocr-vorschau`; a prefix test would list accepted files as open.
2. **Folder location ≠ Status → folder location wins.** `notiz` and
   `geprüft-bis` are kept; "Status adopted from folder location" is logged once.
3. **File without entry** → Create entry; metadata from metadata cache (frontmatter).
4. **Entry without file** → If the saved path lives elsewhere in vault,
   the entry is set to `uebernommen` (retained in memory, no longer listed);
   otherwise the cache row is dropped. Files are never deleted.
5. **Identical basename in two folders** → Rule 6.
6. **Re-conversion of an already decided file.** `pdf2md.py`
   always writes to `<out>/<stem>.md` and is unaware of subfolders —
   so a re-run creates a second file with the same name. Two signals, each sufficient on its own: the same file exists simultaneously open *and* decided, or the `ocr-datum` of the open version differs from the logged one. Result: Status `neu-erzeugt` (re-generated), old decision moves to `vorher` (previous), line displays badge "Re-generated".
   **"Replace old version"** (⋯ menu) renames the old version to
   `_abgelehnt/<stem>-<old-ocr-date>.md` — nothing is lost; the old version receives its own entry via reconciliation.

File movement runs exclusively via `fileManager.renameFile` (updates links in vault), never via `vault.rename`. Therefore, diagram images (`![[…png]]`, stored shared in `_ocr-vorschau/assets/`) continue working after moving. Target folders are checked via `getFolderByPath` beforehand and created if needed. Writes to manifest are debounced (500 ms) and serialized via a Promise chain; unreadable JSON is renamed to `review-status.json.kaputt` and rebuilt from folder structure.

## How the PDF Pane Works

**`loadPdfJs()` is public, documented Obsidian API** and loads the
pdf.js library bundled with Obsidian itself — including the pre-wired worker (`GlobalWorkerOptions.workerSrc`). The plugin builds no Blob worker and no main-thread fallback; the bundle stays at ~45 kB instead of ~2.5 MB. Only the long-term stable API surface is used: `getDocument`, `numPages`, `getPage`, `getViewport`, `render`, `destroy` — all isolated in `src/pdf-pane.ts`, rendering in a single function so signature changes remain a single-line fix. Obsidian's *Viewer* is not modified. cMaps are set (`/lib/pdfjs/cmaps/`): PDFs with embedded CID/Type0 fonts — which is standard for this material — would render blank otherwise.

Lazy rendering with pre-measured geometry: After `getDocument`, the column fetches **all** viewports at scale 1 (page dictionary only, no rasterization) and assigns each page its aspect ratio as a CSS custom property. Height and width follow via `aspect-ratio` — scrollbars have correct geometry from frame one, preventing layout shifts during lazy loading rather than compensating for them. Rasterization runs via `IntersectionObserver` (rootMargin 200%), max 2 parallel, with pixel scale `min(width/page · devicePixelRatio, pdfZoomMax)` and LRU eviction at 12 canvases (on eviction `canvas.width = height = 0`, otherwise buffer remains allocated). `doc.destroy()` on file switch and view close; `RenderTask.cancel()` before re-renders. **Error degradation:** Banners in PDF header offer "Open in PDF viewer" and "Assign PDF…" — never a dead pane.

### Fallback if `loadPdfJs` is ever removed

Documented reserve: Bundle `pdfjs-dist` and inline the worker as a Blob URL via esbuild text loader. Cost: Bundle grows to ~2.5 MB, CSP adjustments may be needed, and Obsidian's fork differs from npm package. As long as `loadPdfJs` exists, this fallback remains unbuilt.

## Known Limitations (Intentional)

- **`MarkdownRenderer.render` does not resolve internal embeds** — diagram
  images are post-processed after rendering (image embeds via `getFirstLinkpathDest` + `<img>`). Should Obsidian resolve them natively in the future, the post-processing loop is a no-op.
- **Block-by-block rendering instead of a single block:** Required because `%%…%%` is invisible in preview mode (no DOM node at marker); the page container acts as sync anchor. Positive side-effect: Footnote collisions across page boundaries are eliminated.
- **12-canvas cap** (~4.5 MB per A4 canvas): Distant pages are re-rasterized when scrolling back.
- **Zoom is layout zoom** (CSS `zoom`), not re-render: Zoomed-in pages may appear softer. For pixel-exact inspection, use "Open in PDF viewer".
- **minAppVersion 1.8.7** instead of originally planned 1.5.3: `revealLeaf` and current `Notice` layout require newer versions. The original plan specified 1.5.3, but actual API surface requires more — documented transparently.
- Code that is untestable headless (anything touching `window.pdfjsLib`, `MarkdownRenderer`, DOM) is untestable here as well — see smoke test below.

## Settings

Visible: Preview folder, Accepted folder, Rejected folder, status file (all cleaned via `normalizePath()`, with live indicator if folder is missing), and Markdown column default mode. Persisted internally: Column widths (default 20/40/40, adjustable via split drag handles), `pdfZoomMax` (2.0), `syncAktiv`, `mdEagerLimit` (200 pages). Any settings modification triggers reconciliation.

## Testing

`cd plugin` — `npm run check` (tsc), `npm run lint` (eslint with `eslint-plugin-obsidianmd`), `npm test` (33 unit tests for parser and reconciliation, running under `node --test` without Obsidian), `npm run build`.

### Vault Smoke Test

1. `VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh`, enable plugin.
2. Generate preview for a document with **OCR and diagram page**.
3. Ribbon → View opens, file appears under "Open".
4. **Scroll rapidly to page 20** → placeholder, then content, **no layout jumps**. ⇒ verifies pre-measured heights.
5. Scroll both directions, then stop → **no oscillation, no jitter**. ⇒ verifies the three sync guards.
6. Toggle `Rendered`/`Source` → diagram image visible vs raw `![[…]]`. ⇒ verifies embed post-processing.
7. Press `a` → file moves to `_akzeptiert/`, **image continues rendering there**, manifest entry updated, view advances to next file. ⇒ verifies `renameFile`.
8. Close Obsidian, move file back **in Finder**, restart → file listed under "Open" again, manifest auto-corrects, **nothing is moved back**. ⇒ verifies "filesystem wins".
9. Re-run `pdf2md.py` → Badge "Re-generated — previously accepted", "Replace old version" renames old file.
10. Delete `review-status.json`, re-open view → everything lists correctly. ⇒ verifies manifest is cache-only.
11. `Cmd+R` with open view → same file is restored.

