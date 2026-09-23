# Scripts — Complete Flag Reference

## Common Flags

All three scripts share these flags:

| Flag | Default | Meaning |
|---|---|---|
| `--engine auto\|apple\|tesseract` | `auto` | OCR engine selection |
| `--dpi N` | `300` | Pre-OCR downscaling (0 = disabled) |
| `--jobs N` | by RAM (1–4) | Parallel OCR workers |
| `--split-columns` | off | Automatically detect two-column pages, split, then re-merge back into original page layout |
| `--split-columns-all` | off | Same as `--split-columns`, but without detection — splits every page |
| `--keep-split` | off | Suppress re-merge (output remains split into half-pages) |
| `--no-quality-gate` | off | Disable automated quality check + auto-retry |

These flags are parsed and validated in one place (`parse_common_option` in
`pdf-lib.sh`), so all three scripts apply the same rules: a missing or invalid
value (`--jobs 0`, `--dpi abc`) and any unknown option stop the script with a
usage error before any processing. A value you pass explicitly is kept as
given, even when it equals the default: `--jobs 2` is never replaced by RAM
detection, and `--dpi`/`--jobs` win over the `--fast` presets.

### Engine Selection

- `auto`: Uses Apple Vision if `ocrmypdf-appleocr` is installed, otherwise Tesseract
- `apple`: Forces Apple Vision (fails with error if plugin is missing)
- `tesseract`: Forces Tesseract (automatically applies `--tesseract-pagesegmode 1` for column detection and `--clean` when `unpaper` is available)

### DPI Tuning

- `300`: Sweet spot for OCR — Tesseract's optimal resolution, required for fine legal print in Hemmer scripts (Default)
- `200`: Reduced memory usage, slight quality loss on fine print (--fast Default)
- `150`: Absolute minimum — fallback for OOM crashes with `--jobs 1`
- `0`: Downscaling completely disabled

Downscaling defaults to **Bicubic** resampling (`/Bicubic`) instead of Ghostscript's default `/Subsample` to preserve text edge sharpness.

### Jobs Tuning

- `auto` (without `--jobs`): Automatically determined via `detect_safe_jobs()`: ≤ 8 GB RAM → 1 job, ≤ 16 GB → 2 jobs, > 16 GB → 4 jobs. Overridable with `--jobs N`.
- `2`: Safe on Apple Silicon with ≥ 16 GB RAM
- `1`: Single-threaded, enforced on 8 GB Macs (M1 Air, etc.)
- `4-8`: High-end machines with abundant RAM only

## pdf-auto

```bash
pdf-auto <folder> [--output-dir <dir>] [--engine ...] [--dpi N] [--jobs N] \
                  [--cleanup] [--fast] \
                  [--split-columns] [--split-columns-all] [--keep-split] \
                  [--no-quality-gate]
```

### Specific Flags

- `--output-dir <dir>`: Custom output path (Default: `<folder>/_processed/`)
- `--cleanup`: Move originals to `<folder>/_archive/` after success (empties input folder, improving repeatability)
- `--fast`: Presets for large batch jobs (an explicit `--dpi`/`--jobs` wins):
  - `--dpi 200` (instead of 300)
  - `--jobs 1` (more stable)
- `--split-columns`: Per-page detection of two-column layouts, splits pages, and re-merges back into original page layout post-OCR — structural solution against column interleaving in Hemmer/Kaiser materials, including mixed documents (see "Column Splitting" below)
- `--split-columns-all`: Forces splitting on every page (bypasses auto-detection) — fallback when layout detection fails
- `--keep-split`: Suppresses re-merge; output remains in (twice as many) half-pages
- `--no-quality-gate`: Skips post-OCR quality verification and automatic engine retry

### Quality Gate

