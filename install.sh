#!/usr/bin/env bash
# Component of setup.sh: symlinks Stage 1 CLIs to ~/bin and checks
# dependencies. Does not install anything by itself — use ./setup.sh
# in repo root for full setup.
# Idempotent — running multiple times is harmless.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/bin}"

mkdir -p "$BIN_DIR"

echo "== Symlinks to $BIN_DIR"
for name in pdf-auto pdf-combine pdf-workflow reprocess-raw pdf2md; do
    src="$REPO/bin/$name.sh"
    [ -f "$src" ] || src="$REPO/bin/$name"
    dst="$BIN_DIR/$name"
    [ -f "$src" ] || { echo "   !! missing: $src"; exit 1; }
    chmod +x "$src"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "   already linked: $name"
    else
        [ -e "$dst" ] && echo "   replacing existing: $dst"
        ln -sf "$src" "$dst"
        echo "   linked:           $name"
    fi
done

echo
echo "== Dependencies"
MISSING=()
for cmd in ocrmypdf qpdf gs img2pdf tesseract pdfinfo pdftotext; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "   ok      $cmd"
    else
        echo "   MISSING $cmd"
        MISSING+=("$cmd")
    fi
done

echo
echo "== Optional (better compression / cleaning)"
for cmd in pngquant jbig2 unpaper; do
    command -v "$cmd" >/dev/null 2>&1 && echo "   ok      $cmd" || echo "   missing $cmd"
done

echo
echo "== Engines"
if ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1; then
    echo "   ok      Apple Vision Plugin"
else
    echo "   missing Apple Vision Plugin (\${VENV_ROOT:-~/.venvs}/ocrmypdf/bin/pip install ocrmypdf-appleocr)"
fi
if tesseract --list-langs 2>/dev/null | grep -qx deu; then
    echo "   ok      Tesseract language package 'deu'"
else
    echo "   MISSING Tesseract language package 'deu' (brew install tesseract-lang)"
fi

echo
echo "== Python (for column_tools.py / --split-columns)"
for candidate in "${VENV_ROOT:-$HOME/.venvs}/ocrmypdf/bin/python3" python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import pikepdf" 2>/dev/null; then
        echo "   ok      pikepdf in $candidate"
        PIKEPDF_OK=1
        break
    fi
done
[ "${PIKEPDF_OK:-0}" = 1 ] || echo "   MISSING pikepdf (pip install pikepdf) — --split-columns and reprocess-raw need it"

if [ ${#MISSING[@]} -gt 0 ]; then
    echo
    echo "Missing core tools: ${MISSING[*]}"
    echo "  run ./setup.sh in repo root once (brew packages + ocrmypdf-venv)"
    exit 1
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo; echo "Note: $BIN_DIR is not in PATH."
       echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac

echo
echo "Done. Stage 2 (PDF → Markdown) and the plugin are configured via ./setup.sh in repo root."

