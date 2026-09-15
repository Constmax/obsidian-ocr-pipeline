# PaddleOCR as a third Stage-1 engine

**Status:** Plan; implementation starts only after the runtime spike passes.

## Goal

Add PaddleOCR as an explicitly selected Stage-1 recognition engine:

```bash
pdf-auto --engine paddle input.pdf
```

The output remains a searchable PDF produced through OCRmyPDF. The engine must
not change the default `auto` policy until the benchmark proves that it is a
better default for a defined document cohort.

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
  policy; or
- implement a second per-page worker process merely to isolate dependencies.

## Fixed design facts

- OCRmyPDF's plugin interface is the integration seam. The plugin supplies
  recognition output; OCRmyPDF retains PDF rasterization, page orchestration,
  text-layer rendering, and final-file validation.
- OCRmyPDF renders hOCR through fpdf2. The order of hOCR words and lines affects
  the PDF content stream and therefore `pdftotext -raw`, copy/paste, and screen
  readers. Geometry does not make multi-column order free.
- OCRmyPDF 17.8.0 uses threads by default. A shared recognition pipeline must
  not be called concurrently unless the pinned runtime is proven thread-safe.
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
so the fixture is deliberately tied to the pinned version. The CI Python job
installs only `pytest` and `pymupdf`; running the fixture in CI requires adding
the pinned `ocrmypdf` to that job.

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
recognition model `latin_PP-OCRv5_rec_mobile`, which covers German, and
configure one ONNX Runtime thread for both intra- and inter-op parallelism.
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

### 2. Implement the OCRmyPDF adapter conservatively

Implement the complete `OcrEngine` contract and required hooks against the
pinned OCRmyPDF version.

Initial policy:

- expose `deu` to OCRmyPDF and map it to the Latin PP-OCRv5 recognition model;
- force OCRmyPDF to one job from the plugin's option check, with a visible
  warning if a higher value was requested;
- also configure ONNX Runtime for one thread;
- construct the RapidOCR pipeline lazily and reuse it within the process;
- reject sandwich mode with a precise error;
- leave unreachable PDF-generation paths explicit with `NotImplementedError`;
- generate hOCR and plain text from the same ordered line sequence;
- retain all recognized lines initially and map recognition confidence to
  hOCR's 0–100 `x_wconf` range; add filtering only after calibration;
- clamp quadrilaterals to the rendered image, reject degenerate polygons, and
  derive a bounding box plus baseline or angle information; and
- add an optional alignment-debug artifact for upright and skewed fixtures.

Use Tesseract OSD for orientation initially. Measure deskew behavior rather
than returning zero silently; a zero deskew result is acceptable only when the
documented input contract or fixture proves that upstream normalization is
sufficient.

**Complete when:** pure unit tests cover language mapping, confidence mapping,
polygon conversion, and malformed results; the pinned hOCR rendering test
passes; a local searchable PDF has aligned selection on upright and skewed
pages; and no direct recognition call can run concurrently.

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
- Paddle does not add an implicit split retry; explicit user-requested split
  mode remains in force.

Every fallback must be visible in stderr and the final summary, including the
requested engine, the actual engine, and the reason for the transition.

Keep `auto` on the existing Apple/Tesseract policy. PaddleOCR is explicit until
the benchmark justifies a policy change.

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
