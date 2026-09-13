#!/usr/bin/env bash
# One-time setup for a new laptop: installs everything required by the
# obsidian-ocr-pipeline (Stage 1, 2, 3 + Plugin).
# Idempotent — running multiple times is harmless (even after git pull).
#
# Usage:  ./setup.sh
# Optional: VAULT_ROOT=<path> (Default: repo parent — repo is inside vault)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ROOT="${VAULT_ROOT:-$(dirname "$REPO")}"
# venv convention: all pipeline venvs live under $VENV_ROOT (Default
# ~/.venvs). This is the single canonical location — bin/pdf2md, bin/pdf-lib.sh
# and bin/reprocess-raw.sh derive candidate paths from it.
# Overridable: VENV_ROOT=<path> ./setup.sh
export VENV_ROOT="${VENV_ROOT:-$HOME/.venvs}"
export PATH="$HOME/bin:$PATH"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ok      %s\n' "$*"; }
warn() { printf '\033[33m   ⚠ %s\033[0m\n' "$*"; }

# ─────────────────────────────────────────────────────── ① Preflight
say "Preflight"
[ "$(uname -s)" = "Darwin" ] || { echo "!! Only macOS is supported"; exit 1; }
case "$(uname -m)" in
    arm64) ok "Apple Silicon ($(uname -m))" ;;
    *) warn "Intel Mac detected — Stage 2 (MLX) will likely not work there (remains open)" ;;
esac

if xcode-select -p >/dev/null 2>&1; then
    ok "Xcode Command Line Tools"
else
    echo "   Xcode CLT missing. Please run and confirm the macOS dialog:"
    echo "      xcode-select --install"
    echo "   Then re-run ./setup.sh."
    exit 1
fi

if command -v brew >/dev/null 2>&1; then
    ok "Homebrew ($(brew --version | head -1))"
else
    echo "   Homebrew missing — installing (official installer script) ..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    case "$(uname -m)" in
        arm64) eval "$(/opt/homebrew/bin/brew shellenv)" ;;
        *)     eval "$(/usr/local/bin/brew shellenv)" ;;
    esac
fi
command -v brew >/dev/null 2>&1 || { echo "!! brew not available"; exit 1; }
# The non-interactive Homebrew installer does not write to ~/.zprofile — the
# eval above only takes effect in the current process. Otherwise new terminals
# will lack gs, tesseract, qpdf, pdfinfo, and verification in ⑧ would only see
# them because it runs in the process with eval-PATH.
if grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null; then
    ok "brew shellenv already in ~/.zprofile"
else
    # shellcheck disable=SC2016 # $(...) should stay literal shell command in ~/.zprofile
    printf '\n# obsidian-ocr-pipeline\neval "$(%s/bin/brew shellenv)"\n' "$(brew --prefix)" >> "$HOME/.zprofile"
    echo "   brew shellenv added to ~/.zprofile (new terminal required)"
fi

# ─────────────────────────────────────────────── ② brew bundle
say "System packages (brew bundle)"
brew bundle --file="$REPO/Brewfile"
ok "Brewfile packages"

# ───────────────────────────────────── ③ Stage-1-venv (ocrmypdf)
say "Stage 1 — ocrmypdf-venv"
# Python 3.12 via uv (python-build-standalone): bundles expat itself.
# Homebrew Python bottles have broken pyexpat on macOS
# (Symbol not found: _XML_SetAllocTrackerActivationThreshold).
uv python install 3.12 >/dev/null 2>&1 || true
# sort: fixed order for multiple 3.12 builds (find returns directory order).
# '|| true': if uv dir is missing, find exits with 1 and 'set -e' would abort here,
# instead of reaching the uv fallback below.
PY312="$(find "$HOME/.local/share/uv/python" -path '*/cpython-3.12*/bin/python3.12' 2>/dev/null | sort | head -1 || true)"
[ -n "$PY312" ] || PY312="$(uv python find 3.12 2>/dev/null | head -1 || true)"
[ -n "$PY312" ] || { echo "!! no Python 3.12 found (uv python install 3.12)"; exit 1; }
if ! "$PY312" -c "import pyexpat" >/dev/null 2>&1; then
    echo "!! Python $PY312 has a broken pyexpat (libexpat issue on macOS)."
    echo "   Workaround: uv python uninstall 3.12 && uv python install --force 3.12"
    exit 1
