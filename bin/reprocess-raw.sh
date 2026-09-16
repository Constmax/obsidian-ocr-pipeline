#!/bin/bash
# ============================================================
# reprocess-raw.sh — Safely re-process an EXISTING raw/ PDF.
#
# Runs the full pdf-combine pipeline on a copy of the source file and
# accepts the result ONLY if both hold:
#   1. Page count is preserved exactly (no silent half-page bug)
#   2. Every page has at least --min-chars characters (B5 gate — a
#      document-wide character average can hide a single completely
#      textless page, see BUGREPORT-2026-07-06-split-merge.md)
#
# Without --output the accepted result overwrites the source in place. On
# failure the source remains unchanged; the failed result is saved alongside
# for inspection (<name>_FAILED_*.pdf).
#
# With --output the source is never written. The result is published under
# the new name without overwriting anything, and failure or cancellation
# leaves no file behind (no partial PDF, no _FAILED_ artifact).
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

PDF_COMBINE="$HOME/bin/pdf-combine"
if [ ! -x "$PDF_COMBINE" ]; then
    PDF_COMBINE="$SCRIPT_DIR/pdf-combine.sh"
fi

if [ $# -lt 1 ]; then
    cat <<EOF
Usage: $(basename "$0") <raw-pdf-file> [--output FILE] [pdf-combine-options] [--min-chars N] [--allow-pages LIST]

Re-processes an existing raw/ PDF file using the current pipeline
and accepts the result ONLY after passing B5 verification:
   1. Page count preserved exactly
   2. Every page >= --min-chars characters (Default: 50)

Without --output the source file is overwritten.

Options:
   --output FILE        Write the result to FILE instead; the source is never
                        modified. FILE must not exist yet and is never
                        overwritten; on failure nothing is written.
   --min-chars N        Minimum characters per page (Default: 50)
   --allow-pages LIST   Exempt pages from check 2, e.g. "1,5-7"
                        (known cover/diagram pages without body text)

All other flags are passed through 1:1 to pdf-combine
(e.g. --engine, --split-columns, --force-ocr, --dpi).

Examples:
   $(basename "$0") "raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" --force-ocr --split-columns
   $(basename "$0") casebook.pdf --output casebook-ocr.pdf --engine tesseract
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
DEST_ARG=""
COMBINE_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --min-chars)   MIN_CHARS="$2"; shift 2 ;;
        --allow-pages) ALLOW_PAGES="$2"; shift 2 ;;
        --output)
            if [ $# -lt 2 ] || [ -z "$2" ] || [ -n "$DEST_ARG" ]; then
                echo "❌ --output needs exactly one file name"; exit 1
            fi
            DEST_ARG="$2"; shift 2 ;;
        *) COMBINE_ARGS+=("$1"); shift ;;
    esac
done

# ── --output: resolve and reject the destination before any processing ──
# DEST is the physical path (symlinked folders resolved), so a destination
# that is the source under another spelling is recognized as such.
DEST=""
if [ -n "$DEST_ARG" ]; then
    DEST_NAME="$(basename "$DEST_ARG")"
    case "$DEST_ARG" in
        */) DEST_NAME="" ;;
    esac
    if [ -z "$DEST_NAME" ] || [ "$DEST_NAME" = "." ] || [ "$DEST_NAME" = ".." ]; then
        echo "❌ --output must name a file: $DEST_ARG"; exit 1
    fi
    DEST_DIR="$(cd "$(dirname "$DEST_ARG")" 2>/dev/null && pwd -P)" || {
        echo "❌ Output folder not found: $(dirname "$DEST_ARG")"; exit 1
    }
    DEST="$DEST_DIR/$DEST_NAME"
    if [ "$DEST" = "$(readlink -f "$SRC_ABS")" ] || [ "$DEST" -ef "$SRC_ABS" ]; then
        echo "❌ --output is the source file itself: $DEST_ARG"; exit 1
    fi
    if [ -e "$DEST" ] || [ -L "$DEST" ]; then
        echo "❌ Output already exists, not overwriting: $DEST"; exit 1
    fi
fi

BASE="$(basename "$SRC_ABS" .pdf)"
ORIG_PAGES=$(pdfinfo "$SRC_ABS" 2>/dev/null | awk '/^Pages:/ {print $2}')
if [ -z "$ORIG_PAGES" ]; then
    echo "❌ Cannot determine page count of $SRC_ABS"; exit 1
fi

PYTHON_BIN=""
for candidate in "${VENV_ROOT:-$HOME/.venvs}/ocrmypdf/bin/python3" "python3"; do
    if "$candidate" -c "import pikepdf" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
# --output has no result to keep for manual inspection, so fail before OCR.
if [ -z "$PYTHON_BIN" ] && [ -n "$DEST" ]; then
    echo "⚠️  pikepdf not found — cannot check B5 gate, aborting for safety"
    exit 1
fi