After every OCR run, the pipeline automatically validates:
1. **Characters/page** ≥ 200 (catches total failures)
2. **Garbage score** < 0.40 (catches column mixing, §→88 corruption, unexpected mid-word capitals)
3. **iso ratio** < 0.40 (special check: >40% 1-2 character words = guaranteed column mixing)

Threshold 0.40 instead of 0.30: Tolerates unavoidable OCR artifacts in older Hemmer scans (e.g., "eaglen" for "hemmer") while reliably catching structural failures.

On failure: Auto-retry with column split (when using Tesseract and `pikepdf` is available, including re-merge to original format), followed by retry with alternative engine (apple ↔ tesseract).

### Multi-Part File Detection

Regex: `^(.+)[[:space:]]+[Tt]eil[[:space:]]+([0-9]+)\.[Pp][Dd][Ff]$`

- ✅ `Verwaltungsrecht AT Skript Teil 1.pdf`
- ✅ `Strafrecht BT TEIL 12.pdf`
- ❌ `Verwaltungsrecht-Teil-1.pdf` (no spaces)
- ❌ `Part 1 ....pdf` (English)

Parts are merged sorted alphanumerically and numerically.

### Output Naming

- `Foo Teil 1.pdf` + `Foo Teil 2.pdf` → `Foo.pdf` (part suffix stripped)
- `Urteil BGH 2024.pdf` (no part pattern) → `Urteil BGH 2024.pdf`

## pdf-workflow

```bash
pdf-workflow <folder> <output-name> [--engine ...] [--dpi N] [--jobs N] \
             [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### Supported Inputs

- Images: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif` (case-insensitive)
- PDFs: existing PDFs are appended

### Sorting

Natural Sort (`sort -V`):
- `(1).jpeg`, `(2).jpeg`, ..., `(10).jpeg` → correctly sorted
- `seite_01.jpg`, `seite_02.jpg` → correctly sorted
- `img1.jpg`, `img10.jpg`, `img2.jpg` → sorted as 1, 2, 10 via `-V`

### Output Name Handling

- `.pdf` extension is automatically stripped if provided
- Empty output name → error
- Collision with input file: Output is excluded from PDF list (prevents infinite loop during re-runs)

## pdf-combine

