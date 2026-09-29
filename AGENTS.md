# AGENTS.md

OCR-Pipeline for scanned legal study materials. Three independent stages in one
repo. All code identifiers, comments, docs and commit messages are in English. Match that style.

## Layout

- `bin/` — Stage 1: searchable PDFs via ocrmypdf. Shared lib `pdf-lib.sh` +
  four CLIs (`pdf-auto`, `pdf-combine`, `pdf-workflow`, `reprocess-raw`) +
  Python helper `column_tools.py` (column split/merge, needs pikepdf).
  The merge → MediaBox fix → downscale → split → OCR/quality gate →
  re-merge → publish sequence exists once, `run_pdf_pipeline` in
  `pdf-lib.sh`; the first three CLIs only find and group inputs. Change the
  sequence there, and pin it in `bin/test/test_pipeline.py`.
- `pdf2md/` — Stage 2: `pdf2md.py` (MLX/PaddleOCR-VL) PDF or page image →
  Markdown (images are normalized to a one-page PDF in `open_document()`).
  Apple-Silicon-only, ~15–60 s/page; needs `pymupdf`. `dictionary.py` (formerly `woerterbuch.py`) runs a
  dictionary pass over OCR pages afterwards (reports by default, corrects only
  unambiguous cases with `--dictionary-correct`).
- `plugin/` — Stage 3: Obsidian review view (TypeScript, esbuild, no React).
- `ocrmypdf_paddle/` — OCRmyPDF engine plugin running PaddleOCR PP-OCRv5
  through RapidOCR (plan `docs/paddle-textlayer.md`). Reading order comes
  from line geometry in `ordering.py`, measured against the hand-checked
  truth set of `bench/reading_order.py`. `--paddle-mode fast` (macOS) takes
  Apple Vision's lines and re-reads only citation lines with PP-OCRv5
  (`apple.py`; extra `[fast]` for pyobjc-framework-Vision). Not installed by
  `setup.sh` until the benchmark retains it; tests need the pinned ocrmypdf
  but no RapidOCR, Vision or models (`python3 -m pytest ocrmypdf_paddle/test`).
- `bench/` — benchmark harness; page images are copyrighted scans, NOT in the
  repo, reproducible via `bench/build_bench.py` from the user's vault. Finished
  experiments live in `bench/archive/` (not supported, not smoke-tested).
- `contracts/` — the CLI contract with the plugin (progress events, exit
  codes, Stage-1 message lines, input formats, preview format version; see
  `docs/cli-contract.md`). Python and TypeScript tests both read it: change
  the contract and both sides in one PR.
- `skill/SKILL.md` — Claude skill for vault usage; contains hard-earned
  Stage-1 quirks (`pdftotext -raw` for split-merged pages, leptonica rewrites
  `/tmp` paths on macOS). Read it before touching `bin/`.

## Pinned toolchain (do not bump casually)

- ocrmypdf pinned `17.8.0` in `setup.sh`: `bin/` scripts use the old CLI flag
  `--engine apple|tesseract|auto`. ocrmypdf ≥17.10 renamed it to `--ocr-engine`
  and would break every script. Upgrade path: migrate scripts, then unpin.
- ocrmypdf-appleocr pinned `0.3.4` (≥0.4.0 self-registers via entry point,
  colliding with the `--plugin` check in `install.sh`).

## Plugin development (`plugin/`)

- Commands: `npm run check` (tsc --noEmit), `npm run lint`, `npm test`,
  `npm run build` (= check + esbuild), `npm run dev` (watch; copies artifacts
  to `$OBSIDIAN_PLUGIN_DIR` if set).
- Tests run under plain `node --test --experimental-strip-types
  test/*.test.ts` — no jest/vitest; needs Node ≥22.6. They import `src/`
  modules directly, using `.ts` extension imports (`allowImportingTsExtensions`
  in tsconfig). `sync.test.ts` shims `window`/rAF; anything touching
  `window.pdfjsLib`, `MarkdownRenderer` or real DOM is untestable headless —
  verify via an Obsidian smoke test.
- **`main.js` is committed** so a clone runs without Node. After changing
  `src/`, run `npm run build` and commit `main.js` too — CI verifies the
  committed build against `src/` (`.github/workflows/ci.yml`).
- Install into a vault: `VAULT_ROOT=<path> plugin/install-plugin.sh --enable` (plugin id from `manifest.json` only; default
  copies, no build; `--build` to build, `--symlink` only outside iCloud).
