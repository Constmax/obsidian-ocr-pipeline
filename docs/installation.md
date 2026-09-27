# Installation & Troubleshooting

Setup runs via the one-time setup — `./setup.sh` in repo root
(documented in [README.md](../README.md)). This page is the
troubleshooting guide: what to do if setup fails, and what to do
if something fails afterwards.

## If `setup.sh` Fails at Step X

The sections correspond to the script's execution blocks.

### ① Xcode CLT / Homebrew

- "Xcode CLT missing …" → Run `xcode-select --install`, confirm the
  macOS dialog, then re-run `./setup.sh`.
- "Homebrew missing …" → The script installs Homebrew itself (official
  installer script). The `eval` only affects the running process —
  `setup.sh` therefore appends `brew shellenv` to `~/.zprofile`.
  Check: `command -v brew` in a **new** terminal.
- If installation fails: Check the installer script's error output
  (usually network issues or missing Xcode CLT, see above).

### ② `brew bundle` Fails

- Retry manually: `brew bundle --file=Brewfile` — the detailed error message will appear there.
- `ocrmypdf` is intentionally **not** in the Brewfile (pyexpat issue, see below) —
  do not install via brew; it is installed in step ③ via venv.

### ③ uv / Python 3.12 / pyexpat

- "no Python 3.12 found (uv python install 3.12)" → Offline? Run manually:
  `uv python install 3.12` — stderr shows the cause.
- "Python … has a broken pyexpat" → Use the script's recommended workaround:
  ```bash
  uv python uninstall 3.12 && uv python install --force 3.12
  ```
  then re-run `./setup.sh`.

### ④ PATH (~/bin)

- "added to ~/.zshrc" → `~/bin` is in PATH in **new** terminals; already open
  terminals do not reflect the change.
- `command not found: pdf-auto` (after setup) → Open a new terminal
  (`.zshrc`/`.zprofile` are only read at shell startup).

### ⑤ `~/bin` Symlinks

- Existing symlinks pointing elsewhere are backed up to
  `.setup-bin-backup-<timestamp>.txt` in the repo before being replaced. Restore via
  `ln -sfn <target> ~/bin/<name>`.
- "MISSING pdf-auto …" in verification → Check step ④.

### ⑥ MLX (Stage 2) on Intel Macs

- Not an error: Stage 2 requires Apple Silicon. Setup issues a warning ("remains open")
  and exits 0; Stage 1 + Plugin remain fully functional.

### ⑦ Vault without `.obsidian/`

- "'…' has no .obsidian/" → Plugin step is skipped; Stage 1+2 are
  installed. Start Obsidian once (creates `.obsidian/`), then re-run
  `./setup.sh`.

## Which Environment Holds Which Engine

All venvs live under `$VENV_ROOT` (default `~/.venvs`, see `setup.sh`). Each
OCR engine runs from exactly one of them:

| Environment | Created by | Engine | Packages | Used by |
|---|---|---|---|---|
| `~/.venvs/ocrmypdf` (Python 3.12 via uv) | `setup.sh` ③ | Stage 1: Tesseract and Apple Vision through OCRmyPDF | `ocrmypdf==17.8.0`, `ocrmypdf-appleocr==0.3.4` | `pdf-auto`, `pdf-combine`, `pdf-workflow`, `reprocess-raw` (via `~/bin/ocrmypdf`) |
| `~/.venvs/mlxocr` (Python 3.12 via uv) | `setup.sh` ⑥, Apple Silicon only | Stage 2: PaddleOCR-VL 1.5 through MLX | `pdf2md/requirements.txt` (`mlx-vlm`, `pymupdf`, `pikepdf`, `pillow`, `numpy`) | `pdf2md` wrapper, plugin conversion |
| your own venv (Python ≥ 3.12) | by hand, **not** `setup.sh` | Stage 1 candidate: PaddleOCR PP-OCRv5 through RapidOCR/ONNX Runtime | `pip install ./ocrmypdf_paddle` (pins `ocrmypdf==17.8.0`, `rapidocr==3.9.2`, `onnxruntime==1.26.0`) | benchmarks only, until [paddle-textlayer.md](paddle-textlayer.md) retains the engine |

