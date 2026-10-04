# PaddleOCR as a third Stage-1 engine

**Status:** Steps 0–5 are done (hOCR order #61, runtime spike #62 with a go,
adapter #68, reading order #69 with follow-ups #87, #94, #114, #124, #125, fast
mode 3a #118, resolved engine value #70, plugin option #73). Step 5 (#71,
`bench/ERGEBNIS.md`, Nachtrag 26) **retains** PaddleOCR in fast mode, with one
documented exception, and **removes split mode for PaddleOCR** (#153). Step
6 is done (#72): `setup.sh` installs the plugin into the Stage-1 venv through
`install-paddle.sh` and prefetches the models. Since #198 `auto` prefers
PaddleOCR fast when it is ready. Open: the 90° rotation gap the
benchmark found for every engine (#152). Issue #133 proposes removing
OCRmyPDF from Stage 1, which would rework steps 4 and 6.

## Goal

Add PaddleOCR as an explicitly selected Stage-1 recognition engine:

```bash
pdf-auto --engine paddle input.pdf
```

The output remains a searchable PDF produced through OCRmyPDF. The engine must
not change the default `auto` policy until the benchmark proves that it is a
better default for a defined document cohort. (#71 did; since #198 `auto`
prefers PaddleOCR fast.)

The Obsidian workflow is independently shippable and is specified in
[`stage1-ui.md`](stage1-ui.md). It must work with the existing Apple and
Tesseract engines before PaddleOCR is installed.

## Hypothesis and non-goals

PaddleOCR may improve recognition of German legal material, especially dense
pages, citations, and mixed typography. That is a hypothesis to measure, not a
reason to add another permanent dependency.

This plan does not:

- replace Stage 2 (`pdf2md.py`), which produces Markdown rather than a PDF text
  layer;
- assume that recognition polygons solve reading order;
- bundle PaddleOCR into the Obsidian plugin;
- make PaddleOCR the automatic engine before the benchmark supports that
  policy (it did in #71; `auto` prefers fast mode since #198); or
- implement a second per-page worker process merely to isolate dependencies.

## Fixed design facts

- OCRmyPDF's plugin interface is the integration seam. The plugin supplies
  recognition output; OCRmyPDF retains PDF rasterization, page orchestration,
  text-layer rendering, and final-file validation.
- OCRmyPDF renders hOCR through fpdf2. The order of hOCR words and lines affects
  the PDF content stream and therefore `pdftotext -raw`, copy/paste, and screen
  readers. Geometry does not make multi-column order free. Verified for
  17.8.0 in step 0: the content stream follows hOCR element order exactly.
- OCRmyPDF 17.8.0 uses threads by default. A shared Paddle pipeline must not be
  called concurrently unless the pinned runtime is proven thread-safe.
- Sandwich mode is not a supported shortcut. The engine must emit a text layer
  compatible with the existing Stage-1 pipeline.
- The adapter owns recognition, language mapping, concurrency policy, explicit
  reading order, polygon-to-hOCR conversion, and confidence calibration. Any
  visual alignment error introduced by that conversion is our responsibility.

## Target module

Build a small, testable OCRmyPDF plugin rather than adding Paddle-specific code
to the shell scripts:

```text
ocrmypdf_paddle/
  pyproject.toml
  src/ocrmypdf_paddle/
    __init__.py       # OCRmyPDF hooks and engine adapter
    runtime.py        # lazy RapidOCR construction and language mapping
    ordering.py       # explicit reading-order policy
    hocr.py           # polygon-to-hOCR conversion
  test/
```

The deep interface inside the module is intentionally independent of the
recognition runtime:

```python
@dataclass(frozen=True)
class TextLine:
    text: str
    polygon: tuple[tuple[float, float], ...]
    confidence: float

def order_lines(
    lines: Sequence[TextLine], width: int, height: int
) -> list[TextLine]: ...

def build_hocr(
    lines: Sequence[TextLine], width: int, height: int
) -> str: ...
```

RapidOCR and ONNX Runtime imports remain lazy so ordering and hOCR tests run in
CI without the ML runtime or model weights.

## Execution plan

### 0. Prove the hOCR ordering mechanism

Before integrating PaddleOCR, create a synthetic hOCR fixture whose DOM order
is intentionally different from geometric top-to-bottom order. Render it with
the pinned OCRmyPDF/fpdf2 path and inspect the result with `pdftotext -raw`.

This test establishes which order reaches the PDF content stream. It does not
prove that any proposed multi-column heuristic is correct.

Entry point: OCRmyPDF 17.8.0 no longer exports `HocrTransform`;
`ocrmypdf.hocrtransform` contains only the parser (`HocrParser`,
`OcrElement`, `BoundingBox`, `Baseline`). The renderer is
`ocrmypdf.fpdf_renderer.renderer.Fpdf2PdfRenderer`, which is not public API,
so the fixture is deliberately tied to the pinned version. It runs in its own
CI job, `ocrmypdf`, which installs the pinned version together with Tesseract
and Poppler.

**Observed** (OCRmyPDF 17.8.0, `bin/test/test_hocr_text_layer_order.py`):

- `pdftotext -raw` returns the words in hOCR element order in every case:
  column order, row-interleaved order, and right column first. Geometry
  reorders nothing.
- The same holds for the finished PDF of the full pipeline (pypdfium
  rasterizer, fpdf2 renderer, grafting onto the original page) with a stub
  engine plugin, not only for the renderer alone.
- Plain `pdftotext` without `-raw` ignores element order and reconstructs its
  own reading order; for these short lines it interleaves the columns.

Consequences:

- Reading order is entirely the adapter's responsibility. Emitting lines in
  the order `order_lines()` returns (step 3) controls the text layer; no
  renderer-side sorting interferes.
- OCRmyPDF 17.8.0 requires the `tesseract` binary even when an engine plugin
  does the recognition: its built-in Tesseract plugin checks for it
  unconditionally. Blocking that plugin through `initialize` fails with
  `'OcrOptions' object has no attribute 'tesseract'`. Tesseract therefore
  stays a hard dependency of the Paddle engine (steps 2 and 6).
- 17.8.0 also offers `generate_ocr`, which returns an `OcrElement` tree
  instead of an hOCR file. It was not exercised here; if step 2 uses it,
  extend the test to that path.

**Complete when:** the fixture demonstrates the ordering mechanism, runs in CI
without PaddleOCR, and this document records the observed behavior.

### 1. Run a time-boxed runtime and environment spike

Time-box this step to two hours. Use a disposable Python 3.12 arm64 virtual
environment before touching `setup.sh`.

The runtime is ONNX Runtime via RapidOCR, not `paddlepaddle`. On the target
machine PaddleOCR 3.x downloaded the PP-OCRv5 models and then died with
SIGSEGV inside `libpaddle.so` (Paddle's thread pool; Python 3.12.4, macOS 26.2,
Apple M1). RapidOCR runs the same PP-OCRv5 models, converted to ONNX, without
the Paddle framework. Start with these candidate pins and change them only if
the spike records why:

```text
rapidocr==3.9.2
onnxruntime==1.26.0
```

Instantiate PP-OCRv5 text detection (server and mobile variants) with the Latin
recognition model `latin_PP-OCRv5_rec_mobile`, which covers German. Measure
one and four ONNX Runtime threads (intra- and inter-op); OCRmyPDF still runs
one job, so recognition calls stay sequential either way.
Record the SHA-256 of every model file. RapidOCR downsizes each page to 2000 px
on its long side by default (about 170 dpi for an A4 page rendered at 300 dpi);
measure that default against full resolution.

Exercise:

- one dense single-column page;
- at least three two-column pages;
- German umlauts, section signs, footnote markers, and legal citations;
- one skewed or rotated page;
- five pages in one process; and
- the same page five times.

Record separately:

- cold start and model-download time;
- warm time per page;
- peak resident memory and swap activity;
- hashes of normalized recognition and ordered hOCR output (not the final PDF,
  whose metadata may vary);
- the exact model identifiers and cache paths; and
- whether a warm run succeeds without network access.

Repeat the installation in a disposable clone of the pinned OCRmyPDF
environment, including `ocrmypdf-appleocr==0.3.4`. Run `pip check` and smoke-test
the existing Apple and Tesseract engines.

Abort the Paddle plan if any of these hold:

- no usable Python 3.12 arm64 package exists for the chosen runtime;
- warm recognition exceeds 15 seconds per page on the target machine;
- one job consumes more than half of physical RAM or causes swap pressure;
- repeated runs are materially nondeterministic; or
- the shared environment has dependency conflicts or breaks an existing
  engine.

A shared-environment conflict triggers a redesign decision. Do not hide it by
shelling out to a fresh recognition process per page; that would reload the
model and invalidate the performance design. A separate environment would
require a persistent worker and a new benchmark.

**Complete when:** exact commands, versions, model identifiers, measurements,
and a go/no-go decision are recorded in `bench/ERGEBNIS.md`.

**Result (2026-09-15, `bench/ERGEBNIS.md`, Nachtrag 18):** go. PP-OCRv5 mobile
detection with `latin_PP-OCRv5_rec_mobile` at full resolution and four ONNX
Runtime threads stays at or below 10.5 seconds per page with a 2.5 GB peak and
deterministic output. One thread, server detection, and PP-OCRv6 `small`
exceeded the time limit.

### 2. Implement the OCRmyPDF adapter conservatively

Implement the complete `OcrEngine` contract and required hooks against the
pinned OCRmyPDF version.

Initial policy:

- expose `deu` to OCRmyPDF and map it to the Latin PP-OCRv5 recognition model;
- force OCRmyPDF to one job from the plugin's option check, with a visible
  warning if a higher value was requested;
- configure ONNX Runtime with the thread count accepted in step 1;
- construct the RapidOCR pipeline lazily and reuse it within the process;
- reject sandwich mode with a precise error;
- leave unreachable PDF-generation paths explicit with `NotImplementedError`;
- generate hOCR and plain text from the same ordered line sequence;
- retain all recognized lines initially and map recognition confidence to
  hOCR's 0–100 `x_wconf` range; add filtering only after calibration;
- clamp quadrilaterals to the rendered image, reject degenerate polygons, and
  derive a bounding box plus baseline or angle information; and
- add an optional alignment-debug artifact for upright and skewed fixtures.

Use Tesseract OSD for orientation initially; Tesseract must be installed
anyway (see step 0), so this adds no dependency. Measure deskew behavior rather
than returning zero silently; a zero deskew result is acceptable only when the
documented input contract or fixture proves that upstream normalization is
sufficient.

**Complete when:** pure unit tests cover language mapping, confidence mapping,
polygon conversion, and malformed results; the pinned hOCR rendering test
passes; a local searchable PDF has aligned selection on upright and skewed
pages; and no direct recognition call can run concurrently.

*Implemented in #68* as `ocrmypdf_paddle/` (load with
`--plugin ocrmypdf_paddle`). Decisions beyond the list above:

- **Threads:** four ONNX Runtime threads, as accepted in step 1. One thread
  failed the time limit there, so the issue text's single thread was not
  used.
- **Selection:** OCRmyPDF 17.8.0 offers only `auto`, `tesseract` and `none`
  for `--ocr-engine`. Loading the plugin makes it the engine behind `auto`,
  like the Apple plugin. An explicit `tesseract` or `none` leaves it inactive.
- **Concurrency:** the plugin forces `jobs` to 1 and warns only when more
  jobs were requested. A process-wide lock around pipeline construction and
  every recognition call also covers bypassed job settings.
- **No hidden downloads:** RapidOCR downloads a missing or mismatching model
  on its own. Before any page, the plugin checks the pinned `rapidocr`/
  `onnxruntime` versions and the SHA-256 of the three model files. The
  detector, recognizer and classifier are all required, because RapidOCR
  loads the classifier even with `use_cls` off. The model directory is
  `OCRMYPDF_PADDLE_MODEL_DIR` (default `~/.cache/ocrmypdf-paddle/models`);
  step 6 fills it during setup.
- **Everything kept:** `Global.text_score` is 0, so RapidOCR drops no line
  before calibration. Pages with a long side over 4000 px are downscaled
  first, and their coordinates are mapped back.
- **Word boxes:** words use RapidOCR's word boxes, with character pieces
  merged per word, when the pieces spell the line's words exactly.
  Otherwise the words are spread across the line box. RapidOCR 3.9.2 drops
  the word group of a line without word boxes and then indexes the groups
  per line. The plugin therefore attaches groups only when each line has
  exactly one, and retries a page without word boxes if RapidOCR raises
  `IndexError` there.
- **Malformed results:** boxes, texts and scores of different lengths reject
  the page. Individual unusable polygons or empty texts drop that line from
  both hOCR and sidecar.
- **Deskew:** delegated to Tesseract, like orientation.
- **Debug artifact:** `OCRMYPDF_PADDLE_DEBUG_DIR` writes one JSON file per
  page with the recognized lines and polygons.

**Observed** with the spike environment (OCRmyPDF 17.8.0, RapidOCR 3.9.2,
ONNX Runtime 1.26.0, network blocked). Pages were converted to 300-dpi PDFs.
A PDF word counts as aligned when the centre of its `pdftotext -bbox` box lies
inside the polygon of the recognized line that contains it:

| Page | Time | PDF words | On their own line |
| --- | ---: | ---: | ---: |
| 04 upright | 11 s | 341 | 100 % |
| 09 turned 4°, no `--deskew` | 10 s | 442 | 100 % |
| 09 turned 4°, `--deskew` | 12 s | 341 | 100 % |
| 08 skewed two-column scan | 17 s | 1023 | 100 % |

- **Deskew:** Tesseract measured 3.896° on the 4° page. After deskewing,
  every `pdftotext` word matched a recognized word.
- **Without deskew:** selection still follows the tilted lines, but
  `pdftotext -bbox` splits words on rotated baselines into pieces (442
  instead of 341). The skewed scan shows the same effect (59 pieces), so
  word-level text metrics in step 5 should not rely on `pdftotext` alone.
- **Text:** on page 04, `pdftotext -raw` has 38 umlauts, 5 ß and 1 §, the
  same counts as in the spike.

### 3. Make reading order an explicit algorithm

`order_lines()` must handle at least:

- ordinary single-column pages;
- two-column pages;
- full-width headings between column regions;
- full-width footers; and
- margin notes or footnotes without interleaving the main text.

Create a truth set of at least 14 hand-checked pages and compare:

1. the current split-and-merge workflow;
2. native unsplit OCR; and
3. PaddleOCR on the unsplit page with explicit ordering.

Measure ordering as pairwise precedence accuracy over matched truth lines: for
each pair of matched lines, count whether the extracted sequence preserves the
truth order. Report unmatched and duplicated lines separately so recognition
errors cannot disappear inside the ordering score.

Until the unsplit algorithm passes the gate, the supported command is
`--engine paddle --split-columns`. Do not present unsplit geometry sorting as a
feature merely because a simple y/x heuristic looks plausible.

The split requirement may be removed only when:

- full-width elements are not duplicated or assigned to the wrong column;
- no page contains manual column interleaving;
- median ordering accuracy is within one percentage point of the current split
  baseline; and
- page count, file-size, and B5 text-coverage checks still pass.

An earlier draft also required every truth page to yield the exact expected
sequence. That made the median criterion redundant, and on pages with margin
notes and footnotes it would likely never pass, silently keeping split mode
forever. The median criterion is the gate.

**Complete when:** the truth set and comparison output are reproducible, and
the result explicitly says either “keep split mode” or “unsplit is supported.”

**Implemented** in #69:

- **`ordering.order_lines()`:** pure geometry, used by `generate_hocr`. It
  estimates the skew from the median direction of long lines and measures every
  line deskewed. When no gutter shows there, it looks again on the page as
  scanned: a column bowed towards the spine slopes its lines while the column
  edges stay upright, and the median slope is then no rotation (#240). Header and footer bands end at a horizontal gap of at least
  one line height and 1.5 times the usual leading, in the top 22 % or bottom
  12 % of the page. A gutter lies between 30 % and 70 % of the text width,
  almost no narrow line crosses it, and lines stand side by side on both
  sides. At most 5 % of all narrow lines may cross it, but crossings are
  counted only on lines between the top 22 % and the bottom 12 %, so a
  running header or footer that no gap cuts off does not hide the gutter
  (#114). A page with fewer than 8 narrow lines there counts crossings on
  all narrow lines. A line crossing the gutter with no column line beside it separates
  sections; each section is read left column, then right. Within a column,
  lines are read in rows, and short lines standing in the margin follow their
  column. The debug JSON lists the chosen order.
- **Truth set:** `bench/reading_order_truth.json`, 16 vault pages (10 two-column,
  6 single-column). Role regions (header, heading, body, footnote, note,
  footer) were drawn on gridded page images before any ordering output for
  those pages was looked at. Truth lines are the PP-OCRv5 lines, each assigned
  to the smallest region containing its centre and read in rows. Overlays
  (`bench/reading_order.py overlay`) were checked by hand; no recognized line
  lies outside every region.
- **Metric:** `bench/reading_order.py score` finds every truth line of at least
  10 normalized characters in the `pdftotext -raw` output by 4-gram voting.
  Longer lines claim their text first, so a citation repeated in a footnote
  does not take the place of the body line containing it. The score is
  pairwise precedence accuracy over matched lines. Reported separately:
  unmatched and duplicated lines, full-width lines out of order against a
  column line, sections whose body columns interleave, page count, B5 (at
  least 50 characters) and output size. No numeric file-size gate exists in
  the Stage-1 scripts, so size is reported, not gated.
- **Not covered by a real page:** a full-width heading *between* two column
  regions. The truth set has full-width titles only above the columns; the
  case between regions is covered by the synthetic tests in
  `test_paddle_ordering.py`.

**Result:** **keep split mode.** `--engine paddle --split-columns` stays the
supported command. Numbers, pinned versions and commands are in
`bench/ERGEBNIS.md`, Nachtrag 19.

- Unsplit PaddleOCR with `order_lines()` has a higher median accuracy than
  both split baselines, but it places full-width lines inside a column on
  several pages, which the gate forbids.
- **Running header:** on the repetitorium pages the header's last row sits
  less than one line height above the columns. No header band is cut, and the
  right part of that row ("…, Seite N") is read as the first line of the right
  column. On two pages the header's multi-line location list follows it there.
- **Footnotes across the page:** where both columns' footnotes start at the
  same height, the gap above them runs across the page. The footnotes fall
  into the footer band and are read in rows across both columns.
- **Split baselines:** they fail the full-width criterion more often. The split
  cuts the header row, so its right half follows the whole left column.
- **Next attempt:** a fix for the two cases must be measured on pages outside
  this truth set. Tuning against the same 16 pages would not show that the
  gate passes.

**Follow-up** in #87 (`bench/ERGEBNIS.md`, Nachtrag 20):

- **Cause:** detected boxes on the dense scans overlap vertically, so the
  measured leading is 0 and the header sits 1–27 px above the columns. No gap
  threshold separates them.
- **Column bands:** on a two-column page `order_lines()` now looks for column
  pairs: a left and a right line side by side, the right one starting within
  2.5 line heights of where the right column's full lines start. The right
  part of a running header is right-aligned and starts further in.
  - The header reaches the lowest gap between line cores above the first
    column pair.
  - Footer rows go back to the columns from the top while a row holds lines
    of one column only or a column pair, and one of them holds a pair. The
    footer starts at the first row crossing the gutter or covering both sides
    without a pair.
- **Validation set:** `bench/reading_order_holdout.json`, 13 pages from
  documents outside the first set (12 two-column, 1 single-column). They were
  chosen after the fix was committed, and their regions were drawn and checked
  before any ordering output for them was looked at. `bench/reading_order.py
  --truth` scores them separately.
- **Old 16 pages:** unsplit PaddleOCR has no misplaced full-width lines and no
  interleaved pages (median 100.0 %). These pages exposed the failures, so this
  does not decide the gate.
- **Result on the new pages:** **keep split mode.** #71 does not compare
  unsplit PaddleOCR.
  - The median is 100.0 % against 94.8 % for the split baselines, page checks
    pass, and no full-width line is misplaced.
  - Three header lines are not found. The real run recognizes the deskewed
    page slightly differently from the truth image.
  - Three pages count as interleaved. On one (n07) the footnotes of both
    columns start level but offset by half a line, so no footer row holds a
    pair and they are read in rows. The other two are ordered correctly; short
    lines repeated in the text (single words, identical citations) confuse the
    text matching.
- **Next attempt:** search the leading footer rows for a pair as a whole
  rather than row by row. That change was found on a validation page, so it
  needs further unseen pages before the gate can pass.

**Follow-up** in #114 (`bench/ERGEBNIS.md`, Nachtrag 24):

- **Cause:** on pages whose line boxes touch (large type, m06) `_bands()`
  cuts off neither the running header nor the footer. Their lines crossed
  the gutter often enough to exceed the 5 % tolerance, and the page was
  read row by row.
- **Fix:** `_gutter()` counts crossings only on narrow lines between the
  top 22 % and the bottom 12 % of the page (all narrow lines when fewer than
  8 lie there). The tolerance stays 5 % of all narrow lines.
- **Validation set:** `bench/reading_order_holdout3.json`, 13 two-column
  pages from documents outside the earlier sets, chosen after the fix was
  committed. One of them (q11) has the m06 failure and is read correctly.
- **Result on the new pages:** **keep split mode.** The median is 100.0 %
  against 94.9 % for the best split baseline, and no full-width line is
  missing, but two rubric lines are read as the first line of the right
  column (#124) and one page with one-column footnotes and no footer is
  interleaved (#125). A full-width heading between column regions was not
  found in the corpus (#120).

**Follow-up** in #124 and #125 (`bench/ERGEBNIS.md`, Nachtrag 25):

- **Causes:** a tilted rubric row whose right part sits half a line lower
  was measured from that lower part and stayed in the columns (q07); a
  right column recognized as single words moved `_right_edge()` into the
  column, so the rubric's right part paired with its label as the first
  column row (q06, real run); footnotes of one column in the footer band
  never pair across the gutter and were read after both columns (q03).
- **Fix:** the lowest grown header row is measured at its upper part, like
  the first column pair, counting only parts of a similar height; column
  edges (`_edge()`) and the line pitch are measured on visual rows; leading
  footer rows go back to their column when they stand on one side of the
  gutter, open with a footnote numeral hanging left of that column's text
  edge with its text beside it, and every row starts at that edge.
- **Known limit:** footnotes whose numeral is merged into the text box, or
  that have no numeral, still stay in the footer band.
- **Validation set:** `bench/reading_order_holdout4.json`, 13 two-column
  pages from documents outside the earlier sets, chosen after the fix was
  committed. Two of them (r02, r06) had a rubric line in the right column
  before the fix and are read correctly.
- **Result on the new pages:** **keep split mode.** The median is 100.0 %
  against 95.2 % for the best split baseline and no full-width line is
  misplaced, but three location-list lines are not recognized in the real
  run and one skewed page is interleaved: hanging footnote numerals of the
  right column narrow the gutter below half a line height (#127).

### 3a. Fast mode: Apple Vision lines, citations re-read

*Implemented outside the #74 queue* in `ocrmypdf_paddle/src/ocrmypdf_paddle/apple.py`. The plugin
takes `--paddle-mode accurate|fast` (default `accurate`, also as the API
argument `paddle_mode`):

```bash
ocrmypdf --plugin ocrmypdf_paddle --paddle-mode fast -l deu input.pdf output.pdf
```

- **Why:** Apple Vision reads a page 3–7 times faster than RapidOCR on the
  M1 but loses citations ("$ 935" for "§ 935", "§ 568 | BGB"). Its lines are
  ordered as badly as any native order, but with their polygons
  `order_lines()` orders them as well as RapidOCR's (bench/ERGEBNIS.md,
  Nachtrag 22).
- **Pipeline per page:** Vision's `VNRecognizeTextRequest` (accurate,
  `de-DE`, language correction) returns lines as quadrilaterals with word
  boxes. Lines matching `CITATION_HINT` (a "§", "$", "Art." or a number
  followed by a Roman numeral) are read again by RapidOCR's recognizer alone,
  without detection (about 8 % of the lines). The crop follows the line's own
  edges with a 15 % margin and is straightened, so a skewed line does not
  bring its neighbours along. Then `order_lines()` and `render_page()` run as
  in accurate mode.
- **Accepting a re-reading:** RapidOCR's text replaces Vision's only with a
  recognition score of at least 0.8 and a `difflib` similarity to Vision's
  text of at least 0.6; the line then carries RapidOCR's score. A citation
  fix changes a few characters, a reading of noise or of a neighbouring line
  most of them. Both thresholds are not calibrated yet (#71).
- **Word boxes:** Vision's word boxes reach into half the space on either
  side. Rendered unchanged, `pdftotext` glues the words of a line together
  (0.3–2 % of the words survived on four truth pages). Accurate mode's text
  layer glues RapidOCR's words the same way. A re-read line keeps Vision's
  word boxes when it has the same number of words, otherwise the words are
  spread across the line box.
- **Selection in viewers** (both modes): trimming every word box by a
  quarter of the line height separated the words for `pdftotext`, but in
  Obsidian (pdf.js) a selection fell apart into one block per word, sat half
  a line too low, and was too tall. `render_page()` now
  - puts the baseline at 75 % of the line height instead of on the box
    bottom (both recognizers' boxes reach below the descenders; measured
    0.68–0.81 for Vision, 0.75 for RapidOCR);
  - places each word's right edge so that 0.25 font sizes remain after the
    space the renderer appends at the word's stretch: `pdftotext -raw` needs
    about 0.12, pdf.js 4.10 keeps a line in one text item up to 0.6. The
    line's first and last word also start and end 0.25 (at most 30 % of the
    word's width) inside their boxes, because recognizers split a printed
    line into touching lines; and
  - writes every line flat. OCRmyPDF renders a line with a slope of 0.005 or
    more rotated, and pdf.js undoes that rotation with a scale that includes
    each word's stretch, so the words of such a line land on different
    heights. Long one-column lines keep that much skew after deskewing, which
    is why two-column pages looked fine. A sloped line becomes flat pieces
    over which the baseline drifts at most 0.2 font sizes.

  On five vault pages (one- and two-column, both modes) pdf.js text items
  dropped from 3,250 to 1,198, and `pdftotext -raw` text is unchanged apart
  from whitespace. 2 of 4,100 words newly glue to a neighbour, where two
  recognized lines overlap; one pair glued on `main` is now separate.
- **Dependencies:** macOS 13 or later with `pyobjc-framework-Vision` (extra
  `[fast]`) plus everything accurate mode needs, because the models re-read
  lines. `check_options` refuses fast mode before the first page when Vision
  is missing, the macOS is too old, or Vision cannot read German; there is no
  silent fallback to accurate. Both modes share one RapidOCR pipeline, created
  only when a page needs it.
- **Benchmark:** `reading_order.py run` starts `unsplit-paddle-fast` only
  when it is named, so the default run works without Apple Vision.
- **Wiring:** `bin/` offers `--engine paddle --paddle-mode accurate|fast`
  since #70 (explicit only; see step 4). Since #73 the Obsidian engine setting
  offers PaddleOCR in fast mode where `reprocess-raw --check-engine` passes.
- **Readiness:** `ocrmypdf --plugin ocrmypdf_paddle --paddle-check MODE`
  runs the same checks as `check_options` without an input file and exits
  like `--version`: 0 ready, 1 not ready (reason on stderr). `bin/` runs it
  before choosing the engine, so a missing model or package stops the run
  before OCR instead of falling back to Apple Vision.

### 4. Replace binary engine flags with one resolved state

Prerequisites: #48 (central validation of common options, including
`--engine`) and #50 (one merge-to-OCR pipeline interface, including retry).
Build on both instead of changing the engine model across three duplicated
scripts. `bin/test/` already runs the real `ocr_with_retry`,
`build_ocr_args`, and `quality_check` against stubs; the selection and
fallback matrix tests belong there.

The shell layer currently models engine selection as a binary Apple/Tesseract
choice. Replace overlapping booleans with one canonical value:

```text
RESOLVED_ENGINE=apple|tesseract|paddle
```

`resolve_engine()` is the single source of truth. Build arguments with one
`case` over that value:

- Apple: Apple plugin arguments;
- Tesseract: Tesseract language and tuning arguments;
- Paddle: local Paddle plugin arguments plus the one-job policy.

Make the retry matrix explicit and testable:

- Apple failure → Tesseract, including the existing split retry where
  applicable;
- Tesseract failure → split Tesseract, then Apple where available;
- Paddle failure → Apple when available, otherwise Tesseract;
- Paddle does not add an implicit split retry. Since #153 it also ignores an
  explicit split request (with a warning), because step 5 found split mode
  worse on every cohort.

Every fallback must be visible in stderr and the final summary, including the
requested engine, the actual engine, and the reason for the transition.

Keep `auto` on the existing Apple/Tesseract policy. PaddleOCR is explicit until
the benchmark justifies a policy change. (Done: #71 retained fast mode, and
since #198 `auto` prefers it when `--paddle-check fast` passes and no split is
requested.)

Update all user-facing and internal engine lists together:

- `bin/pdf-auto`, `bin/pdf-workflow`, and `bin/pdf-combine`;
- engine resolution, OCR arguments, and retries in `bin/pdf-lib.sh`;
- `setup.sh` and `install.sh` only after the benchmark retains Paddle;
- `docs/scripts-detail.md` and `docs/installation.md`;
- `skill/SKILL.md`; and
- the obsolete binary-engine note in `AGENTS.md` during implementation.

**Complete when:** shell tests use fake engine commands to cover the full
selection and fallback matrix, no contradictory engine state remains, every
Stage-1 entry point accepts the same values, and `shellcheck -x -P bin` passes.

### 5. Benchmark the Stage-1 text layer

`bench/bench_ocr.py` measures the Stage-2 Markdown path and is not the direct
harness for this decision. Add a small Stage-1 adapter that extracts both
reference and result text with `pdftotext -raw` and evaluates per page.

The reference must be ground truth, not incumbent output. The
`<name>.baseline.txt` files listed in `bench/BENCHMARK-SET.md` are what the
Tesseract pipeline produced; scoring against them rewards similarity to
Tesseract. Use the Stage-2 technique instead: rasterize born-digital pages,
run them through the Stage-1 path, and compare against their original text
layer. That cohort has no scan noise, skew, or show-through, so the hard scan
cohort (for example `03-durchschlag-handschrift`) is scored by hand against a
hand-checked transcription.

Use separate cohorts for:

- recognition-heavy single-column pages;
- multi-column order;
- short, graphical, or cover pages; and
- skew or orientation cases.

Compare:

- Tesseract with the current split policy;
- Apple OCR;
- PaddleOCR with the safe split policy; and
- unsplit PaddleOCR only if Step 3 passes.

Record word accuracy, citation accuracy, ordering accuracy, page coverage,
wall time, peak resident memory, output size, and repeated-run stability. Pin
the repository commit, package versions, model identifiers, and commands in the
result.

Retain and install PaddleOCR only if:

- it improves at least one primary metric—word or citation accuracy—by at least
  two percentage points over the best relevant existing engine;
- word, citation, and ordering accuracy do not regress by more than one
  percentage point on any protected cohort;
- it introduces no new B5 or page-coverage failure; and
- runtime and memory remain inside the Step-1 limits.

The percentage-point thresholds above are provisional: derive them from the
page-to-page spread of the existing 40-page benchmark before running this
step.

If it misses the gate, keep the documented spike result but do not add Paddle
to `setup.sh` or the public engine list.

**Complete when:** `bench/ERGEBNIS.md` contains a binary retain/discard decision
and a separate keep/remove-split decision.

**Result (2026-09-30, `bench/ERGEBNIS.md`, Nachtrag 26):** retain fast mode;
remove split mode for PaddleOCR. `bench/stage1_bench.py` sends rendered vector
pages through `reprocess-raw --output` and scores `pdftotext -raw` against
their own text layer (cohorts `words`, `short`, `skew` in
`bench/stage1_cohorts.json`); reading order ran on all five hand-checked sets.
The thresholds are 1.96 standard errors of the best existing engine's page
scores per cohort (words 1.3, citations 12.6 points on the 40-page set).
`paddle-fast` against Apple: citations +18.3 points on short pages (above the
threshold), +9.5 on the 40 pages (significant, below it), words +0.6; 4
instead of 30 interleaved pages on 68 two-column scans; 3.2 s/page, 585 MB.
One criterion is missed formally: on one page turned by 90° no engine rotates
the page upright, Apple writes unreadable text that passes B5 and PaddleOCR
writes almost none, which B5 catches (#152). Split mode loses 9 points of word
accuracy on two-column pages, fails the quality gate on slides and breaks
pages turned by 180°. Hard scans were not scored: there is no checked
transcription.

### 6. Make installation reproducible only after retention

After the benchmark retains PaddleOCR:

- pin the accepted packages in the normal Stage-1 environment;
- install the local plugin distribution into that same environment;
- prefetch the exact model weights during setup so the first Obsidian run does
  not perform a hidden download;
- run `pip check` and smoke-test Apple, Tesseract, and Paddle; and
- make Paddle installation failure leave the existing Stage-1 engines usable,
  with a precise recovery command.

**Complete when:** a clean setup can run a warm Paddle smoke test without
network access, and an intentionally failed Paddle installation does not break
Apple or Tesseract processing.

**Result (2026-09-30, #72):** `install-paddle.sh`, called by `setup.sh` ③.
The engine goes into the Stage-1 venv after all, but pip gets every installed
package as a constraint, so it can only add packages. In a clean venv
(`ocrmypdf==17.8.0` and `ocrmypdf-appleocr==0.3.4` only):

- The install found a real pin conflict. `ocrmypdf-appleocr` now brings
  `pyobjc-framework-Vision` 12.2.2, and the `[fast]` extra pinned 12.2.1. The
  extra now accepts 12.x.
- The install then left every existing package unchanged, fetched the three
  models (SHA-256 checked), passed `--paddle-check` in both modes, and read
  its generated page offline, with every proxy on a closed port.
- A second run was a no-op.
- An install forced to fail (pip without network) changed nothing, and Apple
  and Tesseract read the same page afterwards.

## Verification matrix

Automated CI without PaddleOCR:

- all existing `pdf2md/test` tests;
- pure tests under `ocrmypdf_paddle/test`;
- the synthetic hOCR ordering/rendering fixture using the pinned OCRmyPDF
  version;
- shell selection and fallback tests;
- `shellcheck -x -P bin`; and
- existing plugin checks.

Local target-machine verification with PaddleOCR:

- runtime and repeatability spike;
- searchable-PDF and selection-alignment smoke tests;
- orientation and deskew fixtures;
- truth-set reading-order comparison;
- Stage-1 benchmark; and
- warm offline setup smoke test.

## Estimated effort

| Work | Estimate |
| --- | ---: |
| hOCR mechanism and runtime spike | 0.5–1 day |
| Adapter, hOCR conversion, and tests | 1–1.5 days |
| Reading-order truth set and algorithm | 1 day |
| Shell engine-state migration | 0.5–1 day |
| Benchmark and reproducible setup | 1 day |
| **Total if all gates pass** | **4–5 days** |

The plan can stop after the runtime spike or benchmark without leaving a
half-supported public engine.

## External references to verify during implementation

- RapidOCR's documentation and model list, and ONNX Runtime's threading
  documentation, for the pinned versions.
- PaddleOCR discussions or issues covering multi-column reading order.
- Whether concurrent calls into one RapidOCR pipeline are safe in the pinned
  runtime.

Record exact links and retrieval dates with the spike results; do not let
unversioned web documentation override observed behavior in the pinned local
environment.