- ESLint: `eslint-plugin-obsidianmd`; the `sentence-case` rule is enabled for `src/settings.ts` only (older UI strings elsewhere still violate it); `no-console` allows only `error`/`warn`.
- **Obsidian API Invariants & Quirks**:
  - **No `open()` on Views**: Never define a custom method named `open()` on classes extending `ItemView` / `View` (collides with Obsidian's internal `View.prototype.open(containerEl)` lifecycle). Use `openPreview()`.
  - **`loadPdfJs()` caching**: Obsidian's `loadPdfJs()` returns `Promise<any>` and may not attach to `window.pdfjsLib` automatically. Always use `const pdfjs = window.pdfjsLib ?? await loadPdfJs(); (window as any).pdfjsLib = pdfjs;`.
  - **Idempotent UI Initialization**: Initialize view panes via `ensureUiBuilt()` before any `openPreview()`, `update()`, `setState()`, or `onOpen()` calls so that leaf restorations and conversions never encounter uninitialized subcomponents.
  - **Startup Indexing**: Reconcile cache and open initial previews within `app.workspace.onLayoutReady(...)` to avoid reading incomplete vault metadata on startup.
  - **Bug Fixes**: Always follow the systematic debugging workflow (`systematic-debugging` skill) to trace root causes before modifying code.

## Setup / environments

- `./setup.sh` at repo root is the one installation path (idempotent): brew
  bundle, venvs under `~/.venvs/` (`ocrmypdf`, `mlxocr`; uv Python 3.12 —
  Homebrew-Python's pyexpat is broken on macOS), `~/bin` symlinks, plugin
  copy. `install.sh` and `plugin/install-plugin.sh` are building blocks it
  calls, not parallel installers.
- venv convention is named exactly there: `VENV_ROOT="${VENV_ROOT:-$HOME/.venvs}"`
  (overridable); `setup.sh`, `install.sh`, `bin/pdf2md`, `bin/pdf-lib.sh` and
  `bin/reprocess-raw.sh` derive their candidate paths from it. The old
  vault-local `pdf2md/setup.sh` (venvs `.venv-mlxocr` / `.venv-paddleocr` in
  the vault) is deleted — history in git, Gate-1 measurements in
  `bench/ERGEBNIS.md`.
- **`make check`** runs everything CI runs; **`make test-fast`** runs only
  the unit tests (`pytest -m "not slow"` + plugin tests, a few seconds). Mark
  a new test `@pytest.mark.slow` when it runs a CLI or pipeline end to end.
  CI (`.github/workflows/ci.yml`, every PR and push to `main`) calls the same
  targets, one per job: `plugin` (npm ci → check → lint → test → build →
  `main.js` is versioned *and* identical to `src/`), `shellcheck` (tracked
  scripts), `test-py` (`pytest pdf2md/test bin/test` — `bin/test` holds Stage-1
  behavioral tests with stubbed tools — plus the bench entry-point smoke test)
  and `test-ocrmypdf` (pinned ocrmypdf: hOCR text-layer order,
  `ocrmypdf_paddle/test`; locally via `~/.venvs/ocrmypdf` once pytest is installed there, otherwise skipped).

## Docs

`README.md` is the German entry point; `docs/` and this file are English (the
frontmatter and progress-event keys stay German — they are contract, see
`docs/preview-format.md`). `bench/ERGEBNIS.md` is a German, append-only
measurement log.

Reference (describes what the code does now — update it in the same PR as the
code):

- `docs/scripts-detail.md` — flag reference for Stages 1 and 2
- `docs/cli-contract.md`, `contracts/` — what the plugin reads from the CLIs
- `docs/preview-format.md` — normative Markdown preview format
- `docs/review-view.md` — the plugin's views, commands, settings, folder model
- `docs/installation.md` — setup troubleshooting and which venv holds what
- `docs/ocr-preview.md`, `docs/vault-integration.md`, `docs/log-und-git.md`,
  `skill/SKILL.md` — vault-side conventions for the Claude skill (the vault has
  its own, diverged copy under `.claude/skills/pdf-jura-workflow/`)

Design records (the reasoning behind decisions; status header at the top says
what is done):

- `docs/plugin-roadmap.md` — plugin architecture decision (thin client)
- `docs/stage1-ui.md` — safe searchable-copy action (implemented)
- `docs/paddle-textlayer.md` — PaddleOCR engine plan, decisions and results
- `docs/BUGREPORT-2026-07-06-split-merge.md` — why the B5 gate exists

Open bugs and the work order live in GitHub issues, not in the docs. Flags in
docs use the English option names; the German aliases still work.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `Constmax/obsidian-ocr-pipeline` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/` (created lazily). See `docs/agents/domain.md`.
