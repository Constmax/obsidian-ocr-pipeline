#!/usr/bin/env bash
# Baustein von setup.sh: verlinkt die Stufe-1-CLIs nach ~/bin und prüft die
# Abhängigkeiten. Installiert nichts selbst — für die Einrichtung ./setup.sh
# im Repo-Root verwenden.
# Idempotent — mehrfaches Ausführen ist unschädlich.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/bin}"

mkdir -p "$BIN_DIR"

echo "== Symlinks nach $BIN_DIR"
for name in pdf-auto pdf-combine pdf-workflow reprocess-raw; do
    src="$REPO/bin/$name.sh"
    dst="$BIN_DIR/$name"
    [ -f "$src" ] || { echo "   !! fehlt: $src"; exit 1; }
    chmod +x "$src"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "   schon verlinkt: $name"
    else
        [ -e "$dst" ] && echo "   ersetze bestehendes: $dst"
        ln -sf "$src" "$dst"
        echo "   verlinkt:       $name"
    fi
done

echo
echo "== Abhängigkeiten"
MISSING=()
for cmd in ocrmypdf qpdf gs img2pdf tesseract pdfinfo pdftotext; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "   ok      $cmd"
    else
        echo "   FEHLT   $cmd"
        MISSING+=("$cmd")
    fi
done

echo
echo "== Optional (bessere Kompression / Reinigung)"
for cmd in pngquant jbig2 unpaper; do
    command -v "$cmd" >/dev/null 2>&1 && echo "   ok      $cmd" || echo "   fehlt   $cmd"
done

echo
echo "== Engines"
if ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1; then
    echo "   ok      Apple Vision Plugin"
else
    echo "   fehlt   Apple Vision Plugin (~/.venvs/ocrmypdf/bin/pip install ocrmypdf-appleocr)"
fi
if tesseract --list-langs 2>/dev/null | grep -qx deu; then
    echo "   ok      Tesseract Sprachpaket 'deu'"
else
    echo "   FEHLT   Tesseract Sprachpaket 'deu' (brew install tesseract-lang)"
fi

echo
echo "== Python (für column_tools.py / --split-columns)"
for candidate in "$HOME/.venvs/ocrmypdf/bin/python3" python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import pikepdf" 2>/dev/null; then
        echo "   ok      pikepdf in $candidate"
        PIKEPDF_OK=1
        break
    fi
done
[ "${PIKEPDF_OK:-0}" = 1 ] || echo "   FEHLT   pikepdf (pip install pikepdf) — --split-columns und reprocess-raw brauchen es"

if [ ${#MISSING[@]} -gt 0 ]; then
    echo
    echo "Fehlende Kernwerkzeuge: ${MISSING[*]}"
    echo "  einmal ./setup.sh im Repo-Root ausführen (brew-Pakete + ocrmypdf-venv)"
    exit 1
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo; echo "Hinweis: $BIN_DIR liegt nicht im PATH."
       echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac

echo
echo "Fertig. Stufe 2 (PDF → Markdown) und das Plugin richten sich über ./setup.sh im Repo-Root ein."