fi

mkvenv() { # $1 = venv path, $2 = Python interpreter
    # An existing venv is only reused if it has pip AND carries the expected
    # Python version: a foreign or old venv (e.g. with Python 3.11 instead of 3.12)
    # would otherwise be silently used and bypass the pyexpat guarantee from step ③.
    local version venv_version
    version="$("$2" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
    if [ -d "$1" ]; then
        if [ ! -x "$1/bin/pip" ]; then
            warn "$1 without pip — recreating"
            rm -rf "$1"
        else
            venv_version="$("$1/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
            if [ "$venv_version" != "$version" ]; then
                warn "$1 with Python $venv_version instead of $version — recreating"
                rm -rf "$1"
            fi
        fi
    fi
    [ -d "$1" ] || uv venv --seed --python "$2" "$1"
}

mkvenv "$VENV_ROOT/ocrmypdf" "$PY312"
# ocrmypdf intentionally pinned to 17.8.0: bin/ scripts use 17.8 CLI
# (--engine auto|apple|tesseract). Newer versions (>=17.10) rename the
# flag to --ocr-engine and register appleocr themselves as plugin (entry point).
# ocrmypdf-appleocr 0.3.4: from 0.4.0 onward the package registers itself via
# entry point and collides with the --plugin check in install.sh.
# Upgrade path: update scripts to new CLI, then unpin.
"$VENV_ROOT/ocrmypdf/bin/pip" install -q -U pip "ocrmypdf==17.8.0" "ocrmypdf-appleocr==0.3.4"
mkdir -p "$HOME/bin"
ln -sfn "$VENV_ROOT/ocrmypdf/bin/ocrmypdf" "$HOME/bin/ocrmypdf"
ok "ocrmypdf + Apple Vision plugin"

# ─────────────────────────────────────────────── ④ PATH (~/bin)
say "PATH (~/bin)"
if grep -q 'HOME/bin' "$HOME/.zshrc" 2>/dev/null; then
    ok "export PATH=\"\$HOME/bin:\$PATH\" already in ~/.zshrc"
else
    cat >> "$HOME/.zshrc" <<'ZEILEN'

# obsidian-ocr-pipeline
export PATH="$HOME/bin:$PATH"
ZEILEN
    echo "   added to ~/.zshrc (new terminal required; this run already uses it)"
fi
# Non-default VENV_ROOT must be in terminal profile, otherwise bin/pdf2md,
# bin/pdf-lib.sh and bin/reprocess-raw.sh will not find the venv in next terminal
# (they would silently fall back to default $HOME/.venvs).
if [ "$VENV_ROOT" != "$HOME/.venvs" ]; then
    if grep -qF "VENV_ROOT=\"$VENV_ROOT\"" "$HOME/.zshrc" 2>/dev/null; then
        ok "export VENV_ROOT=\"$VENV_ROOT\" already in ~/.zshrc"
    else
        printf '\n# obsidian-ocr-pipeline\nexport VENV_ROOT="%s"\n' "$VENV_ROOT" >> "$HOME/.zshrc"
        echo "   VENV_ROOT added to ~/.zshrc (new terminal required; this run already uses it)"
    fi
fi

# ─────────────────────────────── ⑤ Stage-1-Links + Verification
say "Stage 1 — CLI scripts"
# Backup: log existing ~/bin symlinks pointing elsewhere before replacing
# (restore via ln -sfn).
BACKUP="$REPO/.setup-bin-backup-$(date +%Y%m%d-%H%M%S).txt"
FOUND=0
for name in pdf-auto pdf-combine pdf-workflow reprocess-raw pdf2md; do
    # pdf2md wrapper is named without .sh; Stage 1 CLIs with .sh.
    src="$REPO/bin/$name"
    [ "$name" = "pdf2md" ] || src="$src.sh"
    if [ -L "$HOME/bin/$name" ] && [ "$(readlink "$HOME/bin/$name")" != "$src" ]; then
        printf '%s -> %s\n' "$name" "$(readlink "$HOME/bin/$name")" >> "$BACKUP"
        FOUND=1
    fi
