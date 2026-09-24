# Review View (Stage 3, v0.1)

Three-column Obsidian view for inspecting OCR preview files from
Stage 2: Original PDF and generated Markdown file coupled page by page, with
**Accept / Reject**, notes, editing, and Undo. The plugin is named `ocr-vorschau` and
is located in `plugin/`.

What this is about: 15% of pages derail (repetition loops or aborts)
and drag accuracy down from 98.2% to 93.3% (measured against `ddf69e9`) — see `README.md`,
"Status". This view is the tool used to locate exactly those pages when
reviewing before anything moves into the wiki.

## The Three Columns

| Column | Content |
|---|---|
| **Previews** | File list with status filter (Open · Accepted · Rejected · All), text filter, refresh, and progress for lists of at least five entries. Mixed-status lists are grouped in the same order used by `j`/`k`. Below each line: `14 p. · 9 OCR · 2 Diagram`, colored side marking by status, yellow dot on OCR pages. Three separate empty states: Folder missing (→ Settings), Folder empty (→ copyable pdf2md command), Filter empty. |
| **Original PDF** | Pages of the original PDF, lazy-rendered. Header with filename, `p. n / m`, zoom −/+, "Open in PDF viewer". |
| **Markdown** | The generated `.md`, page by page, with provenance badge (`Text layer` / `OCR` / `Diagram`) and layout info. Toggle **Rendered \| Source**. |

Clicking a list entry opens both panes. Scrolling is linked:
scrolling the PDF causes the Markdown to follow (and vice versa), fractionally instead of
rounding to page starts. Reading progress (`checked-until`) is recorded and restored upon re-opening.

The decision bar spans the bottom of the view and shows the next file and the
number of open entries. Two operating modes share the same controls:

- **Review flow** (default) keeps the three column headers and uses a larger
  decision bar for rapid accept/reject passes.
- **Workbench** moves the PDF and Markdown controls into one toolbar, adds a
  compact status bar, and exposes editing. Changes are saved automatically;
  the manifest records that the current generated revision was edited and the
  flag resets when a new conversion is detected.

## Opening

- Ribbon icon (column icon) or command palette: **"Open OCR Review View"**
- File menu on a preview `.md` or on a PDF with a matching stem:
  "Open in OCR Review"
- File menu on any PDF: **"OCR → Markdown"** opens the page-selection dialog
  for that file and starts conversion. While another conversion is running, the
  item remains visible but shows a notice instead of starting another one.
- Second command: **"Jump to next preview entry"** (customizable shortcut)

The view survives `Cmd+R`: the last opened file is restored.

## Keyboard Shortcuts

Applies only when the view has focus:

| Key | Action |
|---|---|
| `j` / `k` | Next / previous list entry |
| `a` | **Accept** (move to `_ocr-preview/_accepted/`) |
| `x` | **Reject** (move to `_ocr-preview/_rejected/`) |
| `n` | Open the note dialog |
| `t` | Rendered ⇄ Source |
| `e` | Toggle editing in Workbench mode |
| `Space` | Advance both columns by one page |
| `g` | Go to page |
| `s` | Toggle scroll synchronization |
| `Esc` | Return to list |

## Buttons

- The bottom bar contains **Accept** (green), **Reject** (red), **Note**, and
  **Open in Obsidian**, with their keyboard shortcuts shown on the buttons.
- **Accept** / **Reject** move the file, update
  the manifest, show a **6-second Notice with Undo**, and automatically jump
  to the next matching entry.
- **⋯**: Note… · Replace old version (only when `re-generated`) · Reset status · Copy path.
- **Assign PDF…**: Appears in error banner if no original was found;
  opens a suggestion list of all vault PDFs and displayable images. The assignment lands
  in the manifest (`manual-source-pdf`), never in frontmatter — the `.md` is
  generated output.

## Folder & Manifest Model

The state is defined by the three folders (`_ocr-preview/`, `_accepted/`,
`_rejected/`); `review-status.json` is a cache with notes and can be
deleted. The rule is: **the filesystem wins, always.** The plugin never moves
a file to match JSON — doing so would silently undo a deliberate manual move.

Six reconciliation rules (triggered on open, settings change, and debounced vault events):

1. **Exact `parent.path` comparison** during listing — no `startsWith`:
   `_akzeptiert` lives *inside* `_ocr-vorschau`; a prefix test would list accepted files as open.
2. **Folder location ≠ Status → folder location wins.** `note`,
   `checked-until`, and `manually-edited` are kept; "Status adopted from folder location" is logged once.
3. **File without entry** → Create entry; metadata from metadata cache (frontmatter).
4. **Entry without file** → If the saved path lives elsewhere in vault,
   the entry is set to `adopted` (retained in memory, no longer listed);
   otherwise the cache row is dropped. Files are never deleted.
5. **Identical basename in two folders** → Rule 6.
6. **Re-conversion of an already decided file.** `pdf2md.py`
   always writes to `<out>/<stem>.md` and is unaware of subfolders —
   so a re-run creates a second file with the same name. Two signals, each sufficient on its own: the same file exists simultaneously open *and* decided, or the `ocr-date` of the open version differs from the logged one. Result: status `re-created`, old decision moves to `previous`, and the line displays a "Re-created" badge.
   **"Replace old version"** (⋯ menu) renames the old version to
   `_rejected/<stem>-<old-ocr-date>.md` — nothing is lost; the old version receives its own entry via reconciliation.

