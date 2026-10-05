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
# Without --output the accepted result replaces the source in place, in one
# atomic rename. On failure the source remains unchanged; the failed result is
# saved alongside for inspection (<name>_FAILED_*.pdf). --in-place does the
# same but writes nothing on failure or cancellation (the plugin's mode). It
# only adds a text layer (pdf-combine --text-only: no Ghostscript, deskew,
# optimization or column split), refuses a PDF whose pages all have text, and
# with --keep-original leaves the replaced file under that name.
#
# With --output the source is never written. The result is published under
# the new name without overwriting anything, and failure or cancellation
# leaves no file behind (no partial PDF, no _FAILED_ artifact).
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=./pdf-lib.sh
source "$SCRIPT_DIR/pdf-lib.sh"

PDF_COMBINE="$HOME/bin/pdf-combine"
if [ ! -x "$PDF_COMBINE" ]; then
    PDF_COMBINE="$SCRIPT_DIR/pdf-combine.sh"
fi

# --check-engine [--engine E] [--paddle-mode M]: is the engine usable here?
if [ "${1:-}" = "--check-engine" ]; then
    shift
    while [ $# -gt 0 ]; do
        parse_common_option "$@"; shift "$OPTION_SHIFT"
    done
    check_engine "$ENGINE"
fi

if [ $# -lt 1 ]; then
    cat <<EOF
Usage: $(basename "$0") <raw-pdf-file> [--output FILE | --in-place [--keep-original FILE]] [pdf-combine-options] [--min-chars N] [--allow-pages LIST]
       $(basename "$0") --check-engine [--engine E] [--paddle-mode M]

Re-processes an existing raw/ PDF file using the current pipeline
and accepts the result ONLY after passing B5 verification:
   1. Page count preserved exactly
   2. Every page >= --min-chars characters (Default: 50)

Without --output the source file is replaced; a failed result is kept
beside it as <name>_FAILED_*.pdf.

Options:
   --output FILE        Write the result to FILE instead; the source is never
                        modified. FILE must not exist yet and is never
                        overwritten; on failure nothing is written.
   --in-place           Add a text layer to the source and keep its pages
                        as they are (no downscaling, MediaBox fix, deskew,
                        optimization or column split). On failure nothing is
                        written (no _FAILED_ file); the source stays
                        unchanged if it was modified while OCR ran. Refused
                        when every page already has text.
   --keep-original FILE With --in-place: the replaced file stays at FILE
                        (a hard link, or a copy). FILE's folder must exist
                        and FILE must not.
   --min-chars N        Minimum characters per page (Default: 50)
   --allow-pages LIST   Exempt pages from check 2, e.g. "1,5-7"
                        (known cover/diagram pages without body text)

--check-engine reports whether a run with this engine would start here and
exits: 0 usable, 4 not usable (reason on stderr). No file is read or written.

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
IN_PLACE=""
KEEP_ORIGINAL=""
COMBINE_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --min-chars)
            require_option_value "$@"
            case "$2" in
                *[!0-9]*) usage_error "--min-chars must be a whole number, got '$2'" ;;
            esac
            MIN_CHARS=$((10#$2)); shift 2 ;;
        --allow-pages)
            require_option_value "$@"
            ALLOW_PAGES="$2"; shift 2 ;;
        --output)
            require_option_value "$@"
            if [ -n "$DEST_ARG" ]; then
                usage_error "--output needs exactly one file name"
            fi
            DEST_ARG="$2"; shift 2 ;;
        --in-place)
            IN_PLACE=1; shift ;;
        --keep-original)
            require_option_value "$@"
            KEEP_ORIGINAL="$2"; shift 2 ;;
        *) COMBINE_ARGS+=("$1"); shift ;;
    esac
done

if [ -n "$IN_PLACE" ] && [ -n "$DEST_ARG" ]; then
    usage_error "--in-place and --output exclude each other"
fi
if [ -n "$KEEP_ORIGINAL" ] && [ -z "$IN_PLACE" ]; then
    usage_error "--keep-original needs --in-place"
fi
if [ -n "$IN_PLACE" ]; then
    # Each of these makes Ghostscript or OCRmyPDF rewrite the pages (#180).
    for arg in ${COMBINE_ARGS[@]+"${COMBINE_ARGS[@]}"}; do
        case "$arg" in
            --split-columns|--split-columns-all|--keep-split|--force-ocr|--dpi)
                usage_error "--in-place adds only a text layer: $arg is not allowed" ;;
        esac
    done
    COMBINE_ARGS+=(--text-only)
fi

