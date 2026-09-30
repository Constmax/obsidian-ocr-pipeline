#!/usr/bin/env bash
# Component of setup.sh: adds the PaddleOCR engine plugin (ocrmypdf_paddle)
# to the Stage-1 venv, retained by the benchmark in #71 (#72).
#
#   1. pip installs the plugin from this checkout (editable, so `git pull`
#      updates it) with every package already in the venv as a constraint:
#      pip may add RapidOCR and ONNX Runtime but cannot change what Apple
#      Vision and Tesseract run on. If that is impossible, it fails before
#      it changes anything.
#   2. pip check.
#   3. The pinned model files are fetched and checked (SHA-256), so the
#      first OCR run never downloads.
#   4. --paddle-check for both modes, then a warm smoke test that OCRs one
#      generated page in fast mode with every HTTP proxy pointed at a closed
#      port: it must pass without network.
#
# Any failing step stops with exit 1 and a recovery command; Apple Vision
# and Tesseract stay usable. Idempotent.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV_ROOT:-$HOME/.venvs}/ocrmypdf"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"
OCRMYPDF="$VENV/bin/ocrmypdf"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/install-paddle.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

fail() { # $1 = what failed, $2 = recovery command
    echo "❌ PaddleOCR: $1" >&2
    echo "   Apple Vision and Tesseract are unchanged and stay usable." >&2
    echo "   Fix: $2" >&2
    exit 1
}

[ -x "$PIP" ] || fail "no Stage-1 venv at $VENV" "./setup.sh"

echo "== PaddleOCR engine plugin"
"$PIP" freeze --exclude-editable > "$WORK/constraints.txt"
if ! "$PIP" install -q -c "$WORK/constraints.txt" -e "$REPO/ocrmypdf_paddle[fast]"; then
    fail "pip could not add the plugin without changing the installed Stage-1 packages" \
        "read pip's message above, then run bash $REPO/install-paddle.sh again"
fi
if ! "$PIP" check; then
    fail "pip check found a conflict after the install" \
        "$PIP uninstall -y ocrmypdf-paddle rapidocr onnxruntime, then bash $REPO/install-paddle.sh"
fi
echo "   ok      installed from $REPO/ocrmypdf_paddle"

if ! "$PY" -m ocrmypdf_paddle fetch-models; then
    fail "the model files could not be fetched" "$PY -m ocrmypdf_paddle fetch-models"
fi

# Offline from here on: a download attempt would hit a closed port.
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9
export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" all_proxy="$ALL_PROXY" NO_PROXY="" no_proxy=""

for mode in accurate fast; do
    if ! "$OCRMYPDF" --plugin ocrmypdf_paddle --paddle-check "$mode"; then
        fail "not ready in $mode mode" "$OCRMYPDF --plugin ocrmypdf_paddle --paddle-check $mode"
    fi
done

# One page of text, rendered here, so the smoke test needs no fixture file.
"$PY" - "$WORK/page.pdf" <<'PYTHON'
import sys
from pathlib import Path

import img2pdf
from PIL import Image, ImageDraw, ImageFont

image = Image.new("L", (2480, 600), 255)
font_path = Path("/System/Library/Fonts/Helvetica.ttc")
font = (ImageFont.truetype(str(font_path), 72) if font_path.exists()
        else ImageFont.load_default(size=72))
ImageDraw.Draw(image).text((200, 250), "Paddle Probe § 823 BGB", font=font, fill=0)
image.save(Path(sys.argv[1]).with_suffix(".png"), dpi=(300, 300))
Path(sys.argv[1]).write_bytes(img2pdf.convert(str(Path(sys.argv[1]).with_suffix(".png"))))
PYTHON
if ! "$OCRMYPDF" --plugin ocrmypdf_paddle --paddle-mode fast -l deu --jobs 1 --output-type pdf \
        "$WORK/page.pdf" "$WORK/ocr.pdf" >"$WORK/smoke.log" 2>&1 \
    || ! pdftotext "$WORK/ocr.pdf" - | grep -q "823"; then
    cat "$WORK/smoke.log" >&2 || true
    fail "the offline smoke test did not read its test page" "bash $REPO/install-paddle.sh"
fi
echo "   ok      PaddleOCR ready (offline smoke test passed)"