```bash
pdf-combine <folder> <output-name> [--force-ocr] [--engine ...] [--dpi N] [--jobs N] \
            [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### Specific Flags

- `--force-ocr`: Perform OCR even on pages with existing text layer (Default: `--skip-text`)
  - Useful for replacing low-quality existing OCR
  - Discards legacy text layer and generates fresh text layer

### Sorting

Alphanumeric with Natural Sort. Use numerical prefixes for explicit ordering: `01_`, `02_`, ...

## reprocess-raw

```bash
reprocess-raw <raw-pdf-file> [--output FILE] [pdf-combine-options] [--min-chars N] [--allow-pages LIST]
```

Wrapper around `pdf-combine` for the scenario "re-process an existing `raw/` file with the updated pipeline" (e.g. after bug fixes). Workflow:

1. Copies source file to a private temporary directory (`$TMPDIR`, outside the vault) and executes `pdf-combine` with passed options.
2. **Check 1 — Page Count:** Output page count must match original exactly. Mismatch → original remains unchanged, result saved as `<name>_FAILED_pagecount.pdf` alongside source.
3. **Check 2 — B5 Gate (`column_tools.py verify-pages`):** Every page must contain ≥ `--min-chars` characters (Default 50, via `pdftotext -raw`). A document-wide character average (as checked by standard quality gate) can mask a single textless page inside an otherwise healthy large document — which corrupted fourteen `raw/` files on 2026-07-06 (see `BUGREPORT-2026-07-06-split-merge.md`). Mismatch → original remains unchanged, result saved as `<name>_FAILED_pages.pdf`, affected pages reported individually.
4. Only if both checks pass: Original source file is overwritten.

`--allow-pages "1,5-7"` exempts known cover or diagram pages lacking body text from Check 2. Without `pikepdf` available (Python dependency of `column_tools.py`), the script aborts for safety rather than silently skipping B5 validation.

### Source-preserving mode: `--output FILE`

The interface for GUI callers such as the Obsidian plugin. The same checks run, but the source is never written:

- `FILE` is resolved (symlinked folders included) before any processing. The script refuses a destination that is the source under any spelling, that already exists (file, folder or dangling symlink), or whose folder is missing.
- Existing text is preserved: `--force-ocr` is only passed if the caller adds it.
- Failed checks, a failing `pdf-combine` and cancellation (`SIGTERM` to the process group) write nothing: no destination, no `_FAILED_` file, no hidden temporary copy. Without `pikepdf` the script fails before OCR.
- Publication never overwrites. The result is copied to a hidden `.<FILE>.XXXXXX` file in the destination folder and hard-linked (`ln`) to `FILE`; `ln` fails if `FILE` appeared while OCR was running. Afterwards the script confirms the link really is `FILE`, because BSD `ln` puts the link *inside* a folder that appeared under that name.
- Hard links were verified inside iCloud Drive vaults (`~/Documents` and the Obsidian iCloud container). There is no `mv` fallback: on a filesystem without hard links, publication fails cleanly instead of accepting a check-then-rename race. macOS `mv -n` also exits 0 when it refuses to overwrite, so it cannot report that race.
- `SIGKILL` cannot be trapped. If it lands between the hidden copy and its removal, a hidden `.<FILE>.XXXXXX` file can remain; it never carries the `.pdf` suffix. Cancel with `SIGTERM` to the process group first.

### `column_tools.py verify-pages`

```bash
column_tools.py verify-pages <pdf> [--min-chars N] [--allow-pages LIST]
```

Underlying verification tool, executable independently: extracts every page via `pdftotext -raw`, reports pages falling below threshold (excluding `--allow-pages`) to stderr, and exits code 1 on violations.

## Pre-OCR Pipeline (Automated)

Prior to OCR, every PDF passes through three automated stages without requiring flags:

```
Stage 1: MediaBox Fix        Stage 2: Downscale         Stage 3: Column Split
┌──────────────────┐       ┌─────────────────┐        ┌──────────────────┐
│ Page > 650×900   │  →    │ 300 DPI         │   →    │ (if --split-     │
│ pts?             │       │ Bicubic         │        │  columns active) │
│ → scale to A4    │       │                 │        │ Left + right     │
│   (595×842 pts)  │       │                 │        │ half-page        │
└──────────────────┘       └─────────────────┘        └──────────────────┘
```

### Stage 1: MediaBox Fix

**Problem**: Certain PDFs (typically Hemmer scans) define MediaBox using image pixel dimensions (e.g., 2439×3413 pts @ 72 PPI), setting logical page dimensions to 33.9 × 47.4 inches. Rasterizing at 300 DPI during OCR yields 144 megapixels per page, exceeding available RAM (even on 16 GB systems).

**Solution**: `fix_mediabox()` identifies pages exceeding 650×900 pts and scales them via Ghostscript `-dPDFFitPage` to standard A4 (595×842 pts). At 300 DPI, this consumes only 8.7 megapixels per page, running comfortably on 8 GB RAM systems.

No flag required — executes automatically prior to downscaling.

### Stage 2: Pre-OCR Downscaling

Implemented via Ghostscript intermediary stage between MediaBox Fix and OCRmyPDF:

```bash
gs -sDEVICE=pdfwrite \
   -dDownsampleColorImages=true -dColorImageResolution=300 \
   -dColorImageDownsampleType=/Bicubic \
   -dDownsampleGrayImages=true  -dGrayImageResolution=300 \
   -dGrayImageDownsampleType=/Bicubic \
   -dDownsampleMonoImages=true  -dMonoImageResolution=300 \
   -dMonoImageDownsampleType=/Bicubic \
   -sOutputFile=downscaled.pdf input.pdf
