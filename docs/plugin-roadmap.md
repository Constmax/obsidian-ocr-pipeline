# Roadmap: From Script Bundle to Obsidian Plugin

This document records what a plugin should be, what the current code already
provides for it, and where the real hurdles lie. It is a work-in-progress status, not a promise.

## What the Plugin Should Do

A user places a scanned PDF into the vault, right-clicks in the context menu on
"OCR → Markdown", and gets a readable, searchable `.md` alongside — with a
backlink to the original PDF. No terminal, no venv, no guessing flags.

Realistic v1 feature scope:

- Context menu entry on PDF files in File Explorer
- Progress indicator (page n of m) — mandatory when running at 15–60 s/page
- Output as `.md` in a configurable destination folder
- Settings: Engine, DPI, output folder, path to local installation
- Cancel button that cleanly kills the child process

## The Core Architectural Question

Obsidian plugins are TypeScript running in Electron. This pipeline is Bash + Python +
MLX + Ghostscript + Tesseract. **This cannot be bundled directly.** Three approaches:

### A · Thin Client via Local Installation (Recommended for v1)

The plugin executes the installed CLIs via `child_process.spawn` and parses
their stdout for progress. The pipeline remains the exact code in this repository.

- **Pros:** Immediately actionable. No re-implementation needed. All pipeline bugfixes automatically benefit the plugin.
- **Cons:** Desktop only (`child_process` does not exist on mobile). The user must run `./setup.sh` beforehand. The Obsidian Community Store only accepts plugins relying on external binaries with clear labeling — for a private plugin, this is irrelevant.
- **Required work on this repo:** Scripts must emit machine-readable progress output (`--json` flag or a line like `PROGRESS 7/20` on stderr). Currently output is formatted for human reading (emojis, German sentences). This is the most concrete pending task.

### B · Sidecar Daemon

A lightweight local HTTP server (Python, out of `pdf2md/`) started by the plugin
and served via `fetch`.

- **Pros:** Model remains loaded between jobs — the 1.6s load time is paid only once. Clean progress reporting via Server-Sent Events (SSE). Precursor to "runs on Mac, controlled from iPad".
- **Cons:** Process lifecycle management, port conflicts, zombie processes on Obsidian crashes. Significantly more code for marginal gains in v1.

### C · Re-implementation in TypeScript/WASM

- **Cons:** PaddleOCR-VL over MLX does not exist in WASM, and Tesseract.js is noticeably worse than native Tesseract binaries. Measured results in `bench/ERGEBNIS.md` would be voided. Non-viable path.

**Decision:** A for v1, B as an option once batch processing across many files becomes the primary usage pattern.

## What is Already Plugin-Ready

- `pdf2md.py` already writes frontmatter containing `seiten-textlayer` / `seiten-ocr` / `seiten-diagramm` and a `Quelle:` link — exactly the metadata model that a plugin UI would display (see [ocr-vorschau.md](ocr-vorschau.md)).
- `--out <folder>` already exists, making the target folder configurable.
- The separation "Preview Folder ≠ Wiki" is already designed and documented.
- Diagram pages are output as image + collapsed callout — native Obsidian syntax, no custom rendering needed.

## What is Missing

| Task | Rationale | Effort |
|---|---|---|
| Machine-readable progress from scripts | Required for progress bar UI | Small |
| Clean exit code per error class | Plugin must distinguish "missing dependency" from "OCR failed" | Small |
| Preflight check as standalone command | Plugin Settings needs "Installation OK?" validation | Small |
| Cancellability (SIGTERM handling, temp cleanup) | 30-minute runs must be cancellable cleanly | Medium |
| Reassembly layer stabilization | Most recent component: divider lines, reading order, spaced text | Large |
| Footer detection on tile splits | Full-width elements get truncated at tile splits | Medium |

The first three items take an afternoon combined and turn the repo into a plugin-ready interface. The major task is the reassembly layer — which determines perceived output quality far more than the OCR engine itself.

## Known Issues

| | Issue | Weight |
|---|---|---|
| ~~1~~ | ~~**Derailed pages** — 15% run into infinite loops or abort~~ | **Resolved**, 6 of 6 caught |
| ~~2~~ | ~~**Reading order** `Klausur_2137` p. 7 (47.5%)~~ | **Resolved**, page now at 96.1% |
| 3 | **Diagram page missing image** — `Strafrecht AT VI` p. 8, missing fallback to page image | Medium, workaround `--diagramm-seiten 8` |
| 4 | **One false negative on two-column** — `Verwaltungsrecht AT Fall 8` p. 10, shallow gutter | Intentionally chosen trade-off (1 of 14) |
| 5 | **Interleaved footnote blocks** in 2131/2135/2143 | Small, accounts for remaining 1–2 char loss |
| 6 | **Footnote text across page break** truncated | Small |
| ~~7~~ | ~~**`**Beispiel:**` mid-sentence**~~ | **Resolved**, both structural variants |
| 8 | **Word errors** — quantified: 1.2% across all 40 pages; since dictionary check at least **discoverable** | Low |
| 9 | **Multi-column reading order** — `2131_Lösung` p. 4 at 49.7% | New, currently largest single issue |

