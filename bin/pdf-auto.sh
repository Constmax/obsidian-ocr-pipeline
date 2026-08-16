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
   --engine auto|apple|tesseract   OCR engine (Default: auto)
   --dpi N                         Downscale target (Default: $DEFAULT_DPI, 0 = off)
   --jobs N                        Parallel OCR workers (Default: $DEFAULT_JOBS)
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

OUTPUT_DIR="$INPUT_DIR/_processed"
ENGINE="auto"
TARGET_DPI=$DEFAULT_DPI
JOBS=$DEFAULT_JOBS
CLEANUP=false
FAST=false
NO_QUALITY_GATE=false
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir)       OUTPUT_DIR="$2"; shift 2 ;;
        --engine)           ENGINE="$2"; shift 2 ;;
        --dpi)              TARGET_DPI="$2"; shift 2 ;;
        --jobs)             JOBS="$2"; shift 2 ;;
        --cleanup)          CLEANUP=true; shift ;;
        --fast)             FAST=true; shift ;;
        --split-columns)     SPLIT_COLUMNS=true; shift ;;
        --split-columns-all) SPLIT_COLUMNS=true; SPLIT_ALL_PAGES=true; shift ;;
        --keep-split)        KEEP_SPLIT=true; shift ;;
        --no-quality-gate)   NO_QUALITY_GATE=true; shift ;;
        *) echo "⚠️  Unknown option: $1"; shift ;;
    esac
done

case "$ENGINE" in
    auto|apple|tesseract) ;;
    *) echo "❌ --engine must be 'auto', 'apple', or 'tesseract'"; exit 1 ;;
esac

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

# ── OCR function ──
process_group() {
    local base="$1"; shift
    local files=("$@")
    local count=${#files[@]}
    local output_file="$OUTPUT_DIR/${base}.pdf"

    echo ""
    if [ "$count" -eq 1 ]; then
        echo "📄 Single file: $base"
    else
        echo "📚 Group: $base ($count parts)"
    fi
    for f in "${files[@]}"; do echo "   → $f"; done

    # Merge
    if [ "$count" -eq 1 ]; then
        cp "${files[0]}" "$WORK_DIR/merged.pdf" || { echo "   ❌ cp failed"; return 1; }
    else
        qpdf --empty --pages "${files[@]}" -- "$WORK_DIR/merged.pdf" || { echo "   ❌ qpdf failed"; return 1; }
    fi

    # Fix oversized MediaBox before downscale (Hemmer PDFs: 72 PPI → A4)
    local fixed="$WORK_DIR/fixed.pdf"
    fix_mediabox "$WORK_DIR/merged.pdf" "$fixed"
    local pre_ocr="$fixed"

    # Downscale
    if [ "$TARGET_DPI" -gt 0 ]; then
        gs_downscale "$pre_ocr" "$WORK_DIR/downscaled.pdf" "$TARGET_DPI"
        pre_ocr="$WORK_DIR/downscaled.pdf"
    fi

    # Column split (if requested)
    if [ "$SPLIT_COLUMNS" = true ]; then
        split_two_column_pdf "$pre_ocr" "$WORK_DIR/split.pdf"
        pre_ocr="$WORK_DIR/split.pdf"
    fi

    # Build OCR args (--clean for tesseract when unpaper available;
    # --no-rotate when splitting — per-half orientation detection is
    # unreliable and would break the re-merge)
    # ocr_args is passed by name to build_ocr_args/run_ocr
    # (pass-by-name, bash 3.2) — usage is not seen by shellcheck in pdf-lib.sh.
    # shellcheck disable=SC2034
    local ocr_args
    if [ "$SPLIT_COLUMNS" = true ]; then
        build_ocr_args ocr_args --clean --no-rotate --no-deskew
    else
        build_ocr_args ocr_args --clean
    fi

    # OCR with quality gate
    local ocr_ok=false
    if [ "$NO_QUALITY_GATE" = true ]; then
        if run_ocr "$pre_ocr" "$output_file" ocr_args; then
            ocr_ok=true
        fi
    else
        local alt_engine
        if [ "$USE_APPLE" = true ]; then alt_engine="tesseract"; else alt_engine="apple"; fi
        if ocr_with_retry "$pre_ocr" "$output_file" "$alt_engine" ocr_args; then
            ocr_ok=true
        fi
    fi

    if [ "$ocr_ok" = true ]; then
        # ── Re-Merge after split-columns ──
        if [ "$SPLIT_COLUMNS" = true ] && [ "$KEEP_SPLIT" != true ]; then
            local merged="$WORK_DIR/merged_final.pdf"
            merge_split_pdf "$output_file" "$merged"
            if [ -f "$merged" ]; then
                mv "$merged" "$output_file"
            fi
        fi

        local size; size=$(du -h "$output_file" | cut -f1)
        echo "   ✅ Done: $output_file ($size)"
        rm -f "$WORK_DIR/merged.pdf" "$WORK_DIR/downscaled.pdf" "$WORK_DIR/split.pdf"

        if [ "$CLEANUP" = true ]; then
            for f in "${files[@]}"; do
                if [ -f "$f" ]; then
                    mv "$f" "$ARCHIVE_DIR/" 2>/dev/null && \
                        echo "   🧹 Archived: $f"
                fi
            done
        fi
        return 0
    else
        echo "   ❌ OCR failed for '$base'"
        rm -f "$output_file"
        rm -f "$WORK_DIR/merged.pdf" "$WORK_DIR/downscaled.pdf" "$WORK_DIR/split.pdf"
        return 1
    fi
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