```

**Rationale**: Phone scans and online tools produce 400-600 DPI files → 300+ megapixels per page → exceeds PIL allocation limits → OOM crash. 300 DPI represents Tesseract's optimal target resolution; Bicubic resampling preserves font edge sharpness superior to Ghostscript default `/Subsample`.

**Note**: Ghostscript may temporarily increase file size if input was previously JPEG-compressed. Final file size post OCR + optimization will be reduced.

## Column Splitting (`--split-columns`)

Enabled via `--split-columns` flag in all three CLI scripts. For two-column layouts (Hemmer course materials, Kaiser exams, law journals), every page **detected as two-column** is split vertically prior to OCR, processed separately, and merged back into a single page matching original dimensions:

```
Original (N pages, mixed)            Per page: Detection → Split (if 2-col) → OCR → Merge
┌──────────┬──────────┐    two-col   ┌──────────┐  ┌──────────┐    ┌──────────┬──────────┐
│   Left   │  Right   │  detected → │   Left   │  │  Right   │ →  │   Left   │  Right   │
│  Column  │  Column  │             │  Column  │  │  Column  │    │  Column  │  Column  │
└──────────┴──────────┘              └──────────┘  └──────────┘    └──────────┴──────────┘

┌────────────────────┐   single-col  ┌────────────────────┐
│ Single-column page │ → detected →  │ Single-column page │ (passed through unchanged)
└────────────────────┘               └────────────────────┘

