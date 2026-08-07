# AGENTS.md

OCR-Pipeline for scanned legal study materials. Three independent stages in one
repo. All code identifiers, comments, docs and commit messages are German
(commits use ASCII transliteration: `fuer`, `ergaenzen`). Match that style.

## Layout

- `bin/` — Stage 1: searchable PDFs via ocrmypdf. Shared lib `pdf-lib.sh` +
  four CLIs (`pdf-auto`, `pdf-combine`, `pdf-workflow`, `reprocess-raw`) +
  Python helper `column_tools.py` (column split/merge, needs pikepdf).
- `pdf2md/` — Stage 2: `pdf2md.py` (MLX/PaddleOCR-VL) PDF → Markdown.
  Apple-Silicon-only, ~15–60 s/page; needs `pymupdf`.
- `plugin/` — Stage 3: Obsidian review view (TypeScript, esbuild, no React).
- `bench/` — benchmark harness; page images are copyrighted scans, NOT in the
  repo, reproducible via `bench/build_bench.py` from the user's vault.
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
- Install into a vault: `VAULT_ROOT=<path> plugin/install-plugin.sh` (default
  copies, no build; `--build` to build, `--symlink` only outside iCloud).
- ESLint: `eslint-plugin-obsidianmd`; `sentence-case` rule is disabled because
  the UI is German; `no-console` allows only `error`/`warn`.

## Setup / environments

- `./setup.sh` at repo root is the one installation path (idempotent): brew
  bundle, venvs under `~/.venvs/` (`ocrmypdf`, `mlxocr`; uv Python 3.12 —
  Homebrew-Python's pyexpat is broken on macOS), `~/bin` symlinks, plugin
  copy. `install.sh` and `plugin/install-plugin.sh` are building blocks it
  calls, not parallel installers.
- The venv paths are hardcoded as `$HOME/.venvs/...` in every consumer
  (`setup.sh`, `install.sh`, `bin/pdf2md`, `bin/pdf-lib.sh`,
  `bin/reprocess-raw.sh`) — there is no `VENV_ROOT` override. Moving the
  venvs means editing all five; `bin/pdf2md` otherwise aborts with
  "MLX-venv fehlt".
- `pdf2md/setup.sh` is the superseded vault-local installer (venvs
  `.venv-mlxocr` / `.venv-paddleocr` inside the vault, `rm -rf` on those
  paths). It is still tracked but is NOT the installation path — use the
  root `./setup.sh`. Gate-1 measurements in `bench/ERGEBNIS.md`.
- CI (`.github/workflows/ci.yml`, bei jedem PR und bei Push auf `main` —
  Feature-Branches laufen über ihren PR, sonst startet jeder Job doppelt):
  Job `plugin` (npm ci → check → lint → test → build → `main.js` ist
  versioniert *und* deckungsgleich mit `src/`), Job `shell` (shellcheck über
  alle zehn Skripte, shellcheck-Version gepinnt) und Job `python` (`pytest
  pdf2md/test`: Nahtentdopplung, Randmarken, Schleifen, Golden-Snapshot).
  Lokal: plugin mit lint → check → test → build; Stage-1-Skripte mit
  `shellcheck -x -P bin`; Python mit `python3 -m pytest pdf2md/test`.

## Docs

`docs/` is German: `scripts-detail.md` (flag reference), `installation.md`,
`review-ansicht.md`, `plugin-roadmap.md` (architecture decision: the plugin
spawns the installed CLIs as a thin client — pipeline code is not bundled).
