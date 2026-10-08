# bin/AGENTS.md

Stage 1: searchable PDFs via ocrmypdf. Before changing anything here, read
`skill/SKILL.md`: it holds hard-earned quirks (`pdftotext -raw` for
split-merged pages, leptonica rewriting `/tmp` paths on macOS).

## Plugin path

The plugin's "Add OCR text layer" runs `reprocess-raw --in-place
--keep-original <path>` (`plugin/src/conversion.ts`, `addTextLayer`) with the
engine the user chose (`plugin/src/ocr-settings.ts`). `--in-place` passes
`pdf-combine --text-only`: no `fix_mediabox`, no `gs_downscale`, no rotation or deskew,
`--optimize 0`, no column split (#180). Every reprocess-raw run also passes
`--min-average-chars 1`: its B5 gate checks every page, so the quality
gate's average of 200 no longer fails slide decks (#218). `auto` (the plugin default) uses
PaddleOCR fast when it is ready (#198). A change reaches the user only through
these flags and the engines; the split, `--dpi` and `--force-ocr` paths are
shell-only.

## Pipeline

- `pdf-lib.sh` holds the shared library; `pdf-auto`, `pdf-combine`,
  `pdf-workflow` and `reprocess-raw` only find and group inputs.
- The merge → MediaBox fix → downscale → split → OCR / quality gate →
  re-merge → publish sequence exists once, in `run_pdf_pipeline`. Change the
  sequence there and pin it in `bin/test/test_pipeline.py`.
- `--engine auto|apple|tesseract|paddle` is resolved once by `resolve_engine`
  into `RESOLVED_ENGINE`; OCR arguments and the fallback matrix derive from
  that one value.
- The B5 gate (per-page character floor, `column_tools.py verify-pages`)
  exists because of `docs/BUGREPORT-2026-07-06-split-merge.md`.
- `bin/test` holds behavioral tests with stubbed tools on `PATH`; only
  `test_hocr_text_layer_order.py`, `test_existing_text_layer.py` and
  `test_appleocr_no_boxes.py` (macOS) run the pinned ocrmypdf, and
  `test_downscale.py` the real Ghostscript and `test_mediabox.py` the real
  Ghostscript, qpdf and pdfinfo (slow, skipped without them).

## Pinned toolchain

Upgrade a pin together with the migration it needs:

- **ocrmypdf `17.8.0`** (`setup.sh`): `bin/` selects engines by plugin
  (`--plugin ocrmypdf_appleocr` / `ocrmypdf_paddle`) and leaves ocrmypdf's own
  `--ocr-engine` at `auto`, which is how both plugins activate
  (`ocrmypdf_paddle._selected`). Verify that mechanism on the new version.
- **ocrmypdf-appleocr `0.3.4`**: from 0.4.0 it self-registers via entry
  point, which collides with the `--plugin` check in `install.sh`.
  `appleocr_no_boxes.py` rebinds its `generate_pdf` to drop the red line
  boxes (#209); `test_appleocr_no_boxes.py` checks that on the new version.
