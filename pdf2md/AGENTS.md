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

## Page cases

A page case is a page the user marked as wrong in the review view, kept with
the page block Stage 2 produced and the one the user expects (terms:
`CONTEXT.md`). Cases live in the vault under `<preview folder>/.cases/`,
hold page text, and stay out of the repo.

Not yet available: `make check-cases` and the `pdf2md case` commands arrive
with #139, which removes this note. Until then there are no cases to replay;
a Stage-2 assembly fix names its target pages by id and how it measured them.

For a Stage-2 assembly fix:

1. Before changing code, run `make check-cases` (`ISSUE=<n>` when the issue
   names cases; needs `VAULT_ROOT`) and note the open cases.
2. The fix is done when its page cases replay as matching and no fixed case
   differs. Synthetic fixtures in `pdf2md/test` are welcome beside them.
3. The PR states the open cases before → after and which ones now match.
   Issues and PRs name cases by id (`<stem>/pNNN`).

Cases with fault stage `upstream` (recognition, tiling or ordering) are
counted but not replayed; a fix there names its measurement per the root
`AGENTS.md`.
