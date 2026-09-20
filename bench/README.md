# Benchmark tools

The benchmark directory contains reproducible Stage 2 measurements and a set
of historical investigation scripts. Copyrighted page images and source PDFs
are deliberately excluded from the repository.

## Supported entry points

Run these commands from the repository root. Commands that read source PDFs
resolve their paths against `VAULT_ROOT`; when the repository is located
directly inside the vault, the parent directory is detected automatically.

| Command | Purpose | Requires model inference |
| --- | --- | --- |
| `python bench/build_bench.py` | Rebuild the six-page candidate set and `BENCHMARK-SET.md` from the vault | No |
| `python bench/bench_ocr.py --seiten 40` | Measure word accuracy, reading order, and citation fidelity for the current Stage 2 pipeline | Yes |
| `python bench/regress_steg.py` | Compare the current column-gap heuristic with its previous implementation across vector pages | No |
| `python bench/regress_randlabel.py` | Check margin-label promotion across vector pages | No |
| `python bench/regress_randmarke.py` | Check the margin-label heading exception across vector pages | No |
| `python bench/randlabel_debug.py PDF PAGE` | Inspect OCR line geometry around a margin label | Yes |
| `python bench/reading_order.py COMMAND` | Build the hand-checked reading-order truth set and compare Stage-1 workflows on it (`prepare`, `recognize`, `overlay`, `run`, `score`; issue #69) | `recognize` and the Paddle workflows |
| `python bench/spike_docling.py COMMAND` | Measure Docling against the current PDF-to-Markdown pipeline on the same single-page PDFs (`check`, `run`, `compare`; issue #95) | `run --engine docling` and `run --engine pdf2md` on scanned pages |

Example with an explicit vault location:

```bash
VAULT_ROOT=/path/to/vault python bench/build_bench.py
```

`bench_ocr.py` writes its generated documents and metrics below
`bench/bench-lauf/`. `reading_order.py` reads its truth from
`bench/reading_order_truth.json` (source pages and hand-drawn regions, no page
text) and writes page images, recognized lines, overlays and workflow outputs
below `bench/reading-order-lauf/`. `--truth bench/reading_order_holdout.json`
selects the validation pages of issue #87; give them their own `--run-dir`. The regression commands require `bench/pages.json`, which
is produced from the user's vault and is not versioned.

CI imports every supported entry point without loading the ML model or reading
vault assets. This catches renamed or deleted internal modules while keeping
the smoke check cheap.

## Historical investigations

The remaining Python files are retained as records of specific experiments,
including the original PaddleOCR/MLX candidate comparison, derailment tuning,
tiling diagnostics, diagram and grid experiments, the optional layout-model
evaluation, and the Stage-1 RapidOCR runtime spike (`spike_rapidocr.py`, issue
#62). They are not supported command-line interfaces and are excluded
from the CI smoke check. Promote a script to the table above before relying on
it as part of the regular benchmark workflow.

Benchmark results and methodology live in [ERGEBNIS.md](ERGEBNIS.md). The
reproducible six-page source manifest lives in
[BENCHMARK-SET.md](BENCHMARK-SET.md).