Output: N pages, original size — guaranteed correct reading order per page
```

Column mixing (alternating lines between left and right columns) becomes structurally impossible for detected two-column pages. Single-column pages (cover sheets, diagrams, single-column court decisions) pass through untouched. Output PDF retains exact page count of original document.

### Detection

Line-based analysis built on `pdftotext -bbox` (when text layer exists, e.g., during internal auto-retry on failed quality gate): word bounding boxes are clustered into visual lines; lines are classified as "left", "right", or "full" (spanning across page midpoint, e.g. headers).

Decision logic matches each left line to its **nearest right line of similar vertical center** (rather than global page min/max edges) and validates line-pair gutter against a plausible width range (3–15% of page width). A page is classified as two-column if a minimum fraction of matched pairs (≥ 30%) exhibits a plausible gutter. This pairwise approach is essential because global edge detection fails on skewed photographed binder pages (skew shifts column gutter diagonally, causing global min/max calculation to yield negative gutter despite genuine two-column layout) — validated on real Hemmer scan material. The pairwise test also filters out sliver false positives (e.g., binder photos catching a sliver of an adjacent page): image artifacts lacking real second columns fail to form consistent plausible line pairs.

**Before initial OCR, no text layer exists** — standard path in primary pipeline execution. In this case, detection rasterizes page image (`pdftoppm`) and scans for a continuous vertical gutter band.

`--split-columns-all` skips layout detection and splits every page (fallback for non-standard layouts).

Implemented via Ghostscript CropBox (split) and pikepdf `add_overlay` (merge); requires no dependencies beyond ocrmypdf venv (`pikepdf`, `PIL`).

`pikepdf` is mandatory for splitting. Without it, `--split-columns` and `--split-columns-all` abort before any processing — a split without its page map cannot be merged back, so there is no fallback that could return half-pages. A split or re-merge that fails at runtime likewise fails the run and writes no file. Workflows without column splitting still run without `pikepdf`; only the automatic column-split retry is skipped.

### Reading Order Post-Merge (`pdftotext -raw`)

Post-split-and-merge text layer geometry is precise (words are positioned accurately), but Poppler default reading order heuristics (`pdftotext` without flags) fail to recognize reconstructed two-column layout, returning line-interleaved text despite correct geometry. Cause: `merge_pdf()` embeds left and right halves via separate `add_overlay` calls; Poppler discards resulting content stream order in default mode in favor of custom spatial block clustering.

**Fix: `pdftotext -raw` instead of default mode.** `-raw` follows content stream emission order instead of Poppler's reconstructed reading order — guaranteed correct because `merge_pdf()` writes left half first, then right half. Verified: yields complete left column followed by complete right column on two-column pages, working identically on single-column pages. Skill uses `-raw` internally across all quality gates (`quality_check` in `pdf-lib.sh`, `verify_ocr_split` in `column_tools.py`); apply `-raw` flag when processing these PDFs in downstream tools or scripts.

## Memory Profile

With automated MediaBox Fix, large scans remain RAM-safe:

| Scenario | Pixels/Page | RAM Requirement |
|---|---|---|
| A4 Page @ 300 DPI (post MediaBox Fix) | 8.7 MP | ~150 MB/page |
| Hemmer Two-Column Half-Page @ 300 DPI | 4.3 MP | ~80 MB/half |
| Unfixed Original (72 PPI MediaBox) @ 300 DPI | 144 MP | **OOM** (>8 GB) |
| 50 A4 Pages, jobs 1 (8 GB Mac) | 8.7 MP each | ~500 MB peak |

`detect_safe_jobs()` detects system memory and sets `--jobs` to 1 on 8 GB Macs. Overridable via `--jobs N`.

`ocrmypdf --max-image-mpixels` is configured to 400 MP in `build_ocr_args` (accommodates edge cases lacking MediaBox Fix) passed as CLI argument rather than environment variable (as ocrmypdf ignores `PILLOW_MAX_IMAGE_PIXELS`).

## Stage 2: Accepted Input Formats

`pdf2md.py` takes a PDF or a single page image:

| Format | Suffix |
|---|---|
| PDF | `.pdf` |
| Image | `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp` |

```bash
pdf2md raw/ZR/scan.png --out _ocr-vorschau
```

An image is normalized into a one-page PDF at the input boundary
(`open_document()` in `conversion.py`), so layout detection, box detection,
reassembly and the dictionary pass stay PDF-only and need no second code path.
The wrapped page carries the image as a full-page raster: its text layer is
empty and `image_ratio()` returns 1.0, so the page lands on the OCR branch
exactly like a scan inside a PDF would.

Anything else is rejected by suffix before a single page is processed, with a
one-line error (exit code 1) naming the accepted formats. WebP and HEIC are not
on the list because fitz does not open them; HEIC would need `pillow-heif`.

A **multi-frame TIFF** — what a sheet feeder emits — is rejected too, by frame
count rather than by suffix, and therefore only once the file is opened:

```
multi-page image not supported: stapel.tif carries 3 frames — convert it to a PDF first
```

fitz opens such a file as several pages, but the model is handed the source
file itself and PIL reads only its first frame, so every page after the first
would quietly repeat page 1; the assumed-A4 path below is worse still and
drops the extra frames without a word. Combining several pages is what the PDF
input is for.

Four consequences worth knowing:

- **The source resolution is preserved.** The page image handed to the model is
  the input file itself, not a `--dpi` re-render of it — a 400 dpi scan is not
  resampled down to 150. `--dpi` therefore has no effect on image input.
  Two exceptions are rewritten as a PNG with the same pixels: a photo whose
  EXIF orientation says it is stored sideways is turned upright (fitz and the
  model honour EXIF, the tilers would not), and a CMYK file is converted to
  RGB so its tiles can be saved.
- **The paper size is assumed when the file does not declare one.** fitz reads
  an image that carries no resolution metadata (most JPEGs) at 96 dpi, which
  would make an A4 scan a 49-inch page and throw off the ink-based length
  check that catches derailed generations. Such an image is laid out on an
  assumed A4 page instead, keeping every pixel. A file that *does* declare its
  resolution is taken at its word.
- **One image is one page** — enforced, not assumed: a file carrying more than
  one frame is rejected (above). Header/footer detection in `running_lines()`
  needs at least two pages, so it contributes nothing here. Combining a folder
  of images into one Markdown file is a separate feature.
- **A shared basename only matters once it overwrites something.** `scan.pdf`
  and `scan.png` both write `scan.md`. The plugin blocks the conversion when a
  preview of that name already exists and did not come from the file being
  converted (`previewSource()` reads its `quelle-pdf`); otherwise it says the
  name is shared and continues. Vetoing on the mere existence of a rival was
  tolerable while only PDFs could be sources — with images convertible, every
  same-named attachment in the vault would block a conversion that destroys
  nothing.

Stage 1 (`bin/`) remains PDF-only: `pdf-auto`, `pdf-combine`, the column split
and the text-layer checks all assume PDF input. In the plugin this is the
difference between **OCR → Markdown** (PDF or image) and **Create searchable
copy (OCR)** (PDF only).

## Stage 2: Module Structure (`pdf2md/`)

`pdf2md.py` is the CLI adapter. It translates arguments, console events, and
exit codes, while the document conversion itself is exposed through a stable
Python interface:

`convert_document(request, ocr_adapter, event_sink) -> ConversionResult`

```
pdf2md.py (argparse / console / exit translation)
   └── conversion.py   Request-scoped document runner and result writing
       ├── layout.py     Geometry: columns, boxes, tables, diagrams
       ├── ocr.py        Tiling, model invocation, derailment / repair
       ├── assembly.py   Markdown reassembly (pure functions)
       ├── dictionary.py Dictionary verification post-reassembly
       └── page_cache.py Atomic per-page JSON cache and input fingerprints
