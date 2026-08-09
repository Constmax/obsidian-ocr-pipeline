#!/usr/bin/env bash
# Phase 0 + 1: Arbeitsverzeichnis, gitignore, beide venvs, Verifikation.
# Idempotent — mehrfaches Ausfuehren ist unschaedlich.
# Bewusst ohne 'set -u': unter dem macOS-System-Bash 3.2 bricht die Expansion
# eines leeren Arrays (${#CANDIDATES[@]}) damit ab. Pruefungen stehen explizit.
set -o pipefail

VAULT_ROOT="${VAULT_ROOT:-/Users/constantinjanz/JuraExamenVault}"
BENCH="$VAULT_ROOT/.ocr-bench"

# Sicherung: es wird spaeter rm -rf auf die venv-Pfade angewandt. Ohne
# plausiblen VAULT_ROOT lieber abbrechen als im Wurzelverzeichnis landen.
case "$VAULT_ROOT" in
    /|""|/Users|/Users/*/..) echo "!! unplausibler VAULT_ROOT: '$VAULT_ROOT'"; exit 1 ;;
esac
[ -d "$VAULT_ROOT/raw" ] && [ -d "$VAULT_ROOT/wiki" ] || {
    echo "!! '$VAULT_ROOT' sieht nicht wie der Vault aus (raw/ + wiki/ fehlen)"; exit 1; }
cd "$VAULT_ROOT" || exit 1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ─────────────────────────────────────────────────────────── Phase 0
say "Phase 0: gitignore"
for entry in ".ocr-bench/" ".venv-paddleocr/" ".venv-mlxocr/"; do
    if grep -qxF "$entry" .gitignore; then
        echo "   schon drin: $entry"
    else
        printf '%s\n' "$entry" >> .gitignore
        echo "   ergaenzt:   $entry"
    fi
done

mkdir -p "$BENCH/out-A" "$BENCH/out-B"

say "Phase 0b: Benchmark-Set aus raw/ erzeugen"
if [ -f "$BENCH/04-einspaltig-sauber.png" ]; then
    echo "   liegt schon vor — uebersprungen (loeschen zum Neubauen)"
else
    python3 "$BENCH/build_bench.py" || echo "   ⚠ Benchmark-Set unvollstaendig"
fi
# Seitenklassifikation aus dem Vault-Root einsammeln, falls dort gelandet
[ -f "$VAULT_ROOT/pages.json" ] && mv "$VAULT_ROOT/pages.json" "$BENCH/pages.json" \
    && echo "   pages.json → .ocr-bench/"

# ─────────────────────────────────────────────────────── Python-Wahl
say "Phase 1a: Python-Kandidaten"
CANDIDATES=()
for p in python3.12 python3.11 python3.13 python3; do
    path=$(command -v "$p" 2>/dev/null) || continue
    ver=$("$p" -c 'import sys; print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
    echo "   $p → $ver ($path)"
    CANDIDATES+=("$p")
done
[ ${#CANDIDATES[@]} -eq 0 ] && { echo "!! kein python3 gefunden"; exit 1; }

# Praeferenz: 3.12 → 3.11 → 3.13. paddlepaddle-Wheels fuer macOS arm64
# existieren fuer 3.9-3.13; 3.12 ist der konservative Mittelweg.

# ───────────────────────────────────────────────── Pfad A: PaddlePaddle
say "Phase 1b: Pfad A — PaddlePaddle CPU"
PADDLE_OK=0
for py in "${CANDIDATES[@]}"; do
    ver=$("$py" -c 'import sys; print("%d.%d"%sys.version_info[:2])')
    echo "   Versuch mit $py ($ver)"
    rm -rf "$VAULT_ROOT/.venv-paddleocr"
    "$py" -m venv "$VAULT_ROOT/.venv-paddleocr" || { echo "   venv fehlgeschlagen"; continue; }
    # shellcheck disable=SC1091
    source "$VAULT_ROOT/.venv-paddleocr/bin/activate"
    python -m pip install -q --upgrade pip
    # Nicht in eine Pipe schreiben — sonst maskiert tail den Exit-Code von pip.
    if python -m pip install paddlepaddle==3.2.1 \
            -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
            > "$BENCH/install-paddle-$ver.log" 2>&1; then
        if python -c "import paddle" 2>/dev/null; then
            echo "   ✓ paddlepaddle importiert unter Python $ver"
            PADDLE_PY="$ver"; PADDLE_OK=1
            deactivate; break
        fi
        echo "   ✗ installiert, aber 'import paddle' scheitert"
    else
        echo "   ✗ pip-Install fehlgeschlagen (Log: install-paddle-$ver.log)"
        tail -4 "$BENCH/install-paddle-$ver.log" | sed 's/^/      /'
    fi
    deactivate
done

if [ "$PADDLE_OK" = 1 ]; then
    # shellcheck disable=SC1091
    source "$VAULT_ROOT/.venv-paddleocr/bin/activate"
    echo "   installiere paddleocr[doc-parser] ..."
    python -m pip install -q -U "paddleocr[doc-parser]" 2>&1 | tail -5
    echo "   --- paddle.utils.run_check() ---"
    python -c "import paddle; paddle.utils.run_check()" 2>&1 | tail -8
    echo "   --- PaddleOCRVL importierbar? ---"
    python -c "from paddleocr import PaddleOCRVL; print('   ✓ PaddleOCRVL ok')" 2>&1 | tail -5
    deactivate
else
    echo "   ⚠ Pfad A auf dieser Maschine nicht installierbar → Gate 1 fuer A verfehlt"
fi

# ──────────────────────────────────────────────────────── Pfad B: MLX
say "Phase 1c: Pfad B — MLX"
MLX_PY="${CANDIDATES[0]}"
rm -rf "$VAULT_ROOT/.venv-mlxocr"
"$MLX_PY" -m venv "$VAULT_ROOT/.venv-mlxocr"
# shellcheck disable=SC1091
source "$VAULT_ROOT/.venv-mlxocr/bin/activate"
python -m pip install -q --upgrade pip
# Alle Laufzeit-Abhaengigkeiten aus einer Quelle: requirements.txt fuehrt
# neben mlx-vlm auch pymupdf (importiert als `fitz`), das pdf2md.py braucht —
# nur mlx-vlm zu installieren liesse den Lauf am ersten `import fitz`
# scheitern. Einfach als "was man fuer Stufe 2 braucht" lesbar.
python -m pip install -q -U -r "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/requirements.txt" 2>&1 | tail -5
python - <<'PY' 2>&1 | tail -5
try:
    import mlx_vlm, mlx.core as mx
    print(f"   ✓ mlx-vlm {mlx_vlm.__version__ if hasattr(mlx_vlm,'__version__') else '?'}")
    print(f"   ✓ MLX-Metal verfuegbar: {mx.default_device()}")
except Exception as e:
    print(f"   ✗ {type(e).__name__}: {e}")
PY
deactivate

say "Gate 1 — Ergebnis"
[ "$PADDLE_OK" = 1 ] && echo "   Pfad A: OK (Python ${PADDLE_PY:-?})" || echo "   Pfad A: FEHLGESCHLAGEN"
echo "   Pfad B: siehe oben"
echo
echo "Naechster Schritt: run_bench.py (Phase 2 Smoke-Test)"
