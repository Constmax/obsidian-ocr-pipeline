# Review View (Stage 3, v0.1)

Three-column Obsidian view for inspecting OCR preview files from
Stage 2: Original PDF and generated Markdown file coupled page by page, with
**Accept / Reject**, notes, editing, and Undo. The plugin id is `ocr-preview` (`ocr-vorschau`
before the English rename) and the code is located in `plugin/`.

What this is about: OCR pages can contain word errors, and a derailed tile
(repetition loop, abort) is repaired only when the retry succeeds — see
`README.md`, "Stand". This view is where those pages are found and decided
before anything moves into the wiki. The code calls it the comparison view
(`OcrComparisonView`).

## The Three Columns

| Column | Content |
|---|---|
| **Previews** | File list with status filter (Open · Accepted · Rejected · All), text filter, a Convert PDF button (same as the command *Convert PDF and open in OCR comparison*), refresh, and progress for lists of at least five entries. Mixed-status lists are grouped in the same order used by `j`/`k`. Below each line: `14 p. · 9 OCR · 2 Diagram`, colored side marking by status, yellow dot on OCR pages. Three separate empty states: Folder missing (→ Settings), Folder empty (→ copyable pdf2md command), Filter empty. |
| **Original PDF** | Pages of the original PDF, lazy-rendered. Header with filename, `p. n / m`, zoom −/+, "Open in PDF viewer". |
| **Markdown** | The generated `.md`, page by page, with provenance badge (`Text layer` / `OCR` / `Diagram`) and layout info, a **Marked** badge on pages that have a page case, and a flag button that marks the page as wrong. Toggle **Rendered \| Source**. |

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
- **Saving** writes only edits the view holds, and only when the file is as
  the view last read or wrote it (`src/save-policy.ts`, #213). Opening a
  preview or switching entries does not rewrite it. When the file changes on
  disk (a re-conversion, another editor, sync), the view reloads it if it has
  no edits; if it has, it shows a notice, writes nothing, and the disk version
  loads the next time the preview opens.

## Opening

- Ribbon icon (column icon) or command palette: **"Open OCR comparison"**
- File menu on a preview `.md` or on a PDF with a matching stem:
  "Open in OCR comparison"
- File menu on any PDF: **"OCR → Markdown"** opens the page-selection dialog
  for that file and starts conversion. While another conversion is running, the
  item remains visible but shows a notice instead of starting another one.
  A persistent notice with **Hide** and **Cancel** follows the run, and the
  status bar (bottom right) shows the same progress, page count first, until
  the run ends.
  **Hide** (or a click on the notice) only removes the notice; a click on the
  status-bar item brings it back, with **Cancel** unless the run is already
  being cancelled. Once the analysis is done the notice names the pages of
  the run (`m pages`); after each page it reads
  `page n of m · k derailed · about x min left`, where `n` counts the pages
  this run finished (with a page selection, `m` is the number of selected
  pages). The derailed count appears once a page derailed. The time left is
  the mean time of the pages so far times the pages still to come. Pages that
  report 0 s do not count: pages reused from the cache, and text-layer pages
  under 0.05 s (`sekunden` is rounded to 0.1 s). The estimate is rough: too
  low when model pages follow text-layer pages, and too high after the first
  model page, whose time includes loading the model.
- Command **"Convert PDF and open in OCR comparison"**, or the file-plus button
  in the Previews header: a search over all PDFs and images in the vault, then
  the same page selection and run as **"OCR → Markdown"**
- Second command: **"Jump to next preview entry"** (customizable shortcut)
- **"Mark page as wrong"** (command palette, while the view has focus): marks
  the page the view shows, see "Marking a Page as Wrong"

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
  to the next matching entry. Each notice's Undo reverts its own decision,
  also after later ones, as long as nothing moved or decided that entry since;
  otherwise it says so and changes nothing.
- **⋯**: Note… · Replace old version (only when `re-generated`) · Reset status · Copy path.
- **Assign PDF…**: Appears in error banner if no original was found;
  opens a suggestion list of all vault PDFs and displayable images. The assignment lands
  in the manifest (`manual-source-pdf`), never in frontmatter — the `.md` is
  generated output.

## Marking a Page as Wrong

A page whose block Stage 2 assembled wrongly becomes a **page case**: the
recognized lines of the page, the block Stage 2 produced from them, and the
block the user expects. `make check-cases` replays it without the model, so an
assembly fix is checked against the pages it was made for (terms:
`CONTEXT.md`; format and replay: `docs/scripts-detail.md`, "Page Cases"). The
plugin only spawns `pdf2md case stash | add | list` (`docs/cli-contract.md`,
"Page Cases").

1. **Correct the page** in Workbench mode (`e`). The first edit of a page
   runs `pdf2md case stash` in the background, before that edit is saved, once
   per page each time the preview is opened. It keeps the produced block, so
   a rerun of the source cannot lose it. A failed stash is logged to the
   console and the edit is saved anyway.
2. **Mark it:** the flag button in the page's header, or the command **"Mark
   page as wrong"** for the page the view shows. A dialog takes an optional
   one-line note. Pending edits are saved first, then `pdf2md case add` makes
   the page's current block the expected one. A notice reports the case's
   status and fault stage, and says so when the page was marked without a
   correction the comparison sees.
3. The page shows a **Marked** badge; its tooltip names status and fault
   stage. The badges come from `pdf2md case list` when the preview is opened.
   Marking again updates the expected block (and the note, when one is given).

Marking is offered for previews under review, in the preview folder: the page
cache and the cases (`<preview folder>/.cases/`) stay there, so a preview
under `_accepted/` or `_rejected/` shows neither the button nor badges. Mark
the pages before accepting or rejecting. There is no count in the sidebar and
no overview of cases; `make check-cases` lists them.

## Folder & Manifest Model

The state is defined by the three folders (`_ocr-preview/`, `_accepted/`,
`_rejected/`); `review-status.json` is a cache with notes and can be
deleted. The rule is: **the filesystem wins, always.** The plugin never moves
a file to match JSON — doing so would silently undo a deliberate manual move.

Six reconciliation rules (triggered on open, settings change, and debounced vault events).
At startup the plugin only reads the manifest; the first reconcile runs in
`onLayoutReady`, once the vault index is complete, and no reconcile runs
before it. A view restored with the workspace waits for that first reconcile
before it opens its entry.

1. **Exact `parent.path` comparison** during listing — no `startsWith`:
   `_accepted` lives *inside* `_ocr-preview`; a prefix test would list accepted files as open.
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

**Converting an edited or decided preview again** (any route: file menu,
command, Convert PDF button) first asks in a modal. pdf2md overwrites the
preview folder's file, and reconciliation resets `note`, `checked-until` and
`manually-edited`. When that file is manually edited, confirming first copies
it to `_rejected/<stem>-edited-<ocr-date>.md` (a copy, not a move: a
`--pages` run merges into the existing file); a failed copy stops the
conversion. A decided preview's file stays where it is, so no copy is made.
Cancel, Esc or closing the modal converts nothing. `manually-edited` is set on
the first keystroke in the view, even if a conflict later refuses that write:
a false positive costs one question, a false negative loses edits.

File movement runs exclusively via `fileManager.renameFile` (updates links in vault), never via `vault.rename`. Therefore, diagram images (`![[…png]]`, stored shared in `_ocr-preview/assets/`) continue working after moving. Target folders are checked via `getFolderByPath` beforehand and created if needed. Writes to manifest are debounced (500 ms) and serialized via a Promise chain; unreadable JSON is renamed to `review-status.json.corrupted` and rebuilt from folder structure.

## How the PDF Pane Works

**`loadPdfJs()` is public, documented Obsidian API** and loads the
pdf.js library bundled with Obsidian itself — including the pre-wired worker (`GlobalWorkerOptions.workerSrc`). The plugin builds no Blob worker and no main-thread fallback; the bundle stays at ~45 kB instead of ~2.5 MB. Only the long-term stable API surface is used: `getDocument`, `numPages`, `getPage`, `getViewport`, `render`, `destroy` — all isolated in `src/pdf-pane.ts`, rendering in a single function so signature changes remain a single-line fix. Obsidian's *Viewer* is not modified. cMaps are set (`/lib/pdfjs/cmaps/`): PDFs with embedded CID/Type0 fonts — which is standard for this material — would render blank otherwise.

Lazy rendering with pre-measured geometry: After `getDocument`, the column fetches **all** viewports at scale 1 (page dictionary only, no rasterization) and assigns each page its aspect ratio as a CSS custom property. Height and width follow via `aspect-ratio` — scrollbars have correct geometry from frame one, preventing layout shifts during lazy loading rather than compensating for them. Rasterization runs via `IntersectionObserver` (rootMargin 200%), max 2 parallel, with pixel scale `min(width/page · devicePixelRatio, 2)` (`RENDER_SCALE_MAX` in `src/pdf-pane.ts`, a memory limit, not a setting) and LRU eviction at 12 canvases (on eviction `canvas.width = height = 0`, otherwise buffer remains allocated). `doc.destroy()` on file switch and view close; `RenderTask.cancel()` before re-renders. **Error degradation:** Banners in PDF header offer "Open in PDF viewer" and "Assign PDF…" — never a dead pane.

**What the source column accepts (Issue #101):** PDFs, rendered through pdf.js as above, and PNG, JPG/JPEG and BMP images. An image bypasses pdf.js: it becomes a single `<img>` page whose aspect ratio comes from its natural size after the image loads, so zoom and page/scroll coupling treat it as a one-page document. TIFF converts (Stage 2 accepts it) but Chromium cannot decode it, so a TIFF source shows a banner naming the reason instead. When a PDF and an image share the preview's basename, the PDF wins.

### Fallback if `loadPdfJs` is ever removed

Documented reserve: Bundle `pdfjs-dist` and inline the worker as a Blob URL via esbuild text loader. Cost: Bundle grows to ~2.5 MB, CSP adjustments may be needed, and Obsidian's fork differs from npm package. As long as `loadPdfJs` exists, this fallback remains unbuilt.

## Known Limitations (Intentional)

- **`MarkdownRenderer.render` does not resolve internal embeds** — diagram
  images are post-processed after rendering (image embeds via `getFirstLinkpathDest` + `<img>`). Should Obsidian resolve them natively in the future, the post-processing loop is a no-op.
- **Block-by-block rendering instead of a single block:** Required because `%%…%%` is invisible in preview mode (no DOM node at marker); the page container acts as sync anchor. Positive side-effect: Footnote collisions across page boundaries are eliminated.
- **12-canvas cap** (~4.5 MB per A4 canvas): Distant pages are re-rasterized when scrolling back.
- **Zoom scales the page width** (the stack is `zoom` × the column width; not CSS `zoom`, which a `width: 100%` page cancels out). Above 100 % the column scrolls horizontally. Visible pages re-render at the new width, but the pixel scale stays capped at 2, so strongly zoomed pages may appear softer. For pixel-exact inspection, use "Open in PDF viewer".
- **minAppVersion 1.11.0**: the settings tab builds its groups with `SettingGroup` (Obsidian 1.11). Earlier steps were 1.5.3 (original plan) and 1.8.7 (`revealLeaf`, current `Notice` layout).
- Code that is untestable headless (anything touching `window.pdfjsLib`, `MarkdownRenderer`, DOM) is untestable here as well — see smoke test below.

## Settings

The settings tab has four tabs, each built from Obsidian's `SettingGroup`:

- **General:** Operating mode, Markdown column default, scroll sync. Column
  widths have no field: they are set by dragging the column borders in the view
  and saved in `columnWidths`.
- **Folders:** Preview folder, Accepted folder, Rejected folder, status file
  (all cleaned via `normalizePath()`, with a live indicator if a folder is
  missing).
- **PDF → Markdown:** placeholder; pdf2md options join here (#28).
- **Searchable copy:** see below.

Two former settings are fixed values now: the PDF render scale cap (2,
`RENDER_SCALE_MAX` in `src/pdf-pane.ts`) and the Markdown eager limit (200
pages, `EAGER_LIMIT` in `src/md-pane.ts`). Saved values from older versions are
ignored and drop out on the next save.

**OCR text layer** (for the Stage-1 action, #66): OCR engine (Automatic,
Apple Vision, Tesseract, Apple Vision + RapidOCR (Paddle fast), which reads with
Apple Vision and re-reads citation lines with RapidOCR; default Automatic,
which uses Paddle fast when it is ready, then Apple Vision, then Tesseract, and
skips Paddle while Split two-column pages is on, #198),
Split two-column pages (default off), and Maximum scan resolution (#201, default
300, 0 = off), passed as `--dpi`: scans above it are downscaled to it before OCR,
scans at or below it keep their resolution (JPEG scans pass through unchanged,
other images are re-encoded, see `docs/scripts-detail.md`). The field saves when
it loses focus; an invalid value is discarded and the saved one shown again.
`parseOcrSettings()` in `src/ocr-settings.ts`
validates all three on load: data from before these settings and invalid values
(such as an engine this version does not know) fall back to the defaults field
by field. Obsidian on mobile shows only a desktop-only notice in this section.

PaddleOCR (#73) runs as `--engine paddle --paddle-mode fast` and appears in the
list only after `reprocess-raw --check-engine` passes on this machine (the
settings tab runs it each time it opens); otherwise the setting names the
reason. A stored PaddleOCR is checked again before every run: when it is not
usable (another Mac, a removed venv), the run uses Automatic and a notice says
why. The benchmark in #71 retained it (`bench/ERGEBNIS.md`, Nachtrag 26).
PaddleOCR always reads whole pages: with it, the Split two-column pages toggle
has no effect (#153).

The action itself is **Add OCR text layer**: in the PDF file menu and as a
command that asks for a PDF (command id `create-searchable-copy`, kept so
hotkeys survive). The comparison view offers no entry for it (#96): pure OCR
never opens or requires the view. It adds the text layer to the PDF itself
with `reprocess-raw --in-place` (#180), so the path and every link stay the
same. The CLI replaces the file only after all checks passed, in one rename;
failure, cancellation or a PDF changed during the run leave it unchanged and
write nothing else. Success shows a notice.
Its progress notice has no page count, but it can be hidden and brought back
from the status bar like the conversion's.

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
15. Edit a page, click its flag button, enter a note, **Mark** → a notice
    names status and fault stage, the page shows **Marked**, and
    `_ocr-preview/.cases/<stem>/pNNN.json` holds `produced` ≠ `expected`.
    Reopen the preview → the badge is still there. ⇒ verifies stash, add, list.
16. **OCR → Markdown** on a multi-page scan → after the first model page the
    notice reads `page n of m · about x min left`, and the status bar shows
    the same. **Hide** → the run continues and the status bar keeps counting;
    click the status-bar item → the notice is back with **Cancel**. At the end
    the status-bar item disappears. With a page selection (e.g. 5–7) the
    notice counts `page 1 of 3`.
