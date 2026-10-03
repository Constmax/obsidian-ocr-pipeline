# Plugin Architecture: Thin Client over the Installed CLIs

This document records the architecture decision for the Obsidian plugin
(`plugin/`), what it does today, and what is still missing. It does **not**
track bugs or the work order: open work lives in the
[GitHub issues](https://github.com/Constmax/obsidian-ocr-pipeline/issues) (plugin
work: #28, #54; Stage 1 engine: #74 and #133; Stage 2 quality: #12–#17).
Earlier versions of this file carried a bug table and an implementation order;
both went stale within weeks and were removed.

## What the Plugin Does

A user places a scanned PDF (or a page image) into the vault, right-clicks
"OCR → Markdown", and gets a readable `.md` in the preview folder — with a
backlink to the source. No terminal, no venv, no flags. Details of the views
and commands: [review-view.md](review-view.md).

- **Convert:** file menu on a PDF or image and the command **Convert PDF and
  open in OCR comparison** spawn `pdf2md`, with optional page selection,
  a progress notice fed by the `--progress` events (page n of m, derailed
  pages so far, rough time left) that can be hidden while the status bar
  keeps showing the run, cancellation (process group,
  `SIGTERM`, then `SIGKILL`) and an inactivity timeout.
- **Review:** three-column comparison of source and Markdown with
  Accept / Reject, notes, editing and Undo.
- **Searchable copy:** **Create searchable copy (OCR)** runs
  `reprocess-raw --output` (Stage 1) and writes `<stem>-ocr.pdf` beside the
  source; design record in [stage1-ui.md](stage1-ui.md).
- **Contract:** everything the plugin reads from the CLIs is pinned in
  [cli-contract.md](cli-contract.md) and tested from both sides.

## The Core Architectural Question

Obsidian plugins are TypeScript running in Electron. This pipeline is Bash +
Python + MLX + Ghostscript + Tesseract. **This cannot be bundled directly.**
Three approaches:

### A · Thin Client via Local Installation (chosen)

The plugin executes the installed CLIs via `child_process.spawn` and parses
their output. The pipeline remains the exact code in this repository.

- **Pros:** No re-implementation. Every pipeline fix benefits the plugin.
- **Cons:** Desktop only (`child_process` does not exist on mobile). The user
  must run `./setup.sh` beforehand. A plugin relying on external binaries would
  need clear labeling for the Community Store; this one is private.
- **Interface:** Stage 2 emits versioned JSON progress events, exit codes and a
  `--check` preflight; Stage 1 still prints human text, and the plugin reads two
  pinned message lines from it (see [cli-contract.md](cli-contract.md)).

### B · Sidecar Daemon

A lightweight local HTTP server (Python, out of `pdf2md/`) started by the
plugin and served via `fetch`.

- **Pros:** The model stays loaded between jobs — the load time is paid once.
  Clean progress via Server-Sent Events. Precursor to "runs on Mac, controlled
  from iPad".
- **Cons:** Process lifecycle, port conflicts, zombie processes on Obsidian
  crashes. Significantly more code for marginal gains.

### C · Re-implementation in TypeScript/WASM

PaddleOCR-VL over MLX does not exist in WASM, and Tesseract.js is noticeably
worse than native Tesseract. The measured results in `bench/ERGEBNIS.md`
would be void. Non-viable.

**Decision:** A. Revisit B once batch processing across many files becomes the
primary usage pattern.

## What Is Still Missing

- **Settings** (#28): an adjustable `pdf2md` path, DPI and `--tile-from`, and
  a button that shows the `pdf2md --check` result. Today the CLIs are found on
  `PATH` plus `~/bin` and the Homebrew folders (`resolveCli()`,
  `stage1Path()`).
- **Batch conversion over the whole holdings** (#21).
- **PaddleOCR in the engine setting** (#73), after the Stage-1 benchmark
  decision (#71). Issue #133 proposes dropping OCRmyPDF altogether; that would
  change the Stage-1 engine model described in
  [paddle-textlayer.md](paddle-textlayer.md).
- **Sidecar daemon and mobile support**: only after the above.

## Non-Goals

- No cloud OCR. Course materials remain local on the machine.
- No automatic overwriting of wiki pages. The plugin generates preview files;
  migration into the wiki remains a deliberate human action.
- No expectation of error-free output. The backlink to the original PDF is a
  core architectural feature, not a fallback.

## Not Built on Purpose

**An LLM repair pass** is on hold. At 98.2 % word accuracy across the 40
benchmark pages (measured against commit `ddf69e9`), the gain does not justify
the risk of "improving" a correct statutory citation. If ever implemented, the
benchmark evaluates it, with the bar set at **92.0 % citation accuracy**.

**Document-internal cross-checking** of suspicious words (a confused variant
that appears often on a page while the suspicious word appears once) is not
built. The dictionary check (`pdf2md/dictionary.py`, see
[scripts-detail.md](scripts-detail.md)) accepts morphologically well-formed
pseudo-words like `Verhaltungsakte`, which marks the boundary of the current
approach.

**Migration:** 701 `[[raw/…pdf]]` wikilinks still point to raw PDFs. The final
location of the original files is undecided; without them, Markdown files are
not fully reliable for OCR pages.
