# pdf2md/AGENTS.md

Stage 2: PDF or page image → preview. `pdf2md.py` is the CLI,
`conversion.py` runs a document page by page, `layout.py` finds columns and
boxes, `ocr.py` tiles pages and runs the model, `assembly.py` turns recognized
lines into page blocks, `page_cache.py` keeps each page's recognized lines.
Page images are normalized to a one-page PDF in `open_document()`.
`dictionary.py` checks OCR pages afterwards (reports by default, corrects
only unambiguous cases with `--dictionary-correct`).

The model (MLX, PaddleOCR-VL) runs on Apple Silicon only, about 15–60 s per
page. Pages with a usable text layer skip the model and go straight to
assembly, so most assembly work can be reproduced on vector pages without it.

## Plugin path

The plugin's "OCR → Markdown" runs `bin/pdf2md <pdf> --out <folder>
--fortschritt [--seiten …]` (`plugin/src/conversion.ts`, `convertPdf`) and
reads the preview through `plugin/src/preview-parser.ts`. The preview format
(`docs/preview-format.md`) and the progress events (`contracts/`) are
contract: change the producer and the plugin in one PR.

## Tests

- `pdf2md/test` runs without MLX and without the vault; model execution is
  replaced through the `OcrAdapter` protocol in `conversion.py`.
- `test_snapshot.py` compares pure assembly functions with a golden
  recording (`test/data/snapshot.json`). An intended assembly change
  regenerates it with `python3 pdf2md/test/generate_snapshot.py`; the PR
  names each changed entry and why it changed.
