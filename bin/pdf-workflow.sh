#!/bin/bash
# ============================================================
# PDF Workflow: Bilder + PDFs → durchsuchbares PDF
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=./pdf-lib.sh
source "$SCRIPT_DIR/pdf-lib.sh"

if [ $# -lt 2 ]; then
    cat <<EOF
Usage: $(basename "$0") <ordner> <output-name> [Optionen]

Optionen:
   --engine auto|apple|tesseract   OCR-Engine (Default: auto)
   --dpi N                         Downscale-Ziel (Default: $DEFAULT_DPI, 0 = aus)
   --jobs N                        Parallele OCR-Worker (Default: $DEFAULT_JOBS)
   --split-columns                 Zweispaltige Seiten erkennen, trennen + wieder mergen
   --split-columns-all             Wie --split-columns, aber ALLE Seiten splitten (kein Auto-Detect)
   --keep-split                    Re-Merge unterdrücken (Halbseiten behalten)
   --no-quality-gate               Qualitäts-Check + Auto-Retry deaktivieren
EOF
    exit 1
fi

INPUT_DIR=$(cd "$1" 2>/dev/null && pwd) || {
    echo "❌ Ordner nicht gefunden: $1"; exit 1
}
shift
OUTPUT_NAME="$1"
OUTPUT_NAME="${OUTPUT_NAME%.pdf}"
OUTPUT_NAME="${OUTPUT_NAME%.PDF}"
shift

if [ -z "$OUTPUT_NAME" ]; then
    echo "❌ Output-Name darf nicht leer sein"; exit 1
fi

OUTPUT_FILE="${INPUT_DIR}/${OUTPUT_NAME}.pdf"

ENGINE="auto"
TARGET_DPI=$DEFAULT_DPI
JOBS=$DEFAULT_JOBS
NO_QUALITY_GATE=false
while [ $# -gt 0 ]; do
    case "$1" in
        --engine)           ENGINE="$2"; shift 2 ;;
        --dpi)              TARGET_DPI="$2"; shift 2 ;;
        --jobs)             JOBS="$2"; shift 2 ;;
        --split-columns)     SPLIT_COLUMNS=true; shift ;;
        --split-columns-all) SPLIT_COLUMNS=true; SPLIT_ALL_PAGES=true; shift ;;
        --keep-split)        KEEP_SPLIT=true; shift ;;
        --no-quality-gate)  NO_QUALITY_GATE=true; shift ;;
        *) echo "⚠️  Unbekannte Option: $1"; shift ;;
    esac
done

case "$ENGINE" in
    auto|apple|tesseract) ;;
    *) echo "❌ --engine muss 'auto', 'apple' oder 'tesseract' sein"; exit 1 ;;
esac

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
echo "📸 Schritt 1/4: Bilder suchen..."

shopt -s nullglob nocaseglob
IMAGES_RAW=(*.jpg *.jpeg *.png *.tiff *.tif)
shopt -u nullglob nocaseglob