```

`ConversionRequest`, `AnalyzedPage`, `AssemblyResult`, and `ConversionResult`
replace positional runner state. Temporary files and repeated-header context are
scoped to one request. Tests can provide a lightweight OCR adapter and collect
structured events without invoking argparse or intercepting `sys.exit`.

Run `python3 -m pytest pdf2md/test -q` for the conversion and pure-function
tests. Heavy dependencies (`fitz`, `numpy`, `PIL`, `mlx_vlm`) remain loaded
inside their runtime boundaries; importing the conversion interface does not
load the ML model.

Scan pages are rendered into a temporary directory below the system temp folder
(`$TMPDIR`), never below `--out`: the plugin points `--out` at a vault folder,
and Obsidian would index every intermediate PNG. `ConversionRequest.temp_root`
overrides the location. Image input is copied there instead of rendered, for
the same reason — `tile_vertically()` writes its tiles beside the page image,
which must not be the user's own folder.

**Vault Copying**: `.ocr-bench/` in vault uses a flat structure (see
`bench/paths.py`, two-location convention) requiring **nine** files:
`pdf2md.py`, `conversion.py`, `layout.py`, `ocr.py`, `assembly.py`,
`dictionary.py`, `cancellation.py`, `page_range.py`, and `page_cache.py`.
Missing files trigger `ModuleNotFoundError`. For the same reason, legal term
lists are embedded directly within modules rather than separate data files —
a `daten/` directory would be lost during flat file copies.

## Stage 2: Dictionary Verification (`dictionary.py`)

Executes post-reassembly across **every OCR page** — skipping native textlayer pages whose text is exact and would produce false positives. Unrecognized terms are logged as `⌕` lines in execution output and added to `woerter-verdaechtig` in frontmatter.

| Flag | Effect |
|---|---|
| *(Default)* | Reporting mode only; document text remains unaltered |
| `--woerterbuch-korrigieren` | Replaces unambiguous OCR errors (see below) |
| `--woerterbuch <file>` | Custom wordlist or `.dic` file (repeatable) |
| `--woerterbuch-bericht <file>` | Export complete findings with page numbers as JSON |
| `--kein-woerterbuch` | Disable dictionary checking completely |

**Unambiguous** definition: term does not exist in dictionary, and exactly *one* substitution variant from OCR confusion table (`m`/`rn`, `ff`/`i`, `l`/`1`, `u`/`ü`, etc.) exists in dictionary. If multiple matches exist (`Hans`/`Haus`), term is preserved and flagged only. Citations, numbers, abbreviations, tables, wikilinks, and footnote markers are skipped — resolving Roman numeral `I` vs `1`/`l`/`|` is explicitly outside module scope.

**Dictionary Resolution Order**: `--woerterbuch`, then `$PDF2MD_WOERTERBUCH` (colon-separated), then first available system dictionary (`/opt/homebrew/share/hunspell`, `/usr/share/hunspell`, `~/Library/Spelling`, LibreOffice bundle). If `hunspell` with German dictionary is present, it takes precedence — evaluating affix rules for higher accuracy than simple fallback substitution rules. If no dictionary is found, execution reports status and skips verification.

Without system dictionary pre-installed, download files manually:

```bash
curl -o ~/.local/share/de_DE.dic \
  https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.dic
