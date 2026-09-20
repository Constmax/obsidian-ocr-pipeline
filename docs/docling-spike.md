# Spike #95: Docling against the current PDF-to-Markdown pipeline

Status: **keep the current pipeline** (measured 2026-09-20, see Vault
measurement below). This spike built the measurement harness, verified it on
synthetic pages, ran both paths over the same 16 hand-checked truth pages,
and reached the decision the issue's criteria prescribe. It did not change
any production code path.

Issue: #95 — time-boxed evaluation spike, not a migration. Related tracking
issue: #93. Result contracts and diagnostics (#90, #91, #92) are independent
of the outcome. Stage-1 work (#74 queue, #70, #48) is unaffected.

## Goal

Measure whether Docling's document pipeline converts our scanned legal pages
better than the current `pdf2md/` path before investing further in our own
layout and assembly code (`pdf2md/layout.py`, 474 lines;
`pdf2md/assembly.py`, 560 lines). Those modules are hand-written layout
interpretation and Markdown construction; Docling covers the same ground with
trained models for layout, table structure, and reading order, and exports
Markdown.

Two open questions justified measuring first:

- **Quality.** Docling's reading order is trained on general documents. Our
  material is footnote apparatus, two-column commentary, marginal numbering,
  and diagrams. Whether that transfers is unknown.
- **Cost.** PaddleOCR-VL currently runs at 15–60 s/page on the target machine
  (Apple Silicon, 8 GiB). Docling's default pipeline uses several smaller
  specialised models instead of one large VLM, so it may be meaningfully
  faster on the same hardware. This is a hypothesis, not a measurement.

## Scope

In scope:

- Docling as an evaluation-only adapter producing the same page records the
  existing benchmark consumes (per-page Markdown scored by
  `bench/reading_order.score_page`).
- Both paths over the same single-page PDFs.
- Reading order measured with `bench/reading_order.py` against the existing
  hand-checked truth set (`bench/reading_order_truth.json`, 16 pages
  t01–t16).
- Wall-clock time per page and peak memory on the 8 GiB target machine.
- Structure output (tables, footnotes, diagrams) inspected qualitatively on
  the same pages, with concrete failures recorded with page references.

Out of scope: replacing any production path, plugin changes, Stage-1
changes, and any Docling configuration tuning beyond its documented
local/ARM64 defaults. The PP-StructureV3 alternative candidate is noted in
the issue and is not part of this spike.

## Measurement baseline

`bench/reading_order.py` and its truth set are the reference for ordering.
Structure quality has no comparable hand-checked corpus yet; #20 owns that
work. Until #20 exists, structure findings in this spike are qualitative and
are recorded as such rather than reported as a score.

## Method

New evaluation-only files (all under `bench/`, plus this doc):

- `bench/docling_adapter.py` — one single-page PDF in, one Markdown string
  out, with per-page timing. Uses Docling's documented local defaults: a
  plain `DocumentConverter` with no pipeline overrides (`converter_kwargs()`
  returns `{}` deliberately). Every `docling` import is function-local so
  the module imports without the dependency; `missing_dependencies()`
  reports the reason when it cannot run.
- `bench/spike_docling.py` — harness with three subcommands:
  `check` (is Docling runnable here?), `run --engine {docling,pdf2md}`
  (one process per engine so peak RSS belongs to that engine, same pattern
  as `bench/spike_rapidocr.py`), and `compare` (scores both `run` outputs
  with `reading_order.score_page` / `summarize` and prints which branch of
  the decision criteria the numbers point to).
- `bench/test_spike_docling.py` — 16 pure tests: adapter stays importable
  without Docling, timing/scoring helpers, decision branches, CLI help
  without models or vault.

Reproduction on the 8 GiB target machine (Apple Silicon, macOS, Python
3.10+):

```bash
pip install docling
python bench/spike_docling.py check
VAULT_ROOT=/path/to/vault python bench/reading_order.py prepare
VAULT_ROOT=/path/to/vault python bench/reading_order.py recognize
python bench/spike_docling.py run --engine docling \
  --pages bench/reading-order-lauf/pages/t01.pdf [...] \
  --out bench/docling-lauf --name docling-truth
python bench/spike_docling.py run --engine pdf2md \
  --pages bench/reading-order-lauf/pages/t01.pdf [...] \
  --out bench/docling-lauf --name pdf2md-truth
python bench/spike_docling.py compare \
  --docling bench/docling-lauf/docling-truth/result.json \
  --pdf2md bench/docling-lauf/pdf2md-truth/result.json \
  --truth-lines bench/reading-order-lauf/truth-lines \
  --out bench/docling-lauf/comparison.md
```

`run --engine pdf2md` drives `pdf2md/pdf2md.py` as a subprocess per
single-page PDF (no production import, no production change) and strips the
frontmatter/page marker before scoring. `compare --truth-lines` reuses the
`recognize` records; without them it reports timings only and marks ordering
as missing.

## What was actually measured here

Environment of this spike run: Apple Silicon (arm64), 8 GiB RAM
(`sysctl hw.memsize`), Python 3.12, repository at the #95 spike branch.
Docling **is installed** in an evaluation-only venv following the repo
convention (`VENV_ROOT=~/.venvs`, same `uv venv --seed --python 3.12`
procedure as `setup.sh`, but a separate `docling` venv so the pinned
`ocrmypdf` and `mlxocr` venvs are untouched; `setup.sh` itself was not
changed):

```
$ ~/.venvs/docling/bin/python bench/spike_docling.py check
docling pipeline : standard (documented local default)
docling version  : 2.129.0
status           : runnable (defaults, no tuning)
```

Harness self-verification (synthetic, not vault material):

- `run --engine pdf2md` over two synthetic single-page vector PDFs:
  **0.1 s/page median, 19 MiB peak** in the harness process. Vector pages
  take the textlayer path without the model, so this number says nothing
  about scanned-page cost (15–60 s/page for the VLM path); it only proves
  the `run` plumbing, timing, `result.json`, and per-page `.md` outputs
  work end to end.
- `run --engine docling` over the same two synthetic PDFs (first use,
  models downloaded on demand): **5.4 s/page median, 968 MiB peak** warm
  (the very first page took 28.6 s including model download and cold
  start). Output text was correct on this trivial material (both lines in
  order, `§` preserved). These are vector PDFs, so this measures Docling's
  standard pipeline overhead, not scanned-page OCR cost — recorded here
  only as proof that the adapter works end to end.
- `compare` over those synthetic outputs without `--truth-lines`:
  timings table rendered, ordering marked missing, guidance
  **inconclusive**. With synthetic truth constructed in
  `bench/test_spike_docling.py`, the scorer correctly prefers column-faithful
  order (accuracy 1.0) over interleaved columns (< 1.0).
- Full test suite green: `bench/test_spike_docling.py` (16 passed),
  `bench/test_entrypoints.py` + `bench/test_reading_order.py` (32 passed),
  `pdf2md/test` + `bin/test` (247 passed, 5 skipped).

No vault pages (t01–t16) were scored yet, so there are no vault ordering
numbers and no scanned-page timings for either path. The vault run from the
reproduction steps above is still the missing measurement.

## Vault measurement (2026-09-20)

Ran on the target machine (Apple Silicon, 8 GiB, macOS) with
`VAULT_ROOT` pointing at the vault containing `raw/`. All 16 truth sources
present. Docling 2.129.0, standard pipeline, CPU (layout + RapidOCR
PP-OCRv6 `small` via onnxruntime, all defaults). Current path:
`pdf2md/` defaults (PaddleOCR-VL-1.5-4bit via mlx-vlm, dpi 150,
`tile_from` 3000) driven per single-page PDF through its CLI.

Pipeline: `reading_order.py prepare` (16 pages ok, ocrmypdf venv) →
`recognize` (PP-OCRv5 truth lines, 4–11 s/page, docling venv with pinned
`rapidocr==3.9.2` + `onnxruntime==1.26.0` and SHA-256-verified models in
`~/.cache/ocrmypdf-paddle/models`) → `spike_docling.py run --engine
docling` → `run --engine pdf2md` (sequential, one process per engine) →
`compare --truth-lines`.

### Reading order (truth set t01–t16, `score_page`)

| Page | Docling | Current (pdf2md) |
|---|---|---|
| t01 | 99.3% | 96.1% |
| t02 | 99.2% | 98.6% |
| t03 | 98.6% | 99.2% |
| t04 | 99.2% | 97.2% |
| t05 | 99.2% | 100.0% |
| t06 | 94.3% | 100.0% |
| t07 | 81.1% | 100.0% |
| t08 | 95.1% | 100.0% |
| t09 | 98.7% | 97.9% |
| t10 | 99.0% | 100.0% |
| t11 | 99.5% | 100.0% |
| t12 | 100.0% | 100.0% |
| t13 | 100.0% | 99.1% |
| t14 | 99.1% | 100.0% |
| t15 | 99.5% | 100.0% |
| t16 | 98.7% | 100.0% |

Summaries: Docling median **99.1%** (mean 97.5%, min 81.1%), current path
median **100.0%** (mean 99.3%, min 96.1%). Docling interleaves columns on 4
pages vs 3 for the current path, and misplaces 23 of 77 full-width lines vs
6 (unmatched full-width: 14 vs 66 — the current path drops more lines but
orders what it keeps almost perfectly; its unmatched lines cluster in
box/header/footer zones, e.g. t07 with only 53/64 matched).

The current pipeline's hand-written column/assembly code outscores Docling's
trained reading order on this material. Docling's weak pages are exactly the
layouts the issue worried about: t07 (full-width title above two columns,
81.1%), t06 (boxed notes, 94.3%), t08 (boxed heading and notes, 95.1%).

### Time and memory per page (same 16 pages)

| Engine | Median s/page | Total 16 pages | Peak RSS |
|---|---|---|---|
| Docling | 12.3 | 192 s | 1,515 MiB (in-process) |
| Current (pdf2md) | 65.3 | 969 s | ~1,138 MiB (bench/ERGEBNIS.md; harness process only 21 MiB, see limitations) |

Docling is ~5x faster per page (min 6.8 s vs 27.4 s). Both fit 8 GiB:
Docling peaked at 1.5 GiB in-process with light swap deltas; the pdf2md run
showed sustained swap traffic over its 16 minutes, consistent with the known
~1.1 GiB MLX peak. Raw run records (copyrighted page text) stay in the
git-ignored `bench/docling-lauf/` (`docling-truth/`, `pdf2md-truth/`,
`comparison.md`/`.json`).

## Structure findings (qualitative)

Recorded against the vault outputs above (page-referenced, qualitative until
#20 provides a hand-checked corpus):

- **Duplicated paragraphs (t06, t08, t03, t04, t09).** Docling repeats
  nearly every paragraph back-to-back on two-column pages with boxed notes
  (t06: 54 duplicated truth lines; total 225 vs 12 for the current path;
  output visibly ~1.5x the text, e.g. t06 6,635 vs 4,105 chars). Content
  integrity, worse than ordering noise: repeated paragraphs would surface
  verbatim in the vault.
- **Footnotes unlinked (t01, t02).** The current path emits `[^71]`–`[^75]`
  references with definitions; Docling appends footnote paragraphs as plain
  `- 71 …` bullets and leaves reference numbers naked in the text
  (e.g. `Hauptsache.73`). Footnote apparatus does not transfer.
- **Full-width title misplaced + element dropped (t07).** Docling moves the
  full-width title (`Fall 15 Lösung`) after `FRAGE 1` and emits
  `<!-- image -->` where the current path returns the text. The trained
  layout model misreads exactly the full-width-over-columns construction.
- **Tables: no comparison possible on this set.** Neither output contains a
  Markdown table on t01–t16 (the pages carry boxed notes, not true tables),
  so table-structure quality remains unmeasured.
- Both paths show word-level recognition noise (each misreads `§`/roman
  numerals somewhere); recognition accuracy was not scored here — it
  belongs to #91/`bench_ocr.py`, not to this ordering spike.

## Verdict: keep the current pipeline

Applied the issue's decision criteria to the vault measurement:

- Docling is **worse** on reading order (median 99.1% vs 100.0%, min 81.1%
  vs 96.1%) **and** faster per page (12.3 s vs 65.3 s median). The criteria
  require better-on-order **and** not-slower for a migration; failing the
  first branch means **keep the current pipeline**, even with the ~5x speed
  advantage. The qualitative failures (paragraph duplication, unlinked
  footnotes, misplaced full-width title) independently disqualify Docling
  for this material: they corrupt content, not just order.
- No migration issue is opened. The layout/assembly parts of #93 proceed as
  planned — this verdict unblocks the issues that were waiting on it. #90,
  #91, #92 proceed regardless, as before.

## Limitations

- The `pdf2md` peak RSS in `comparison.json` (21 MiB) is the harness process
  only: that engine converts via one `pdf2md.py` subprocess per page, whose
  memory is not attributed. The comparable figure is the known MLX peak of
  ~1,138 MiB (`bench/ERGEBNIS.md`, same model); the sustained swap traffic
  over the 16-minute run corroborates it. A future harness could read
  subprocess peak via `/usr/bin/time -l` or `resource` wait4.
- Recognition (word) accuracy was not scored; only ordering, time, and
  memory were measured, plus qualitative structure review.
- Table structure is unmeasured: t01–t16 contain no true tables.
- Structure findings are qualitative (no #20 corpus yet), as the issue
  requires.
- Docling defaults may drift upstream; the spike pins the intent
  ("documented local/ARM64 defaults, standard pipeline, no tuning"), not a
  version. The measured version was 2.129.0 (`check` prints it).
- Raw outputs contain copyrighted page text and stay in the git-ignored
  `bench/docling-lauf/`; only aggregates and short phrases are recorded
  here.

## Completion criteria status

- [x] Docling runs locally on the target machine (2.129.0 in `~/.venvs/docling`,
  `check` reports runnable; vault conversion verified end to end).
- [x] Both paths measured over the same vault pages with
  `bench/reading_order.py` (t01–t16, `compare --truth-lines`).
- [x] Per-page time and peak memory for both paths on the 8 GiB machine
  (12.3 vs 65.3 s/page median; 1,515 MiB in-process vs ~1,138 MiB known
  MLX peak — harness attribution limit stated above).
- [x] Structure failures with page references, marked qualitative
  (duplication t06/t08/t03/t04/t09; footnotes t01/t02; title t07; tables
  unmeasurable on this set).
- [x] Written verdict in `docs/` with limitations stated (this file):
  **keep the current pipeline**.

## References

- Docling: https://github.com/docling-project/docling
- PP-StructureV3 (alternative candidate, not in scope):
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md
- Baseline: `bench/reading_order.py`, `bench/reading_order_truth.json`
- Current pipeline costs and layout findings: `bench/ERGEBNIS.md`
