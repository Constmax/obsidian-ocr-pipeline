#!/bin/bash
# ============================================================
# PDF Workflow: Images + PDFs → searchable PDF
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

# All options are common ones, parsed and validated in pdf-lib.sh.
while [ $# -gt 0 ]; do
    parse_common_option "$@"; shift "$OPTION_SHIFT"
done

# ── Init ──
lib_init "$ENGINE" "false"

# ── Local setup ──
check_deps img2pdf || exit 1

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

cd "$INPUT_DIR"
ORIGINAL_SIZE=$(du -sh "$INPUT_DIR" | cut -f1)

# ── Step 1: Images → PDF ──
echo ""
echo "📸 Step 1/3: Searching for images..."

shopt -s nullglob nocaseglob
IMAGES_RAW=(*.jpg *.jpeg *.png *.tiff *.tif)
shopt -u nullglob nocaseglob

IMAGES=()
if [ ${#IMAGES_RAW[@]} -gt 0 ]; then
    while IFS= read -r line; do IMAGES+=("$line"); done \
        < <(printf '%s\n' "${IMAGES_RAW[@]}" | sort -V)
fi

if [ ${#IMAGES[@]} -gt 0 ]; then
    echo "   Found: ${#IMAGES[@]} images"
    img2pdf "${IMAGES[@]}" -o "$WORK_DIR/from_images.pdf"
    echo "   ✅ Images converted to PDF"
else
    echo "   No images found"
fi

# ── Step 2: Collect PDFs ──
echo ""
echo "📑 Step 2/3: Collecting PDFs..."

shopt -s nullglob nocaseglob
PDF_LIST_RAW=(*.pdf)
shopt -u nullglob nocaseglob

PDF_LIST=()
if [ ${#PDF_LIST_RAW[@]} -gt 0 ]; then
    for pdf in "${PDF_LIST_RAW[@]}"; do
        [ "$pdf" != "${OUTPUT_NAME}.pdf" ] && PDF_LIST+=("$pdf")
    done
fi

if [ ${#PDF_LIST[@]} -gt 0 ]; then
    SORTED=()
    while IFS= read -r line; do SORTED+=("$line"); done \
        < <(printf '%s\n' "${PDF_LIST[@]}" | sort -V)
    PDF_LIST=("${SORTED[@]}")
fi

[ -f "$WORK_DIR/from_images.pdf" ] && PDF_LIST+=("$WORK_DIR/from_images.pdf")

if [ ${#PDF_LIST[@]} -eq 0 ]; then
    echo "❌ No images or PDFs found"; exit 1
fi

# ── Step 3: merge, OCR, re-merge (run_pdf_pipeline in pdf-lib.sh) ──
echo ""
echo "🔤 Step 3/3: Merge + OCR + Optimization..."
if ! run_pdf_pipeline "$OUTPUT_FILE" "${PDF_LIST[@]}"; then
    # Its ❌ line names the cause; the plugin shows the last one (#217).
    exit 1
fi

FINAL_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)

echo ""
echo "═══════════════════════════════════════════"
echo "✅ Done!"
echo "═══════════════════════════════════════════"
echo "📄 Output:   $OUTPUT_FILE"
echo "🧠 Engine:   $OCR_RESULT_DESC"
echo "📊 Before:   $ORIGINAL_SIZE → After: $FINAL_SIZE"
echo ""
echo "💡 Open: open \"$OUTPUT_FILE\""

