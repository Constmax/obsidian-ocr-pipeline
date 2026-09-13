#!/bin/bash
# ============================================================
# reprocess-raw.sh — Safely re-process an EXISTING raw/ PDF in place.
#
# Runs the full pdf-combine pipeline on a copy of the source file and
# overwrites the original ONLY if both hold:
#   1. Page count is preserved exactly (no silent half-page bug)
#   2. Every page has at least --min-chars characters (B5 gate — a
#      document-wide character average can hide a single completely
#      textless page, see BUGREPORT-2026-07-06-split-merge.md)
#
# On failure, the source file remains unchanged; the failed
# result is saved alongside for inspection (<name>_FAILED_*.pdf).
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

PDF_COMBINE="$HOME/bin/pdf-combine"
if [ ! -x "$PDF_COMBINE" ]; then
    PDF_COMBINE="$SCRIPT_DIR/pdf-combine.sh"
fi

if [ $# -lt 1 ]; then
    cat <<EOF
Usage: $(basename "$0") <raw-pdf-file> [pdf-combine-options] [--min-chars N] [--allow-pages LIST]

Re-processes an existing raw/ PDF file using the current pipeline
and overwrites it ONLY after passing B5 verification:
   1. Page count preserved exactly
   2. Every page >= --min-chars characters (Default: 50)

Options:
   --min-chars N        Minimum characters per page (Default: 50)
   --allow-pages LIST   Exempt pages from check 2, e.g. "1,5-7"
                        (known cover/diagram pages without body text)

All other flags are passed through 1:1 to pdf-combine
(e.g. --engine, --split-columns, --force-ocr, --dpi).

Example:
   $(basename "$0") "raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" --force-ocr --split-columns
EOF
    exit 1
fi

SRC="$1"; shift
if [ ! -f "$SRC" ]; then
    echo "❌ File not found: $SRC"; exit 1
fi
SRC_ABS="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"

MIN_CHARS=50
ALLOW_PAGES=""
COMBINE_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --min-chars)   MIN_CHARS="$2"; shift 2 ;;
        --allow-pages) ALLOW_PAGES="$2"; shift 2 ;;
        *) COMBINE_ARGS+=("$1"); shift ;;
    esac
done

BASE="$(basename "$SRC_ABS" .pdf)"
ORIG_PAGES=$(pdfinfo "$SRC_ABS" 2>/dev/null | awk '/^Pages:/ {print $2}')
if [ -z "$ORIG_PAGES" ]; then
    echo "❌ Cannot determine page count of $SRC_ABS"; exit 1
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cp "$SRC_ABS" "$WORK_DIR/"
# Output name intentionally != input base name: otherwise pdf-combine excludes
# the source file itself from the PDF list (collision guard against
# infinite loops during re-runs) and reports "No PDFs found".
OUTNAME="${BASE}_reprocessed"

echo "🔄 Reprocessing: $SRC_ABS ($ORIG_PAGES pages)"
if ! "$PDF_COMBINE" "$WORK_DIR" "$OUTNAME" "${COMBINE_ARGS[@]}"; then
    echo "❌ pdf-combine failed — $SRC_ABS remains unchanged"
    exit 1
fi

OUT="$WORK_DIR/${OUTNAME}.pdf"
if [ ! -f "$OUT" ]; then
    echo "❌ No output generated — $SRC_ABS remains unchanged"
    exit 1
fi

NEW_PAGES=$(pdfinfo "$OUT" 2>/dev/null | awk '/^Pages:/ {print $2}')
if [ "$NEW_PAGES" != "$ORIG_PAGES" ]; then
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_pagecount.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "❌ Page count mismatch (Original: $ORIG_PAGES, New: $NEW_PAGES) — $SRC_ABS remains unchanged"
    echo "   Result saved for inspection: $FAILED_OUT (delete afterwards)"
    exit 1
fi

echo "📋 B5 Gate: checking chars/page (min: $MIN_CHARS)..."
PYTHON_BIN=""
for candidate in "${VENV_ROOT:-$HOME/.venvs}/ocrmypdf/bin/python3" "python3"; do
    if "$candidate" -c "import pikepdf" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "⚠️  pikepdf not found — cannot check B5 gate, aborting for safety"
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_noverify.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "   Result saved for manual inspection: $FAILED_OUT"
    exit 1
fi

VERIFY_ARGS=(verify-pages "$OUT" --min-chars "$MIN_CHARS")
if [ -n "$ALLOW_PAGES" ]; then
    VERIFY_ARGS+=(--allow-pages "$ALLOW_PAGES")
fi

if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" "${VERIFY_ARGS[@]}"; then
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_pages.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "❌ B5 gate failed (see pages above) — $SRC_ABS remains unchanged"
    echo "   Result saved for inspection: $FAILED_OUT (delete afterwards, or provide --allow-pages)"
    exit 1
fi

cp "$OUT" "$SRC_ABS"
echo "✅ Overwritten: $SRC_ABS ($NEW_PAGES pages, B5 gate passed)"