File movement runs exclusively via `fileManager.renameFile` (updates links in vault), never via `vault.rename`. Therefore, diagram images (`![[…png]]`, stored shared in `_ocr-preview/assets/`) continue working after moving. Target folders are checked via `getFolderByPath` beforehand and created if needed. Writes to manifest are debounced (500 ms) and serialized via a Promise chain; unreadable JSON is renamed to `review-status.json.corrupted` and rebuilt from folder structure.

## How the PDF Pane Works

**`loadPdfJs()` is public, documented Obsidian API** and loads the
pdf.js library bundled with Obsidian itself — including the pre-wired worker (`GlobalWorkerOptions.workerSrc`). The plugin builds no Blob worker and no main-thread fallback; the bundle stays at ~45 kB instead of ~2.5 MB. Only the long-term stable API surface is used: `getDocument`, `numPages`, `getPage`, `getViewport`, `render`, `destroy` — all isolated in `src/pdf-pane.ts`, rendering in a single function so signature changes remain a single-line fix. Obsidian's *Viewer* is not modified. cMaps are set (`/lib/pdfjs/cmaps/`): PDFs with embedded CID/Type0 fonts — which is standard for this material — would render blank otherwise.

Lazy rendering with pre-measured geometry: After `getDocument`, the column fetches **all** viewports at scale 1 (page dictionary only, no rasterization) and assigns each page its aspect ratio as a CSS custom property. Height and width follow via `aspect-ratio` — scrollbars have correct geometry from frame one, preventing layout shifts during lazy loading rather than compensating for them. Rasterization runs via `IntersectionObserver` (rootMargin 200%), max 2 parallel, with pixel scale `min(width/page · devicePixelRatio, pdfZoomMax)` and LRU eviction at 12 canvases (on eviction `canvas.width = height = 0`, otherwise buffer remains allocated). `doc.destroy()` on file switch and view close; `RenderTask.cancel()` before re-renders. **Error degradation:** Banners in PDF header offer "Open in PDF viewer" and "Assign PDF…" — never a dead pane.

**What the source column accepts (Issue #101):** PDFs, rendered through pdf.js as above, and PNG, JPG/JPEG and BMP images. An image bypasses pdf.js: it becomes a single `<img>` page whose aspect ratio comes from its natural size after the image loads, so zoom and page/scroll coupling treat it as a one-page document. TIFF converts (Stage 2 accepts it) but Chromium cannot decode it, so a TIFF source shows a banner naming the reason instead. When a PDF and an image share the preview's basename, the PDF wins. "Create searchable copy" is not offered for an image source — Stage 1 is PDF-only.

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

Visible: Operating mode, Preview folder, Accepted folder, Rejected folder,
status file (all cleaned via `normalizePath()`, with a live indicator if a
folder is missing), Markdown column default, scroll sync, PDF render factor,
Markdown eager limit, and column widths.

**Searchable copy** (for the Stage-1 action, #66): OCR engine (Automatic,
Apple Vision, Tesseract; default Automatic) and Split two-column pages
(default off). `parseOcrSettings()` in `src/ocr-settings.ts` validates both on
load: data from before these settings and invalid values (such as an engine
this version does not offer) fall back to the defaults field by field.
PaddleOCR is not offered yet. Obsidian on mobile shows only a desktop-only
notice in this section.

The action itself is **Create searchable copy (OCR)**: in the PDF file menu,
as a command that asks for a PDF, and in the view's More menu for the open
preview's original PDF. It writes `<stem>-ocr.pdf` beside the source with
`reprocess-raw --output`, stops if that file already exists, never touches the
source, and opens the new PDF.

## Testing

`cd plugin` — `npm run check` (tsc), `npm run lint` (eslint with
`eslint-plugin-obsidianmd`), `npm test` (plain `node --test`, without
Obsidian), `npm run build`.

### Vault Smoke Test

1. `VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh`, enable plugin.
2. Generate preview for a document with **OCR and diagram page**.
3. Ribbon → View opens, file appears under "Open".
4. **Scroll rapidly to page 20** → placeholder, then content, **no layout jumps**. ⇒ verifies pre-measured heights.
5. Scroll both directions, then stop → **no oscillation, no jitter**. ⇒ verifies the three sync guards.
6. Toggle `Rendered`/`Source` → diagram image visible vs raw `![[…]]`. ⇒ verifies embed post-processing.
7. Press `a` → file moves to `_accepted/`, **image continues rendering there**, manifest entry updated, view advances to next file. ⇒ verifies `renameFile`.
8. Close Obsidian, move file back **in Finder**, restart → file listed under "Open" again, manifest auto-corrects, **nothing is moved back**. ⇒ verifies "filesystem wins".
9. Re-run `pdf2md.py` → Badge "Re-created — previously accepted", "Replace old version" renames old file.
10. Delete `review-status.json`, re-open view → everything lists correctly. ⇒ verifies manifest is cache-only.
11. `Cmd+R` with open view → same file is restored.
12. Select **All** with several statuses → group headings appear and `j`/`k`
    follow their visible order.
13. Switch between Review flow and Workbench while the view is open → controls
    move without duplicate buttons or losing state.
14. In Workbench, press `e`, edit Markdown, then press `e` again → the file is
    saved, the one-time overwrite warning appears only for the current revision,
    and Review flow does not expose editing.
