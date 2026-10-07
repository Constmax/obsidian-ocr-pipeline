#!/bin/bash
# ============================================================
# PDF-Only Workflow: Multiple PDFs → one searchable PDF
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=./pdf-lib.sh
source "$SCRIPT_DIR/pdf-lib.sh"

if [ $# -lt 2 ]; then
    cat <<EOF
Usage: $(basename "$0") <folder> <output-name> [options]

Options:
   --engine auto|apple|tesseract|paddle
                                   OCR engine (Default: auto = Paddle fast if ready,
                                   else Apple Vision, else Tesseract)
   --paddle-mode accurate|fast     PaddleOCR mode (Default: accurate; fast needs macOS 13+)
   --dpi N                         Downscale target (Default: $DEFAULT_DPI, 0 = off)
   --jobs N                        Parallel OCR workers (Default: by RAM, 1–4)
   --force-ocr                     Force OCR even if text layer exists
   --split-columns                 Detect two-column pages, split + re-merge
   --split-columns-all             Like --split-columns, but split ALL pages (no auto-detect)
   --keep-split                    Suppress re-merge (keep half-pages)
   --no-quality-gate               Disable quality check + auto-retry
   --min-average-chars N           Quality check: minimum characters per page on average (default 200)
EOF
    exit 1
fi

INPUT_DIR=$(cd "$1" 2>/dev/null && pwd) || {
    echo "❌ Folder not found: $1"; exit 1
}
shift
OUTPUT_NAME="$1"
OUTPUT_NAME="${OUTPUT_NAME%.pdf}"
OUTPUT_NAME="${OUTPUT_NAME%.PDF}"
shift

if [ -z "$OUTPUT_NAME" ]; then
    echo "❌ Output name cannot be empty"; exit 1
fi

OUTPUT_FILE="${INPUT_DIR}/${OUTPUT_NAME}.pdf"

# Common options (--engine, --paddle-mode, --dpi, --jobs, split flags, --no-quality-gate)
# are parsed and validated by parse_common_option in pdf-lib.sh.
FORCE_OCR=false
while [ $# -gt 0 ]; do
    case "$1" in
        --force-ocr)        FORCE_OCR=true; shift ;;
        *) parse_common_option "$@"; shift "$OPTION_SHIFT" ;;
    esac
done

# ── Init ──
lib_init "$ENGINE" "false"

# ── Local setup ──
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

cd "$INPUT_DIR"
ORIGINAL_SIZE=$(du -sh "$INPUT_DIR" | cut -f1)

# ── Step 1: Collect PDFs ──
echo ""
echo "📑 Step 1/2: Collecting PDFs..."

shopt -s nullglob nocaseglob
PDF_LIST_RAW=(*.pdf)
shopt -u nullglob nocaseglob

PDF_LIST=()
if [ ${#PDF_LIST_RAW[@]} -gt 0 ]; then
    for pdf in "${PDF_LIST_RAW[@]}"; do
        [ "$pdf" != "${OUTPUT_NAME}.pdf" ] && PDF_LIST+=("$pdf")
    done
fi

if [ ${#PDF_LIST[@]} -eq 0 ]; then
    echo "❌ No PDFs found"; exit 1
fi

SORTED=()
while IFS= read -r line; do SORTED+=("$line"); done \
    < <(printf '%s\n' "${PDF_LIST[@]}" | sort -V)
PDF_LIST=("${SORTED[@]}")

echo "   Found: ${#PDF_LIST[@]} PDFs"
for i in "${!PDF_LIST[@]}"; do
    printf "     %2d. %s\n" "$((i+1))" "${PDF_LIST[$i]}"
done

# ── Step 2: merge, OCR, re-merge (run_pdf_pipeline in pdf-lib.sh) ──
# Unlike pdf-auto and pdf-workflow, pdf-combine never passed unpaper's --clean.
echo ""
echo "🔤 Step 2/2: Merge + OCR + Optimization..."
pipeline_options=(--no-clean)
[ "$FORCE_OCR" = true ] && pipeline_options+=(--force-ocr)
if ! run_pdf_pipeline "${pipeline_options[@]}" "$OUTPUT_FILE" "${PDF_LIST[@]}"; then
    # Its ❌ line names the cause; the plugin shows the last one (#217).
    exit 1
fi

FINAL_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)

echo ""
echo "═══════════════════════════════════════════"
echo "✅ Done!"
echo "═══════════════════════════════════════════"
echo "📄 File:     $OUTPUT_FILE"
echo "🧠 Engine:   $OCR_RESULT_DESC"
echo "📊 Pages:    $PIPELINE_PAGES"
echo "📊 Before:   $ORIGINAL_SIZE → After: $FINAL_SIZE"