Regarding item 7: Margin labels had **two** structural variants, and only one was previously recognized. Outdented into left margin (Hemmer scripts) → `randlabel_vorziehen()` moves it to block start. As inline prefix to same line → was misclassified as heading and broke the sentence; `ist_ueberschrift()` now excludes it. The `**A.**` portion of the same item was not an error: markers carry their title after them, which is correct Markdown.

Untested in addition: **~140 scan pages with under 50 characters in legacy textlayer**. Unclear whether content is missing or pages are genuinely empty.

## Not Yet Built

**The LLM repair pass** is on hold. At 98.5% word accuracy across all pages, the gain does not justify the risk of "improving" a correct statutory citation. If ever implemented: the benchmark suite now evaluates it, with the bar set at **92.4% citation accuracy** — it must not degrade accuracy below this threshold.

**Local dictionary checking** (hunspell + legal term list) is **built** — serving as the verification aid planned here: `pdf2md/woerterbuch.py` reports issues, but replaces only unambiguous cases and only when explicitly requested. The primary benefit is the review queue (`woerter-verdaechtig` in frontmatter, `⌕` in logs), not automated text rewrites. Remaining open: **document-internal cross-checking**: if a confused variant of a word appears frequently on a page while the suspicious word appears once, that is a contextual clue no static dictionary can provide — dictionary checking also accepts morphologically well-formed pseudo-words like `Verhaltungsakte`, marking the boundary of the current approach.

**Migration.** 701 `[[raw/…pdf]]` wikilinks still point to raw PDFs. Awaiting decision on final location of original files — without them, Markdown files are not fully reliable for OCR pages.

**Minor items:** `pages.json` belongs in `.gitignore` — done in repo (`bench/pages.json`), pending in vault.

## Advanced Ahead of Schedule: Review View (v0.1)

This section documents an intentional deviation from the original sequence. Details on the view itself: [review-view.md](review-view.md).

The sequence below lists the plugin skeleton as **Step 5**. However, a different component with distinct scope was built first:

- **Different scope:** The Review View **is read-only** — no conversion, no `spawn`, no progress modal. It displays Stage 2 output `.md` files side-by-side with original PDFs and moves them via **Accept / Reject** between three folders. None of "What the plugin should do" above is implemented in this view.
- **Why advanced:** The 15% derailed pages (see "Known Issues", #1) forced a manual human review process — comparing PDF and Markdown in split windows by hand. This review workflow lacked a dedicated interface and represents the critical bottleneck for identifying derailments.
- **Architecture:** The view uses **Approach A without the spawn component** — calling Obsidian's built-in PDF.js library (`loadPdfJs`) without child processes. The A/B/C architectural decision remains unchanged.
- **The three interface tasks remain open:** Machine-readable progress, exit codes, preflight checks — none were completed or rendered obsolete by this view. They remain pending for the core plugin implementation (Step 5).

Sole interaction with the pipeline: `pdf2md.py` now writes page provenance into markers (contract: `docs/ocr-vorschau.md`, "Marker Grammar"). Non-breaking change — legacy `%% S. n %%` markers continue to be supported.

## Built Next: Conversion Command (v0.2)

The initial Approach A building block is complete: command **"Convert PDF and open in OCR Review"**. Selects a PDF in vault via Suggest Modal, spawns `~/bin/pdf2md <pdf> --out <preview-folder>` via `child_process.spawn`, and opens Review View upon completion. Feedback delivered via Notice.

Intentionally **omitted** (remaining pending, see "What is Missing"): Progress bar UI, cancel button, machine-readable progress output, preflight check, context menu entry on PDF files, and configurable pdf2md path.

## Implementation Order

1. ~~**Derailment detection**~~ — Complete, 93.3% → 98.5%.
2. **Known Issue 9** — Multi-column reading order (`2131_Lösung` p. 4). Largest remaining single item; gutter logic recently updated.
3. **Known Issue 3** — Diagram fallback to page image. Requires hand-labeled sample set of diagram pages first; otherwise tweaks to `ist_diagramm()` merely shift probabilities.
4. **Make scripts plugin-ready** — Progress output, exit codes, `--check`.
5. **Harden reassembly layer** — Against a benchmark corpus of 20 pages with verified reference text. Harness resides in `bench/`.
6. **Plugin skeleton** — TypeScript, esbuild, context menu, `spawn`, progress modal. `~/Developer/ask-my-notes` serves as reference template. *Review View (v0.1) is built — see "Advanced Ahead of Schedule" above; it does not replace this step.*
7. **Settings Tab** with preflight validation.
8. Only then evaluate Sidecar (Approach B) and Mobile support.

## Non-Goals

- No cloud OCR. Course materials remain local on machine.
- No automatic overwriting of wiki pages. Plugin generates preview files; migration into wiki remains a deliberate human action.
- No expectation of error-free output. Backlink to original PDF is a core architectural feature, not a fallback compromise.

