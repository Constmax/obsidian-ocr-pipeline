# Spike #95: Docling against the current PDF-to-Markdown pipeline

Status: **inconclusive** (valid outcome per the issue). This spike built the
measurement harness, verified it on synthetic pages, and recorded why the
vault measurement it was designed for has not run yet. It did not change any
production code path.

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

## Structure findings (qualitative)

None recorded against vault pages yet — deliberately. The issue requires
structure failures with page references (t01–t16) marked as qualitative
until #20 provides a hand-checked corpus. Inventing page-referenced failures
from synthetic pages would be fabrication, so this section records what to
inspect on the vault run instead:

- Tables: does Docling emit real Markdown tables where the current path
  concatenates cells in reading direction (cf. `bench/ERGEBNIS.md`
  Nachtrag 3)? Record page id, expected rows/columns, observed failure.
- Footnotes: footnote apparatus placement per column vs. interleaved or
  dropped definitions; record page id and footnote numbers.
- Diagrams / boxed notes: image fallback vs. text destruction
  (cf. Nachträge 4–7); record page id and which of the three diagram
  signals (if any) the output corresponds to.

## Verdict: inconclusive

The spike harness exists, is tested, and is ready for the vault run, but the
vault comparison it was built for has not been measured: Docling is not
installed in this environment and no t01–t16 pages were scored. Per the
issue, an inconclusive result is a valid outcome and is recorded as one.
The spike is not extended to make Docling win.

Decision criteria for the future vault run (unchanged from the issue):

- Docling better on reading order **and** not slower per page → open a
  migration issue and re-scope the layout/assembly parts of #93 to the work
  that would actually remain.
- Docling comparable or worse on either axis → record the measurement and
  observed failure modes, keep the current pipeline, proceed with #93.
- Issues that would extend `layout.py` or `assembly.py` should wait for that
  vault verdict. #90, #91, #92 proceed regardless.

## Limitations

- Docling install and model download were not exercised here; first-run
  download size, cold-start time, and peak RSS on 8 GiB remain unmeasured.
- The `pdf2md` synthetic timing (0.1 s/page) covers vector textlayer pages
  only, not the 15–60 s/page scanned VLM path.
- `compare` without `--truth-lines` cannot score ordering; the guidance is
  then inconclusive by construction.
- Structure assessment awaits both the vault run and #20's corpus.
- Docling defaults may drift upstream; the spike pins the intent
  ("documented local/ARM64 defaults, standard pipeline, no tuning"), not a
  version. Record the installed version with any future measurement
  (`check` prints it).

## Completion criteria status

- [x] Docling runs locally on the target machine (2.129.0 in `~/.venvs/docling`,
  `check` reports runnable; synthetic conversion verified end to end).
- [ ] Both paths measured over the same vault pages with
  `bench/reading_order.py` — pending vault run (harness ready).
- [ ] Per-page time and peak memory for both paths on the 8 GiB machine —
  pending vault run (synthetic pdf2md vector timing recorded above only as
  plumbing proof).
- [ ] Structure failures with page references, marked qualitative — none
  invented; inspection list recorded for the vault run.
- [x] Written verdict in `docs/` with limitations stated (this file):
  **inconclusive**.

## References

- Docling: https://github.com/docling-project/docling
- PP-StructureV3 (alternative candidate, not in scope):
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md
- Baseline: `bench/reading_order.py`, `bench/reading_order_truth.json`
- Current pipeline costs and layout findings: `bench/ERGEBNIS.md`
