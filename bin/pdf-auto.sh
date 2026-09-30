#!/bin/bash
# ============================================================
# PDF Auto-Batch: Automatically process folders containing PDFs
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=./pdf-lib.sh
source "$SCRIPT_DIR/pdf-lib.sh"

if [ $# -lt 1 ]; then
    cat <<EOF
Usage: $(basename "$0") <folder> [options]

Options:
   --output-dir DIR                Output folder (Default: <input>/_processed)
   --engine auto|apple|tesseract|paddle
                                   OCR engine (Default: auto; paddle only explicitly)
   --paddle-mode accurate|fast     PaddleOCR mode (Default: accurate; fast needs macOS 13+)
   --dpi N                         Downscale target (Default: $DEFAULT_DPI, 0 = off)
   --jobs N                        Parallel OCR workers (Default: by RAM, 1–4)
   --cleanup                       Move originals to _archive/ after success
   --fast                          Presets for large batches (dpi $FAST_DPI, jobs $FAST_JOBS)
   --split-columns                 Detect two-column pages, split + re-merge
   --split-columns-all             Like --split-columns, but split ALL pages (no auto-detect)
   --keep-split                    Suppress re-merge (keep half-pages)
   --no-quality-gate               Disable quality check + auto-retry
EOF
    exit 1
fi

INPUT_DIR=$(cd "$1" 2>/dev/null && pwd) || {
    echo "❌ Folder not found: $1"; exit 1
}
shift

# Common options (--engine, --paddle-mode, --dpi, --jobs, split flags, --no-quality-gate)
# are parsed and validated by parse_common_option in pdf-lib.sh.
OUTPUT_DIR="$INPUT_DIR/_processed"
CLEANUP=false
FAST=false
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir)       require_option_value "$@"; OUTPUT_DIR="$2"; shift 2 ;;
        --cleanup)          CLEANUP=true; shift ;;
        --fast)             FAST=true; shift ;;
        *) parse_common_option "$@"; shift "$OPTION_SHIFT" ;;
    esac
done

# ── Init shared state ──
lib_init "$ENGINE" "$FAST"

# ── Local setup ──
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)

ARCHIVE_DIR="$INPUT_DIR/_archive"
if [ "$CLEANUP" = true ]; then
    mkdir -p "$ARCHIVE_DIR"
fi

[ "$CLEANUP" = true ] && echo "   🧹 --cleanup active (Archive: $ARCHIVE_DIR)"

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

cd "$INPUT_DIR"

shopt -s nullglob nocaseglob
ALL_PDFS=(*.pdf)
shopt -u nullglob nocaseglob

if [ ${#ALL_PDFS[@]} -eq 0 ]; then
    echo "❌ No PDFs in folder"; exit 1
fi

echo ""
echo "📂 Input:   $INPUT_DIR"
echo "📁 Output:  $OUTPUT_DIR"
echo "📄 PDFs:    ${#ALL_PDFS[@]}"

# ── Group detection ──
for pdf in "${ALL_PDFS[@]}"; do
    if [[ "$pdf" =~ ^(.+)[[:space:]]+[Tt]eil[[:space:]]+([0-9]+)\.[Pp][Dd][Ff]$ ]]; then
        base="${BASH_REMATCH[1]}"; part="${BASH_REMATCH[2]}"
    else
        base="${pdf%.[Pp][Dd][Ff]}"; part="0"
    fi
    printf '%s\t%03d\t%s\n' "$base" "$part" "$pdf" >> "$WORK_DIR/files.tsv"
done

sort -t$'\t' -k1,1V -k2,2n "$WORK_DIR/files.tsv" > "$WORK_DIR/sorted.tsv"

# ── One group → one output (run_pdf_pipeline in pdf-lib.sh) ──
process_group() {
    local base="$1"; shift
    local output_file="$OUTPUT_DIR/${base}.pdf"

    echo ""
    if [ $# -eq 1 ]; then
        echo "📄 Single file: $base"
    else
        echo "📚 Group: $base ($# parts)"
    fi
    local f
    for f in "$@"; do echo "   → $f"; done

    if ! run_pdf_pipeline "$output_file" "$@"; then
        echo "   ❌ '$base' failed"
        return 1
    fi

    local size; size=$(du -h "$output_file" | cut -f1)
    echo "   ✅ Done: $output_file ($size)"
    if [ "$OCR_RESULT_DESC" != "$ENGINE_DESC" ]; then
        echo "   🔄 Engine: $OCR_RESULT_DESC"
    fi

    if [ "$CLEANUP" = true ]; then
        for f in "$@"; do
            if [ -f "$f" ]; then
                mv "$f" "$ARCHIVE_DIR/" 2>/dev/null && \
                    echo "   🧹 Archived: $f"
            fi
        done
    fi
    return 0
}

# ── Main loop ──
current_base=""
GROUP_FILES=()
SUCCESS_COUNT=0
FAIL_COUNT=0

while IFS=$'\t' read -r base part fname; do
    if [ "$base" != "$current_base" ]; then
        if [ ${#GROUP_FILES[@]} -gt 0 ]; then
            if process_group "$current_base" "${GROUP_FILES[@]}"; then
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            else
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        fi
        current_base="$base"
        GROUP_FILES=("$fname")
    else
        GROUP_FILES+=("$fname")
    fi
done < "$WORK_DIR/sorted.tsv"

if [ ${#GROUP_FILES[@]} -gt 0 ]; then
    if process_group "$current_base" "${GROUP_FILES[@]}"; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi

print_summary "$SUCCESS_COUNT" "$FAIL_COUNT" "$OUTPUT_DIR"
if [ "$CLEANUP" = true ]; then
    echo "🧹 Archive:     $ARCHIVE_DIR"
fi