curl -o ~/.local/share/de_DE.aff \
  https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.aff
export PDF2MD_WOERTERBUCH=~/.local/share/de_DE.dic
```

The accompanying `.aff` file is required: `SET` header defines encoding (`de_DE_frami.dic` uses ISO-8859-1). If missing, file is parsed as UTF-8, breaking dictionary lookup for all terms with German umlauts.

**Benchmark**: Tested on 202 words of legal German against `de_DE_frami`: 0 false positives, 6 of 7 introduced OCR errors identified. The 7th (`Verhaltungsakte`) demonstrates documented limitation — morphologically well-formed pseudo-word decomposed by compound rule into `verhalten` + `Akte`. Strict rules would cause false positives on compound nouns, as `.dic` dictionaries delegate compound analysis to affix rules.

## --seiten (Stage 2)

Convert selected pages only. Format as comma-separated list with page ranges (e.g. `1,3-5,8`). Omit or leave empty for all pages.

```bash
python pdf2md/pdf2md.py raw/ZR/skript.pdf --seiten "1,3-5" --out _ocr-vorschau
```

- Page numbers are 1-based matching original PDF. Image input has exactly one
  page, so `--seiten` accepts only `1`.
- `--diagramm-seiten` uses the same grammar. Whitespace around entries is ignored.
- Empty entries (`1,,3`, `,`), page 0, descending ranges (`5-3`), non-numeric entries and pages beyond the end of the PDF are rejected with a one-line error (exit code 1) before any page is processed.
- `laufende_zeilen()` (header/footer detection) evaluates entire document so boilerplate analysis remains unaffected by page filtering.
- Generated `.md` retains original PDF page numbers in markers (`%% p. N %%`). Frontmatter `seiten` records count of selected pages.
- Plugin queries selection via `SeitenAuswahlModal` (total page count rendered via pdf.js).

## Page Cache and `--neu` (Stage 2)

Every completed page is written atomically below
`<out>/.cache/<pdf-stem>/<page>.json`. The JSON contains parsed lines with
their boxes, source and layout metadata, and derailment/repair traces. Each
run persists its pages while assembling from memory; a resumed or repeated run
reads matching pages back from disk instead of recomputing them. Consequently,
a stopped run resumes at the first missing page by default and a second pass
can run without loading MLX when all OCR pages are cached.

The cache key includes the PDF SHA-256 digest, DPI, tiling threshold, bold and
OCR-only modes, retry count, prompt, diagram-image mode, and the OCR model name
and locally resolved revision. Textlayer pages never touch the model, so their
key excludes the model name, revision, and prompt — a model upgrade does not
discard them. An unresolvable model revision never reuses a cached OCR page:
the fingerprint is unique per run, so unknown versions are always recalculated.
A changed input or parameter is otherwise a cache miss; corrupt entries, invalid
boxes, and older-schema entries are likewise recalculated.

```bash
# Resume automatically, reusing every matching page
pdf2md raw/ZR/skript.pdf --out _ocr-vorschau

