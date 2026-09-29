# bench/AGENTS.md

Benchmark harness for both stages, and the measurement side of Stage-1
reading order. Supported entry points, what each needs and where it writes:
`bench/README.md`. Scripts in `bench/archive/` are records of finished
experiments, not tools.

## Source pages

Page images and source PDFs are copyrighted scans from the user's vault and
stay out of the repo. Commands resolve them through `VAULT_ROOT`; truth files
hold source pages, hand-drawn regions and fingerprints, never page text.

## Reading order (`ocrmypdf_paddle/`)

- `ocrmypdf_paddle/` is an OCRmyPDF engine plugin: PaddleOCR PP-OCRv5 through
  RapidOCR, or with `--paddle-mode fast` Apple Vision's lines with citation
  lines re-read by PP-OCRv5 (`apple.py`, extra `[fast]`). Its reading order
  comes from line geometry in `ordering.py` (`order_lines`). Plan, decisions
  and results: `docs/paddle-textlayer.md`.
- `ordering.py` is measured against the hand-checked truth set of
  `bench/reading_order.py` (`reading_order_truth.json`) and the validation
  sets `reading_order_holdout*.json`.
- **Holdout discipline.** A validation set is spent once you tune on it.
  Validate a new ordering fix on a new set of unseen pages, and record the
  result as the next Nachtrag in `bench/ERGEBNIS.md`.
- `ocrmypdf_paddle/test` needs the pinned ocrmypdf but no RapidOCR, Vision or
  models (`python3 -m pytest ocrmypdf_paddle/test`); without ocrmypdf it is
  skipped unless `REQUIRE_OCRMYPDF=1` (set in CI).
- `setup.sh` does not install the Paddle engine; the retain/discard benchmark
  (#71) decides that. The plugin offers it only where
  `reprocess-raw --check-engine --engine paddle` finds it usable.

## Measurement log

`bench/ERGEBNIS.md` is German and append-only: each measurement is a new
`## Nachtrag <date> (<n>): <topic>` with setup, numbers, findings and
limitations. Correct an earlier entry with a new Nachtrag.