IMAGES=()
if [ ${#IMAGES_RAW[@]} -gt 0 ]; then
    while IFS= read -r line; do IMAGES+=("$line"); done \
        < <(printf '%s\n' "${IMAGES_RAW[@]}" | sort -V)
fi

if [ ${#IMAGES[@]} -gt 0 ]; then
    echo "   Gefunden: ${#IMAGES[@]} Bilder"
    img2pdf "${IMAGES[@]}" -o "$WORK_DIR/from_images.pdf"
    echo "   ✅ Bilder zu PDF konvertiert"
else
    echo "   Keine Bilder gefunden"
fi

# ── Step 2: Collect + Merge PDFs ──
echo ""
echo "📑 Schritt 2/4: PDFs zusammenführen..."

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
    echo "❌ Keine Bilder oder PDFs gefunden"; exit 1
fi

if [ ${#PDF_LIST[@]} -eq 1 ]; then
    cp "${PDF_LIST[0]}" "$WORK_DIR/combined.pdf"
else
    qpdf --empty --pages "${PDF_LIST[@]}" -- "$WORK_DIR/combined.pdf"
fi
echo "   ✅ Zusammengeführt (${#PDF_LIST[@]} Datei(en))"

# ── Step 2.5: Fix oversized MediaBox (Hemmer PDFs: 72 PPI → A4) ──
# Must run BEFORE downscale — oversized pages cause 140MP rasterization at 300 DPI.
PRE_OCR_FILE="$WORK_DIR/combined.pdf"
fix_mediabox "$PRE_OCR_FILE" "$WORK_DIR/fixed.pdf"
PRE_OCR_FILE="$WORK_DIR/fixed.pdf"

# ── Step 3: Downscale ──

if [ "$TARGET_DPI" -gt 0 ]; then
    echo ""
    echo "🔽 Schritt 3/4: Downscaling auf $TARGET_DPI DPI..."
    gs_downscale "$PRE_OCR_FILE" "$WORK_DIR/downscaled.pdf" "$TARGET_DPI"
    PRE_OCR_FILE="$WORK_DIR/downscaled.pdf"
else
    echo ""
    echo "⏭️  Schritt 3/4: Downscaling übersprungen"
fi

# ── Optional: Column split ──
if [ "$SPLIT_COLUMNS" = true ]; then
    split_two_column_pdf "$PRE_OCR_FILE" "$WORK_DIR/split.pdf"
    PRE_OCR_FILE="$WORK_DIR/split.pdf"
fi

# ── Step 4: OCR ──
echo ""
echo "🔤 Schritt 4/4: OCR + Optimierung..."

# ocr_args wird per Namen an build_ocr_args/run_ocr gereicht (Pass-by-Name,
# bash 3.2) — die Nutzung sieht shellcheck in pdf-lib.sh nicht.
# shellcheck disable=SC2034
ocr_args=()
if [ "$SPLIT_COLUMNS" = true ]; then
    build_ocr_args ocr_args --clean --no-rotate --no-deskew
else
    build_ocr_args ocr_args --clean
fi

OCR_OK=true
if [ "$NO_QUALITY_GATE" = true ]; then
    run_ocr "$PRE_OCR_FILE" "$OUTPUT_FILE" ocr_args
else
    alt_engine=""
    if [ "$USE_APPLE" = true ]; then alt_engine="tesseract"; else alt_engine="apple"; fi
    # ocr_with_retry returns 1 on a best-effort (quality-gate-failed) result.
    if ! ocr_with_retry "$PRE_OCR_FILE" "$OUTPUT_FILE" "$alt_engine" ocr_args; then
        echo "   ⚠️  Quality-Gate nicht bestanden — Lösche unvollständige Datei"
        rm -f "$OUTPUT_FILE"
        OCR_OK=false
    fi
fi

if [ "$OCR_OK" = false ]; then
    echo ""
    echo "❌ Kein Ergebnis — Quality-Gate nicht bestanden, keine Datei geschrieben"
    exit 1
fi

# ── Step 4.5: Re-Merge (--split-columns → reassemble half-page pairs) ──
if [ "$SPLIT_COLUMNS" = true ]; then
    echo ""
    echo "📐 Schritt 4.5/4: Re-Merge (Halbseiten → Originalformat)..."
    merge_split_pdf "$OUTPUT_FILE" "$WORK_DIR/merged_final.pdf"
    if [ -f "$WORK_DIR/merged_final.pdf" ]; then
        mv "$WORK_DIR/merged_final.pdf" "$OUTPUT_FILE"
    fi
fi

FINAL_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)

echo ""
echo "═══════════════════════════════════════════"
echo "✅ Fertig!"
echo "═══════════════════════════════════════════"
echo "📄 Ausgabe:  $OUTPUT_FILE"
echo "🧠 Engine:   $ENGINE_DESC"
echo "📊 Vorher:   $ORIGINAL_SIZE → Nachher: $FINAL_SIZE"
echo ""
echo "💡 Öffnen: open \"$OUTPUT_FILE\""