# WORK_DIR lives in $TMPDIR, outside the vault. TMP_DEST is the hidden
# same-folder copy that publish_output links to its final name.
WORK_DIR=""
TMP_DEST=""
cleanup() {
    [ -n "$TMP_DEST" ] && rm -f "$TMP_DEST"
    [ -n "$WORK_DIR" ] && rm -rf "$WORK_DIR"
    return 0
}
# Bash also runs the EXIT trap when SIGTERM ends the script (verified with
# /bin/bash 3.2), so cancellation cleans up too.
trap cleanup EXIT

WORK_DIR=$(mktemp -d)
cp "$SRC_ABS" "$WORK_DIR/"
# Output name intentionally != input base name: otherwise pdf-combine excludes
# the source file itself from the PDF list (collision guard against
# infinite loops during re-runs) and reports "No PDFs found".
OUTNAME="${BASE}_reprocessed"
OUT="$WORK_DIR/${OUTNAME}.pdf"

# reject_result <failed-suffix>
# The in-place mode keeps a failed result beside the source for inspection;
# --output writes nothing. Always exits 1.
reject_result() {
    if [ -n "$DEST" ]; then
        echo "   No file written: $DEST"
    else
        local failed_out="${SRC_ABS%.pdf}_FAILED_$1.pdf"
        cp "$OUT" "$failed_out"
        echo "   Result saved for inspection: $failed_out (delete afterwards)"
    fi
    exit 1
}

# publish_output
# Publishes $OUT as $DEST without ever overwriting: a hidden, non-PDF copy in
# the destination folder is hard-linked to the final name. ln fails if that
# name exists, including one created while OCR was running. Verified to work
# inside iCloud Drive vaults (Documents and the Obsidian container).
publish_output() {
    TMP_DEST=$(mktemp "$DEST_DIR/.${DEST_NAME}.XXXXXX")
    cp "$OUT" "$TMP_DEST"
    # mktemp creates mode 0600; give the result the usual new-file mode.
    chmod "$(printf '%o' $(( 0666 & ~0$(umask) )))" "$TMP_DEST"

    if ! ln "$TMP_DEST" "$DEST" 2>/dev/null; then
        if [ -e "$DEST" ] || [ -L "$DEST" ]; then
            echo "❌ Output appeared during processing, not overwriting: $DEST"
        else
            echo "❌ Cannot link result to $DEST (does this filesystem support hard links?)"
        fi
        echo "   No file written: $DEST"
        exit 1
    fi
    # BSD ln places the link INSIDE the target when the target is a directory
    # (or a symlink to one) that appeared after the initial check.
    if [ ! "$TMP_DEST" -ef "$DEST" ]; then
        rm -f "$DEST/$(basename "$TMP_DEST")"
        echo "❌ Output appeared during processing, not overwriting: $DEST"
        echo "   No file written: $DEST"
        exit 1
    fi
    rm -f "$TMP_DEST"
    TMP_DEST=""
}

echo "🔄 Reprocessing: $SRC_ABS ($ORIG_PAGES pages)"
if ! "$PDF_COMBINE" "$WORK_DIR" "$OUTNAME" ${COMBINE_ARGS[@]+"${COMBINE_ARGS[@]}"}; then
    echo "❌ pdf-combine failed — $SRC_ABS remains unchanged"
    exit 1
fi

if [ ! -s "$OUT" ]; then
    echo "❌ No output generated — $SRC_ABS remains unchanged"
    exit 1
fi

NEW_PAGES=$(pdfinfo "$OUT" 2>/dev/null | awk '/^Pages:/ {print $2}')
if [ "$NEW_PAGES" != "$ORIG_PAGES" ]; then
    echo "❌ Page count mismatch (Original: $ORIG_PAGES, New: $NEW_PAGES) — $SRC_ABS remains unchanged"
    reject_result pagecount
fi

echo "📋 B5 Gate: checking chars/page (min: $MIN_CHARS)..."
if [ -z "$PYTHON_BIN" ]; then
    echo "⚠️  pikepdf not found — cannot check B5 gate, aborting for safety"
    reject_result noverify
fi

VERIFY_ARGS=(verify-pages "$OUT" --min-chars "$MIN_CHARS")
if [ -n "$ALLOW_PAGES" ]; then
    VERIFY_ARGS+=(--allow-pages "$ALLOW_PAGES")
fi

if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" "${VERIFY_ARGS[@]}"; then
    echo "❌ B5 gate failed (see pages above) — $SRC_ABS remains unchanged"
    if [ -z "$DEST" ]; then
        echo "   Provide --allow-pages for known pages without body text"
    fi
    reject_result pages
fi

if [ -n "$DEST" ]; then
    publish_output
    echo "✅ Written: $DEST ($NEW_PAGES pages, B5 gate passed; source unchanged)"
else
    cp "$OUT" "$SRC_ABS"
    echo "✅ Overwritten: $SRC_ABS ($NEW_PAGES pages, B5 gate passed)"
fi