# Recalculate every selected page
pdf2md raw/ZR/skript.pdf --out _ocr-vorschau --neu

# Recalculate only pages 12-14; reuse all other matching pages
pdf2md raw/ZR/skript.pdf --out _ocr-vorschau --neu "12-14"
```

`--refresh-cache` is the English alias of `--neu`. The optional range uses the
same grammar and validation as `--seiten`. Dictionary reporting/correction and
Markdown formatting are intentionally not part of the key: they are rerun from
the cached raw lines on every invocation.

## --fortschritt (Stage 2)

Machine-readable progress emitted as JSON lines to stderr. Default console output (German sentences, emojis, arrows) remains unaffected. Passing `--fortschritt` streams one JSON event per status change to stderr without altering stdout.

### Emitted Events

One event object emitted per state transition. Downstream parsers must accept and ignore unknown fields.

```json
{"typ":"start","datei":"…","seiten":42,"dpi":150}
{"typ":"seite","nr":7,"von":42,"sekunden":31.2,"herkunft":"ocr","entgleist":false}
{"typ":"seite","nr":8,"von":42,"sekunden":44.1,"herkunft":"ocr","entgleist":true,"grund":"zu lang 324%"}
{"typ":"fertig","ziel":"…","sekunden":1284.0,"entgleist":1}
```

- **start** — post PDF analysis: filename, page count, DPI
- **seite** — per page: page number, total pages, elapsed seconds, provenance (`textlayer`/`ocr`/`diagramm`), derailment flag, optional cause
- **fertig** — post completion: total execution time, output path, total derailments

### Schema Contract

Additional fields may be introduced to events in future revisions. Parsers (plugins, UIs, external tools) must ignore unrecognized fields without throwing errors.

## --check (Stage 2)

Preflight check without model execution and without input PDF. Responds in under
one second. `--check` rejects superfluous PDF arguments with an error. Without `--out`,
`--check` tests against a temporary directory and does not create the default `pdf2md/out-C`
folder. Checks:

| Name | Check | Missing if |
|---|---|---|
| `python` | Python version | < 3.9 |
| `fitz` | PyMuPDF importable | Import fails |
| `mlx_vlm` | MLX-VLM importable | Import fails |
| `modell` | Model in HuggingFace cache | No cache entry found (respects `$HF_HUB_CACHE`, `$HF_HOME`) |
| `ausgabe` | Write permission on `--out` | Directory cannot be created / not writable |
| `speicher` | Total RAM (macOS `sysctl`) | `n/a` = unknown still ok; < 8 GiB = warning, no abort |

Human output: one line per check with `[ ok ]` or `[fehlt]`, plus warning lines and summary.

```bash
pdf2md.py --check --out _ocr-preview
# [ ok ] python: 3.12.4
# [ ok ] fitz: 1.24.10
# [fehlt] mlx_vlm: nicht installiert
# [fehlt] modell: nicht im Cache (~/.cache/huggingface/hub/models--...)
# [ ok ] ausgabe: .../_ocr-preview
# [ ok ] speicher: 16.0 GiB
# —
# fehlgeschlagen
```

`--check --progress` (or `--check --fortschritt`) outputs the same as a single JSON document on stdout:

```json
{"typ":"check","ok":false,"checks":[{"name":"python","ok":true,"detail":"3.12.4"},…],"warnungen":["speicher: …"]}
```

### Exit Codes

- **0** — all checks passed
- **2** — argparse error (e.g. PDF argument with `--check`)
- **4** — at least one check failed
- **1** — (wrapper) MLX venv does not exist

The wrapper logic in `bin/pdf2md` skips its own fitz import check when `--check` is passed, delegating evaluation entirely to `pdf2md.py` for consistent exit codes.