done
if [ "$FOUND" = 1 ]; then
    echo "   backed up old symlinks: $BACKUP"
else
    rm -f "$BACKUP"
fi
bash "$REPO/install.sh"

# ─────────────────────────────── ⑥ Stage-2-venv + pdf2md-Wrapper
say "Stage 2 — MLX-venv (Status open, best-effort attempted)"
MLX_OK=0
if mkvenv "$VENV_ROOT/mlxocr" "$PY312"; then
    if "$VENV_ROOT/mlxocr/bin/pip" install -q -U pip -r "$REPO/pdf2md/requirements.txt"; then
        ln -sfn "$REPO/bin/pdf2md" "$HOME/bin/pdf2md"
        MLX_OK=1
        ok "mlx-vlm in $VENV_ROOT/mlxocr"
    else
        warn "mlx-vlm installation failed — Stage 2 without model, Stage 1 + plugin working"
    fi
else
    warn "venv creation failed — Stage 2 skipped"
fi

# ─────────────────────────────────────────────── ⑦ Plugin (Stage 3)
say "Stage 3 — Plugin"
if [ -d "$VAULT_ROOT/.obsidian" ]; then
    VAULT_ROOT="$VAULT_ROOT" bash "$REPO/plugin/install-plugin.sh"
    JSON="$VAULT_ROOT/.obsidian/community-plugins.json"
    python3 - "$JSON" <<'PY'
import json, sys
p = sys.argv[1]
try:
    with open(p) as f:
        d = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    d = []
if "ocr-vorschau" not in d:
    d.append("ocr-vorschau")
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
    print("   enabled: ocr-vorschau")
else:
    print("   already active: ocr-vorschau")
PY
    if pgrep -x Obsidian >/dev/null 2>&1; then
        warn "Obsidian is running — reload once (Cmd+R)"
    fi
else
    warn "'$VAULT_ROOT' has no .obsidian/ — plugin step skipped (Stage 1+2 installed)"
fi

# ─────────────────────────────────────────────── ⑧ Verification
say "Verification"
FAIL=0
WARN=0
for cmd in pdf-auto pdf-combine pdf-workflow reprocess-raw ocrmypdf qpdf gs img2pdf tesseract pdfinfo pdftotext; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd"
    else
        echo "   MISSING $cmd"
        FAIL=1
    fi
done
if [ "$MLX_OK" = 1 ]; then
    if command -v pdf2md >/dev/null 2>&1; then
        ok "pdf2md"
    else
        echo "   MISSING pdf2md"
        FAIL=1
    fi
else
    warn "pdf2md missing (Stage 2 open)"
    WARN=1
fi
if ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1; then
    ok "Apple Vision plugin"
else
    warn "Apple Vision plugin not usable"
    WARN=1
fi
if tesseract --list-langs 2>/dev/null | grep -qx deu; then
    ok "Tesseract language package 'deu'"
else
    echo "   MISSING Tesseract 'deu'"
    FAIL=1
fi
if [ "$MLX_OK" = 1 ] && "$VENV_ROOT/mlxocr/bin/python" -c "import mlx_vlm" 2>/dev/null; then
    ok "mlx-vlm importable"
else
    warn "mlx-vlm not importable (Stage 2 open)"
    WARN=1
fi
if [ -d "$VAULT_ROOT/.obsidian" ]; then
    if [ -f "$VAULT_ROOT/.obsidian/plugins/ocr-vorschau/main.js" ]; then
        ok "Plugin in $VAULT_ROOT/.obsidian/plugins/ocr-vorschau/"
    else
        echo "   MISSING plugin files (Stage 3)"
        FAIL=1
    fi
else
    warn "no .obsidian/ — plugin step skipped (Stage 1+2 installed)"
    WARN=1
fi

echo
if [ "$FAIL" = 1 ]; then
    echo "Done — with errors (see above)."
    exit 1
fi
if [ "$WARN" = 1 ]; then
    echo "Done — with open items (see ⚠)."
else
    echo "Done. Reload Obsidian (Cmd+R), then everything is ready to use."
fi
echo "Note: open a new terminal window for new shells (PATH changes in ~/.zshrc)."

