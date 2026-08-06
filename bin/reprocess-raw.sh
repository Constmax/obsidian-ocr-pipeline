#!/bin/bash
# ============================================================
# reprocess-raw.sh — Safely re-process an EXISTING raw/ PDF in place.
#
# Runs the full pdf-combine pipeline on a copy of the source file and
# overwrites the original ONLY if both hold:
#   1. Seitenzahl bleibt exakt erhalten (kein stiller Halbseiten-Bug)
#   2. jede Seite hat mindestens --min-chars Zeichen (B5-Gate — ein
#      dokumentweiter Zeichen-Durchschnitt kann eine einzelne komplett
#      textlose Seite verstecken, siehe BUGREPORT-2026-07-06-split-merge.md)
#
# Bei Fehlschlag bleibt die Quelldatei unverändert; das fehlerhafte
# Ergebnis wird zur Inspektion daneben abgelegt (<name>_FAILED_*.pdf).
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

PDF_COMBINE="$HOME/bin/pdf-combine"
if [ ! -x "$PDF_COMBINE" ]; then
    PDF_COMBINE="$SCRIPT_DIR/pdf-combine.sh"
fi

if [ $# -lt 1 ]; then
    cat <<EOF
Usage: $(basename "$0") <raw-pdf-datei> [pdf-combine-Optionen] [--min-chars N] [--allow-pages LISTE]

Verarbeitet eine bestehende raw/-PDF-Datei mit der aktuellen Pipeline neu
und überschreibt sie NUR nach bestandener B5-Verifikation:
   1. Seitenzahl exakt erhalten
   2. jede Seite >= --min-chars Zeichen (Default: 50)

Optionen:
   --min-chars N        Mindest-Zeichen pro Seite (Default: 50)
   --allow-pages LISTE  Seiten von Check 2 ausnehmen, z.B. "1,5-7"
                        (bekannte Deckblatt-/Grafik-Seiten ohne Fließtext)

Alle anderen Flags werden 1:1 an pdf-combine durchgereicht
(z.B. --engine, --split-columns, --force-ocr, --dpi).

Beispiel:
   $(basename "$0") "raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" --force-ocr --split-columns
EOF
    exit 1
fi

SRC="$1"; shift
if [ ! -f "$SRC" ]; then
    echo "❌ Datei nicht gefunden: $SRC"; exit 1
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
    echo "❌ Kann Seitenzahl von $SRC_ABS nicht ermitteln"; exit 1
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cp "$SRC_ABS" "$WORK_DIR/"
# Output-Name bewusst != Input-Basisname: pdf-combine schließt sonst die
# Quelldatei selbst aus der PDF-Liste aus (Kollisions-Guard gegen
# Endlosschleifen bei Re-Runs) und meldet "Keine PDFs gefunden".
OUTNAME="${BASE}_reprocessed"

echo "🔄 Reprocessing: $SRC_ABS ($ORIG_PAGES Seiten)"
if ! "$PDF_COMBINE" "$WORK_DIR" "$OUTNAME" "${COMBINE_ARGS[@]}"; then
    echo "❌ pdf-combine fehlgeschlagen — $SRC_ABS bleibt unverändert"
    exit 1
fi

OUT="$WORK_DIR/${OUTNAME}.pdf"
if [ ! -f "$OUT" ]; then
    echo "❌ Kein Output erzeugt — $SRC_ABS bleibt unverändert"
    exit 1
fi

NEW_PAGES=$(pdfinfo "$OUT" 2>/dev/null | awk '/^Pages:/ {print $2}')
if [ "$NEW_PAGES" != "$ORIG_PAGES" ]; then
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_pagecount.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "❌ Seitenzahl-Mismatch (Original: $ORIG_PAGES, Neu: $NEW_PAGES) — $SRC_ABS bleibt unverändert"
    echo "   Ergebnis zur Prüfung abgelegt: $FAILED_OUT (danach löschen)"
    exit 1
fi

echo "📋 B5-Gate: prüfe Zeichen/Seite (min: $MIN_CHARS)..."
PYTHON_BIN=""
for candidate in "${VENV_ROOT:-$HOME/.venvs}/ocrmypdf/bin/python3" "python3"; do
    if "$candidate" -c "import pikepdf" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "⚠️  pikepdf nicht gefunden — B5-Gate kann nicht geprüft werden, breche sicherheitshalber ab"
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_noverify.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "   Ergebnis zur manuellen Prüfung abgelegt: $FAILED_OUT"
    exit 1
fi

VERIFY_ARGS=(verify-pages "$OUT" --min-chars "$MIN_CHARS")
if [ -n "$ALLOW_PAGES" ]; then
    VERIFY_ARGS+=(--allow-pages "$ALLOW_PAGES")
fi

if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" "${VERIFY_ARGS[@]}"; then
    FAILED_OUT="${SRC_ABS%.pdf}_FAILED_pages.pdf"
    cp "$OUT" "$FAILED_OUT"
    echo "❌ B5-Gate fehlgeschlagen (siehe Seiten oben) — $SRC_ABS bleibt unverändert"
    echo "   Ergebnis zur Prüfung abgelegt: $FAILED_OUT (danach löschen, oder --allow-pages nachreichen)"
    exit 1
fi

cp "$OUT" "$SRC_ABS"
echo "✅ Überschrieben: $SRC_ABS ($NEW_PAGES Seiten, B5-Gate bestanden)"