Tesseract itself comes from Homebrew (`tesseract-lang` in the `Brewfile`); the
Apple Vision engine needs macOS. Keep the Paddle engine out of
`~/.venvs/ocrmypdf`, so that a failed Paddle install cannot break the
Stage-1 engines in daily use.

For development, `make check` also needs Node ≥ 22 and `shellcheck`
(`brew install node shellcheck`); neither is part of the `Brewfile`, because
using the pipeline needs neither.

## Python 3.12 expat Bug

Homebrew builds of ocrmypdf sometimes link against a Python with broken
`pyexpat` on macOS. Affected: Brew Python 3.12 and newer (e.g. 3.14). Symptoms:

```
ImportError: No module named expat
Symbol not found: _XML_SetAllocTrackerActivationThreshold
```

Because of this, `setup.sh` never installs ocrmypdf via brew, but inside a
dedicated venv (`~/.venvs/ocrmypdf`) with Python 3.12 from **uv**
(python-build-standalone) — which bundles expat itself.

Check if system Python is affected:

```bash
~/.venvs/ocrmypdf/bin/python -c "import pyexpat" || echo "pyexpat broken"
```

Fix: Reinstall uv Python and re-run setup:

```bash
uv python uninstall 3.12 && uv python install --force 3.12
./setup.sh
```

## Plugin (Stage 3)

```bash
VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh --enable
```

- Copies `main.js`, `manifest.json`, and `styles.css` to
  `$VAULT_ROOT/.obsidian/plugins/<id>/`, with the id taken from
  `plugin/manifest.json` (`plugin/install-plugin.sh --print-id` shows it).
  The committed `main.js`
  is default — no Node needed; only `--build` (npm, dev machine)
  requires node/npm.
- Copying is default: when vault lives in iCloud Drive, symlinks can lose
  files. `--symlink` remains a dev opt-in (local only, never iCloud).
- `--enable` also enables the plugin in `community-plugins.json` and disables
  the pre-rename id `ocr-vorschau`. Without it: Settings → Community Plugins →
  Enable "OCR Preview". Reload once (`Cmd+R`) either way.
- Coming from the pre-rename install (`plugins/ocr-vorschau/`): on its first
  start the plugin takes over that install's `data.json`, or — without one —
  keeps an existing `_ocr-vorschau/` folder. Delete `plugins/ocr-vorschau/`
  afterwards.
- Usage: [review-view.md](review-view.md).

## Verification

```bash
# Test main tools
ocrmypdf --version
tesseract --list-langs    # must contain 'deu'
gs --version
sort -V /dev/null && echo "natural sort OK"

# Apple Vision check
ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1 && echo "Apple Vision OK"
```

## Common Errors

### `command not found: pdf-auto`

PATH does not include `~/bin` — open a new terminal (see "④ PATH" above).
Line in `~/.zshrc`:

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### `brew install ocrmypdf` fails with "pyexpat"

Intentional behavior: Do not use brew ocrmypdf — see "Python 3.12 expat Bug".
`setup.sh` uses the venv approach.

### `'pngquant' could not be executed`

Legacy script version with hardcoded `--optimize 3`. Update scripts.

### `zsh: killed` during OCR

RAM kill by macOS OOM killer. Try fixes in this order:
1. `--jobs 1 --dpi 150`
2. `--jobs 1 --dpi 100`
3. Split file (fewer pages processed concurrently)

### Paths with spaces misquoted

Always wrap in quotes:

```bash
pdf-auto "/path/with spaces/folder"
```

or drag folder from Finder into Terminal (auto-escapes spaces).

## iCloud Notes

### Scripts in iCloud?

No — scripts belong in `~/bin/` (local filesystem, not iCloud). Only
input/output PDFs can reside in iCloud.

### Vault synchronized in iCloud

This is normal. Scripts read/write to iCloud paths like any other
path. If "Optimize Mac Storage" is enabled:

```bash
# Download all files in a folder before running script
brctl download "<path>"
```

### Obsidian External Terminal Plugin

Opens terminal at vault root. PATH/Scripts function normally since they live in
`~/bin/`, not inside the vault.