if [ -n "$KEEP_ORIGINAL" ]; then
    KEEP_DIR="$(cd "$(dirname "$KEEP_ORIGINAL")" 2>/dev/null && pwd -P)" || {
        echo "❌ --keep-original folder not found: $(dirname "$KEEP_ORIGINAL")"; exit 1
    }
    KEEP_ORIGINAL="$KEEP_DIR/$(basename "$KEEP_ORIGINAL")"
    if [ -e "$KEEP_ORIGINAL" ] || [ -L "$KEEP_ORIGINAL" ]; then
        echo "❌ --keep-original already exists, not overwriting: $KEEP_ORIGINAL"; exit 1
    fi
fi

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

# Both in-place modes replace the source: refuse a read-only or locked one
# before OCR rather than after it.
if [ -z "$DEST" ] && [ ! -w "$(readlink -f "$SRC_ABS")" ]; then
    echo "❌ Source is read-only or locked, not replacing it: $SRC_ABS"; exit 1
fi
# The hidden copy and the rename need the folder.
if [ -z "$DEST" ] && [ ! -w "$(dirname "$(readlink -f "$SRC_ABS")")" ]; then
    echo "❌ Folder is not writable, cannot replace the PDF in it: $(dirname "$(readlink -f "$SRC_ABS")")"; exit 1
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
# --output and --in-place keep no result for inspection, so fail before OCR.
if [ -z "$PYTHON_BIN" ] && [ -n "$DEST$IN_PLACE" ]; then
    echo "⚠️  pikepdf not found — cannot check B5 gate, aborting for safety"
    exit 1
fi

# OCRmyPDF skips a page that has any text, so there would be nothing to add.
if [ -n "$IN_PLACE" ] \
    && "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" verify-pages "$SRC_ABS" --min-chars 1 >/dev/null 2>&1; then
    echo "❌ Every page already has a text layer, nothing to add: $SRC_ABS"; exit 1
fi

# WORK_DIR lives in $TMPDIR, outside the vault. TMP_DEST is the hidden
# same-folder copy that publish_output links to its final name, or that
# replace_source renames over the source.
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
# The legacy in-place mode keeps a failed result beside the source for
# inspection; --output and --in-place write nothing. Always exits 1.
reject_result() {
    if [ -n "$DEST" ]; then
        echo "   No file written: $DEST"
    elif [ -n "$IN_PLACE" ]; then
        echo "   No file written: $SRC_ABS remains unchanged"
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

# replace_source
# Swaps $OUT in for the source with one rename, so the source is either the
# old or the new file, never a partial one. The hidden copy starts as a copy
# of the source (mode and attributes) and then takes the result's bytes.
# Refuses if the source no longer matches the copy OCR started from.
replace_source() {
    local target
    target="$(readlink -f "$SRC_ABS")"
    if ! cmp -s "$target" "$WORK_DIR/$(basename "$SRC_ABS")"; then
        echo "❌ Source changed during processing, not replacing it: $SRC_ABS"
        reject_result changed
    fi
    # cp -p would carry a read-only mode or lock flag onto the hidden copy.
    if [ ! -w "$target" ]; then
        echo "❌ Source became read-only or locked during processing: $SRC_ABS"
        reject_result readonly
    fi
    TMP_DEST=$(mktemp "$(dirname "$target")/.$(basename "$target").XXXXXX") || {
        echo "❌ Cannot create a temporary file in $(dirname "$target")"
        reject_result replace
    }
    if ! { cp -p "$target" "$TMP_DEST" && cat "$OUT" > "$TMP_DEST"; }; then
        echo "❌ Cannot write the temporary file $TMP_DEST"
        reject_result replace
    fi
    # ponytail: a change between cmp and mv is still lost; that window is milliseconds.
    # From here on the run finishes: a SIGTERM after the rename would report a
    # cancelled run whose result is already in place.
    trap '' TERM INT
    if [ -n "$KEEP_ORIGINAL" ] && ! ln "$target" "$KEEP_ORIGINAL" 2>/dev/null \
        && ! cp -p "$target" "$KEEP_ORIGINAL"; then
        rm -f "$KEEP_ORIGINAL"
        echo "❌ Cannot keep the original at $KEEP_ORIGINAL, not replacing it"
        reject_result keep
    fi
    if ! mv -f "$TMP_DEST" "$target"; then
        [ -n "$KEEP_ORIGINAL" ] && rm -f "$KEEP_ORIGINAL"
        echo "❌ Cannot replace $SRC_ABS"
        reject_result replace
    fi
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
    replace_source
    echo "✅ Overwritten: $SRC_ABS ($NEW_PAGES pages, B5 gate passed)"
fi
