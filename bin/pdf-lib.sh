#!/bin/bash
# ============================================================
# pdf-lib.sh — Shared library for pdf-auto, pdf-workflow, pdf-combine
# ============================================================
# Source this file in the other scripts:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/pdf-lib.sh"
#
# Provides:
#   - Defaults & constants
#   - Common option parsing
#   - Dependency + engine detection
#   - Ghostscript downscaling (Bicubic)
#   - Two-column page splitting (CropBox)
#   - OCR args construction
#   - Post-OCR quality gate with auto-retry
#   - run_pdf_pipeline: the merge-to-OCR sequence all three CLIs share
# ============================================================

# ── Defaults ────────────────────────────────────────────────
DEFAULT_DPI=300          # Tesseract sweet spot; was 200 — too low for fine print
DEFAULT_JOBS=2
FAST_DPI=200             # --fast floor: never below 200 with tesseract
FAST_JOBS=1
DOWNSAMPLE_TYPE="Bicubic"  # was /Subsample — smears text edges
A4_MAX_W=650             # Max page width in pts before MediaBox fix (A4=595 + 10 %)
A4_MAX_H=900             # Max page height in pts before MediaBox fix (A4=842 + 7 %)
MAX_IMAGE_MPIXELS=400    # ocrmypdf --max-image-mpixels safety net (Default 250) for inputs without fix_mediabox

# ── Global state (set by callers or detect_*) ───────────────
APPLE_AVAILABLE=false
PADDLE_AVAILABLE=false
PADDLE_PROBLEM=""         # why an installed PaddleOCR cannot run (set by detect_paddle_ocr)
RESOLVED_ENGINE=""        # apple|tesseract|paddle — the one engine state (set by resolve_engine)
ENGINE_DESC=""            # RESOLVED_ENGINE for humans, e.g. "Apple Vision (auto)"
OCR_RESULT_DESC=""        # Engine behind the last OCR result, with fallback (set by ocr_with_retry)
OCR_FALLBACK=false        # true when the last OCR result came from a fallback engine
FALLBACK_COUNT=0          # Results from a fallback engine in this run (pdf-auto summary)
HAS_JBIG2=false
HAS_PNGQUANT=false
HAS_UNPAPER=false
OPTIMIZE_LEVEL=1
ENGINE="auto"             # --engine flag
PADDLE_MODE=""            # --paddle-mode flag (accurate|fast; only with --engine paddle)
TARGET_DPI=$DEFAULT_DPI
DPI_SET=false             # true once --dpi was given (even with the default value)
JOBS=$DEFAULT_JOBS
JOBS_SET=false            # true once --jobs was given (even with the default value)
NO_QUALITY_GATE=false     # --no-quality-gate flag
SPLIT_COLUMNS=false       # --split-columns flag
SPLIT_ALL_PAGES=false     # --split-columns-all flag (force --all instead of per-page --auto)
KEEP_SPLIT=false          # --keep-split flag (suppress merge)
SPLIT_MAP=""              # Path to split_map.json (set by split_two_column_pdf)
PYTHON_BIN=""             # Python with pikepdf (set by lib_init)
PIPELINE_PAGES=""         # Page count of the merged input (set by run_pdf_pipeline)

# ════════════════════════════════════════════════════════════
#  OPTION PARSING
# ════════════════════════════════════════════════════════════

# usage_error <message>
# Prints a one-line usage error to stderr and exits 1.
usage_error() {
    echo "❌ $1" >&2
    echo "   Run $(basename "$0") without arguments for usage." >&2
    exit 1
}

# require_option_value "$@"
# Exits with a usage error unless option $1 is followed by a value.
require_option_value() {
    if [ $# -lt 2 ] || [ -z "$2" ] || [ "${2#--}" != "$2" ]; then
        usage_error "$1 needs a value"
    fi
}

# parse_common_option "$@"
# Parses the option at $1 if it is shared by all three CLIs, validates its
# value and sets OPTION_SHIFT to the number of arguments consumed. Any other
# option is a usage error, so call this from the CLI's fallback case branch:
#   *) parse_common_option "$@"; shift "$OPTION_SHIFT" ;;
# ENGINE, PADDLE_MODE, NO_QUALITY_GATE and OPTION_SHIFT are read by the CLIs — usage is
# not seen by shellcheck in pdf-lib.sh.
# shellcheck disable=SC2034
parse_common_option() {
    OPTION_SHIFT=1
    case "$1" in
        --engine)
            require_option_value "$@"
            case "$2" in
                auto|apple|tesseract|paddle) ENGINE="$2" ;;
                *) usage_error "--engine must be 'auto', 'apple', 'tesseract', or 'paddle', got '$2'" ;;
            esac
            OPTION_SHIFT=2 ;;
        --paddle-mode)
            require_option_value "$@"
            case "$2" in
                accurate|fast) PADDLE_MODE="$2" ;;
                *) usage_error "--paddle-mode must be 'accurate' or 'fast', got '$2'" ;;
            esac
            OPTION_SHIFT=2 ;;
        --dpi)
            require_option_value "$@"
            case "$2" in
                *[!0-9]*) usage_error "--dpi must be a whole number (0 = off), got '$2'" ;;
            esac
            TARGET_DPI=$((10#$2)); DPI_SET=true
            OPTION_SHIFT=2 ;;
        --jobs)
            require_option_value "$@"
            case "$2" in
                *[!0-9]*) usage_error "--jobs must be a positive whole number, got '$2'" ;;
            esac
            if [ $((10#$2)) -lt 1 ]; then
                usage_error "--jobs must be a positive whole number, got '$2'"
            fi
            JOBS=$((10#$2)); JOBS_SET=true
            OPTION_SHIFT=2 ;;
        --split-columns)     SPLIT_COLUMNS=true ;;
        --split-columns-all) SPLIT_COLUMNS=true; SPLIT_ALL_PAGES=true ;;
        --keep-split)        KEEP_SPLIT=true ;;
        --no-quality-gate)   NO_QUALITY_GATE=true ;;
        *) usage_error "Unknown option: $1" ;;
    esac
}

# ════════════════════════════════════════════════════════════
#  DETECTION FUNCTIONS
# ════════════════════════════════════════════════════════════

# check_deps <tool1> <tool2> ...
# Verifies required tools exist; exits with message if not.
check_deps() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "❌ Missing tools: ${missing[*]}" >&2
        return 1
    fi
    return 0
}

# detect_apple_ocr
# Sets APPLE_AVAILABLE=true if the Apple Vision plugin is usable.
detect_apple_ocr() {
    APPLE_AVAILABLE=false
    if ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1; then
        APPLE_AVAILABLE=true
    fi
}

# detect_paddle_ocr
# Sets PADDLE_AVAILABLE=true if the PaddleOCR engine plugin (ocrmypdf_paddle)
# loads and `--paddle-check` reports its runtime, models and (fast mode)
# Apple Vision ready for PADDLE_MODE. A plugin that loads but is not ready
# leaves the reason in PADDLE_PROBLEM, so a run fails before OCR instead of
# quietly falling back to another engine (issue #73).
detect_paddle_ocr() {
    PADDLE_AVAILABLE=false
    PADDLE_PROBLEM=""
    if ! ocrmypdf --plugin ocrmypdf_paddle --help >/dev/null 2>&1; then
        return 0
    fi
    local rc=0
    PADDLE_PROBLEM="$(ocrmypdf --plugin ocrmypdf_paddle \
        --paddle-check "${PADDLE_MODE:-accurate}" 2>&1 >/dev/null)" || rc=$?
    case "$rc" in
        0) PADDLE_AVAILABLE=true; PADDLE_PROBLEM="" ;;
        # argparse: an ocrmypdf_paddle from before --paddle-check.
        2) PADDLE_PROBLEM="The ocrmypdf_paddle that ocrmypdf loads is too old for this pipeline (no --paddle-check).
   Update it: git pull in the checkout of an editable install, or pip install <repo>/ocrmypdf_paddle (docs/installation.md)" ;;
        *) [ -n "$PADDLE_PROBLEM" ] || PADDLE_PROBLEM="PaddleOCR engine readiness check failed" ;;
    esac
    return 0
}

# engine_label <apple|tesseract|paddle>
# Prints the engine's name for messages and summaries.
engine_label() {
    case "$1" in
        apple)     echo "Apple Vision" ;;
        tesseract) echo "Tesseract" ;;
        paddle)    echo "PaddleOCR ${PADDLE_MODE:-accurate}" ;;
    esac
}

# resolve_engine <auto|apple|tesseract|paddle>
# The single source of truth for the engine (issue #70): sets
# RESOLVED_ENGINE (apple|tesseract|paddle) and ENGINE_DESC. `auto` prefers
# Apple Vision and falls back to Tesseract; PaddleOCR is never chosen by
# `auto`. Returns 1 if the requested engine is not installed.
# ENGINE_DESC is read by the CLIs — usage is not seen by shellcheck here.
# shellcheck disable=SC2034
resolve_engine() {
    local engine="${1:-auto}"
    case "$engine" in
        auto)
            if [ "$APPLE_AVAILABLE" = true ]; then
                RESOLVED_ENGINE=apple
                ENGINE_DESC="Apple Vision (auto)"
            else
                RESOLVED_ENGINE=tesseract
                ENGINE_DESC="Tesseract (auto-fallback)"
            fi ;;
        apple)
            if [ "$APPLE_AVAILABLE" != true ]; then
                echo "❌ Apple Vision plugin not installed." >&2
                echo "   Install: pip install ocrmypdf-appleocr" >&2
                return 1
            fi
            RESOLVED_ENGINE=apple
            ENGINE_DESC="Apple Vision (manual)" ;;
        tesseract)
            RESOLVED_ENGINE=tesseract
            ENGINE_DESC="Tesseract (manual)" ;;
        paddle)
            if [ -n "$PADDLE_PROBLEM" ]; then
                echo "❌ $PADDLE_PROBLEM" >&2
                return 1
            fi
            if [ "$PADDLE_AVAILABLE" != true ]; then
                echo "❌ PaddleOCR engine plugin not installed (ocrmypdf_paddle)." >&2
                echo "   Put its venv first on PATH, or PYTHONPATH=<repo>/ocrmypdf_paddle/src: docs/installation.md" >&2
                return 1
            fi
            RESOLVED_ENGINE=paddle
            ENGINE_DESC="$(engine_label paddle) (manual)" ;;
        *)
            echo "❌ --engine must be 'auto', 'apple', 'tesseract', or 'paddle'" >&2
            return 1 ;;
    esac
    return 0
}

# fallback_engine <apple|tesseract|paddle>
# Prints the engine the quality gate switches to after <engine> failed:
# Apple Vision → Tesseract, Tesseract → Apple Vision, PaddleOCR → Apple
# Vision or, without it, Tesseract. Prints nothing if that is not installed.
fallback_engine() {
    case "$1" in
        apple) echo tesseract ;;
        tesseract)
            if [ "$APPLE_AVAILABLE" = true ]; then echo apple; fi ;;
        paddle)
            if [ "$APPLE_AVAILABLE" = true ]; then echo apple; else echo tesseract; fi ;;
    esac
    return 0
}

# detect_optimizers
# Sets HAS_JBIG2, HAS_PNGQUANT, HAS_UNPAPER, OPTIMIZE_LEVEL.
detect_optimizers() {
    HAS_JBIG2=false; HAS_PNGQUANT=false; HAS_UNPAPER=false
    command -v jbig2    >/dev/null 2>&1 && HAS_JBIG2=true
    command -v pngquant >/dev/null 2>&1 && HAS_PNGQUANT=true
    command -v unpaper  >/dev/null 2>&1 && HAS_UNPAPER=true

    if [ "$HAS_PNGQUANT" = true ] && [ "$HAS_JBIG2" = true ]; then
        OPTIMIZE_LEVEL=3
    elif [ "$HAS_PNGQUANT" = true ]; then
        OPTIMIZE_LEVEL=2
    else
        OPTIMIZE_LEVEL=1
    fi
}

# detect_safe_jobs
# Sets JOBS based on available RAM to prevent OOM on low-memory Macs.
# Called by lib_init when neither --jobs nor --fast chose JOBS.
detect_safe_jobs() {
    local ram_gb
    ram_gb=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1073741824)}')
    if [ -z "$ram_gb" ] || [ "$ram_gb" -le 8 ]; then
        JOBS=1    # 8 GB: serial OCR — avoids swap thrashing
    elif [ "$ram_gb" -le 16 ]; then
        JOBS=2
    else
        JOBS=4
    fi
}

# ════════════════════════════════════════════════════════════
#  PRE-OCR PROCESSING
# ════════════════════════════════════════════════════════════

# fix_mediabox <input.pdf> <output.pdf>
# Corrects page MediaBox from pixel dimensions (72 PPI metadata) to A4.
# Problem: Some PDFs have MediaBox set to image pixel dimensions
# (e.g. 2439×3413 pts), which means ocrmypdf rasterizes at
# insane sizes (144 MP at 300 DPI). This fixes them to A4.
# Returns 0; output is guaranteed to exist (copy if no fix needed).
fix_mediabox() {
    local input="$1" output="$2"
    local w h

    read -r w h < <(pdfinfo "$input" 2>/dev/null | awk '/^Page size:/ {gsub(/pts/, "", $3); gsub(/pts/, "", $5); print int($3), int($5)}')
    if [ -z "$w" ] || [ -z "$h" ]; then
        cp "$input" "$output"
        return 0
    fi

    if [ "$w" -le "$A4_MAX_W" ] && [ "$h" -le "$A4_MAX_H" ]; then
        # Already A4-compliant — no fix needed
        cp "$input" "$output"
        return 0
    fi

    echo "   📐 MediaBox fix: ${w}×${h} pts → A4 (595×842 pts)"

    gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.7 \
       -dNOPAUSE -dQUIET -dBATCH \
       -dPDFFitPage \
       -dDEVICEWIDTHPOINTS=595 -dDEVICEHEIGHTPOINTS=842 \
       -sOutputFile="$output" \
       "$input" 2>/dev/null

    if [ ! -f "$output" ]; then
        echo "   ⚠️  MediaBox fix failed, continuing with original"
        cp "$input" "$output"
    fi
    return 0
}

# gs_downscale <input.pdf> <output.pdf> [dpi]
# Downscales a PDF using Ghostscript with Bicubic resampling.
# Returns 0 on success; output path is guaranteed to exist on success.
gs_downscale() {
    local input="$1" output="$2" dpi="${3:-$TARGET_DPI}"
    local size_before size_after

    if [ "$dpi" -le 0 ]; then
        cp "$input" "$output"
        return 0
    fi

    size_before=$(du -h "$input" 2>/dev/null | cut -f1)

    gs -sDEVICE=pdfwrite \
       -dCompatibilityLevel=1.4 \
       -dNOPAUSE -dQUIET -dBATCH \
       -dDownsampleColorImages=true \
       -dColorImageResolution="$dpi" \
       -dColorImageDownsampleType=/"$DOWNSAMPLE_TYPE" \
       -dDownsampleGrayImages=true \
       -dGrayImageResolution="$dpi" \
       -dGrayImageDownsampleType=/"$DOWNSAMPLE_TYPE" \
       -dDownsampleMonoImages=true \
       -dMonoImageResolution="$dpi" \
       -dMonoImageDownsampleType=/"$DOWNSAMPLE_TYPE" \
       -sOutputFile="$output" \
       "$input" 2>/dev/null

    if [ -f "$output" ]; then
        size_after=$(du -h "$output" 2>/dev/null | cut -f1)
        echo "   🔽 Downscaling to $dpi DPI ($DOWNSAMPLE_TYPE): $size_before → $size_after"
        return 0
    else
        echo "   ⚠️  Downscaling failed, continuing with original"
        cp "$input" "$output"
        return 0  # non-fatal
    fi
}

# Deskews all pages of a PDF using ocrmypdf with a 0-second OCR timeout.
# This performs image deskewing but skips embedding a text layer.

# split_two_column_pdf <input.pdf> <output.pdf>
# Splits pages vertically via column_tools.py.
# Default: --auto (per-page detection — only genuinely two-column pages
# are split; single-column pages in a mixed document pass through
# untouched). Set SPLIT_ALL_PAGES=true (via --split-columns-all) to force
# every page through the splitter, as an override if detection misfires.
# Writes split_map.json to $SPLIT_MAP for later reassembly.
# Returns 1 unless both the split PDF and its map were written. There is
# deliberately no fallback: halves without a map cannot be merged back and
# would silently double the page count (issue #47). lib_init already refuses
# --split-columns without pikepdf; this guard covers the internal retry.
split_two_column_pdf() {
    local input="$1" output="$2"
    SPLIT_MAP="${input%.pdf}_split_map.json"

    if [ -z "$PYTHON_BIN" ] || [ ! -f "$SCRIPT_DIR/column_tools.py" ]; then
        echo "   ❌ Column split requires pikepdf and column_tools.py" >&2
        SPLIT_MAP=""
        return 1
    fi

    local mode_flag="--auto"
    if [ "$SPLIT_ALL_PAGES" = true ]; then
        mode_flag="--all"
    fi
    # A stale map from an earlier group must not pass for this split's map.
    rm -f "$output" "$SPLIT_MAP"
    # Guarded: report a failing split instead of dying silently under `set -e`.
    if "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" split "$input" "$output" \
        --map "$SPLIT_MAP" "$mode_flag" 2>&1 \
        && [ -f "$output" ] && [ -f "$SPLIT_MAP" ]; then
        return 0
    fi
    echo "   ❌ column_tools.py split failed" >&2
    rm -f "$output" "$SPLIT_MAP"
    SPLIT_MAP=""
    return 1
}

# merge_split_pdf <ocr.pdf> <output.pdf>
# Reassembles half-page pairs back to original full-page format.
# Uses the split_map.json from split_two_column_pdf.
# With --keep-split the split version is copied through on purpose.
# Otherwise returns 1 if the merge cannot run or fails — never falls back to
# the split version, which would silently double the page count (issue #47).
merge_split_pdf() {
    local input="$1" output="$2"

    if [ "$KEEP_SPLIT" = true ]; then
        echo "   ⏭️  Re-merge skipped (--keep-split)"
        [ "$input" != "$output" ] && cp "$input" "$output"
        return 0
    fi

    if [ -z "$SPLIT_MAP" ] || [ ! -f "$SPLIT_MAP" ]; then
        echo "   ❌ Re-merge impossible: split map missing" >&2
        return 1
    fi
    if [ -z "$PYTHON_BIN" ] || [ ! -f "$SCRIPT_DIR/column_tools.py" ]; then
        echo "   ❌ Re-merge requires pikepdf and column_tools.py" >&2
        return 1
    fi

    # Guarded: a failing merge (corrupt map, unreadable input) must be
    # reported to the caller, not kill it silently under `set -e`.
    if "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" merge "$input" "$output" \
        --map "$SPLIT_MAP" 2>&1 && [ -f "$output" ]; then
        return 0
    fi
    echo "   ❌ column_tools.py merge failed" >&2
    rm -f "$output"
    return 1
}

# ════════════════════════════════════════════════════════════
#  OCR EXECUTION
# ════════════════════════════════════════════════════════════

# build_ocr_args <output_var_name> [--engine E] [--force-ocr] [--clean] [--no-rotate] [--no-deskew]
# Builds the ocrmypdf argument array for engine E (default RESOLVED_ENGINE).
# Sets the variable named by $1 to the array of arguments.
build_ocr_args() {
    local outvar="$1"; shift
    local engine="$RESOLVED_ENGINE"
    local use_clean=false
    local no_rotate=false
    local no_deskew=false
    local skip_text="--skip-text"
    while [ $# -gt 0 ]; do
        case "$1" in
            --engine)    engine="$2"; shift 2 ;;
            --force-ocr) skip_text="--force-ocr"; shift ;;
            --clean)     use_clean=true; shift ;;
            --no-rotate) no_rotate=true; shift ;;
            --no-deskew) no_deskew=true; shift ;;
            *) shift ;;
        esac
    done

    local args=(
        -l deu
        "$skip_text"
    )
    # --rotate-pages relies on per-page OSD confidence; unreliable on
    # column halves (own orientation detection is unreliable — see
    # "confidence too low to rotate" warnings observed on split pages) and
    # mismatched rotation between the two halves would break the merge.
    if [ "$no_rotate" = false ]; then
        args+=(--rotate-pages)
    fi
    if [ "$no_deskew" = false ]; then
        args+=(--deskew)
    fi
    # PaddleOCR runs one OCR job: parallel workers each load the models.
    local jobs="$JOBS"
    [ "$engine" = paddle ] && jobs=1
    args+=(
        --optimize "$OPTIMIZE_LEVEL"
        --jobs "$jobs"
        --max-image-mpixels "$MAX_IMAGE_MPIXELS"
    )

    case "$engine" in
        apple)
            args=(--plugin ocrmypdf_appleocr "${args[@]}") ;;
        paddle)
            args=(--plugin ocrmypdf_paddle --paddle-mode "${PADDLE_MODE:-accurate}" "${args[@]}") ;;
        tesseract)
            args+=(--tesseract-pagesegmode 1)
            if [ "$use_clean" = true ] && [ "$HAS_UNPAPER" = true ]; then
                args+=(--clean)
            fi ;;
    esac

    # Indirect assignment: set the caller's variable
    printf -v "$outvar" '%s ' "${args[@]}"
    eval "$outvar=(${!outvar})"
}

# run_ocr <input.pdf> <output.pdf> <ocr-args-array-name>
# Runs ocrmypdf with the given arguments.
# The array is passed by name for bash 3.2 compatibility.
# --max-image-mpixels is baked into the args by build_ocr_args — generous for
# edge cases; after fix_mediabox, A4@300 DPI ≈ 8.7 MP/page.
# Returns 0 on success, non-zero on failure.
run_ocr() {
    local input="$1" output="$2" array_name="$3"
    eval ocrmypdf "\"\${${array_name}[@]}\"" "\"\$input\"" "\"\$output\""
}

# ════════════════════════════════════════════════════════════
#  QUALITY GATE (Post-OCR)
# ════════════════════════════════════════════════════════════

# quality_check <pdf> [--threshold N]
# Checks OCR quality of a PDF. Exits 0 if OK, 1 if garbage.
# Metrics:
#   1. chars/page ≥ threshold (default 200)
#   2. garbage_score < 0.40 (column-mixing / symbol corruption)
quality_check() {
    local pdf="$1" threshold="${2:-200}"
    local text chars pages chars_per_page

    # -raw: stream order, not Poppler's reconstructed reading order. For
    # split+merged two-column pages, default-mode pdftotext frequently
    # interleaves the two columns line-by-line even though the text layer
    # is geometrically correct — Poppler's column-clustering heuristic
    # doesn't reliably recognize our reconstructed layout. -raw instead
    # follows the content-stream emission order, which merge_pdf()
    # guarantees is left-column-then-right-column (see column_tools.py).
    # The garbage heuristic below is per-word and order-independent either
    # way, but -raw is the representative choice for what a human actually
    # gets when reading these files.
    text=$(pdftotext -raw "$pdf" - 2>/dev/null || true)
    chars=$(printf '%s' "$text" | wc -c | tr -d ' ')
    pages=$(pdfinfo "$pdf" 2>/dev/null | awk '/^Pages:/ {print $2}')

    if [ -z "$pages" ] || [ "$pages" -eq 0 ]; then
        echo "   🔍 Quality: Cannot determine page count → evaluated as OK"
        return 0
    fi

    chars_per_page=$((chars / pages))

    # ── Metric 1: raw character density ──
    if [ "$chars_per_page" -lt "$threshold" ]; then
        echo "   🗑️  Quality-FAIL: Only $chars_per_page chars/page (min: $threshold)"
        return 1
    fi
    echo "   📊 Quality: $chars_per_page chars/page ✓"

    # ── Metric 1.5: Split-specific text loss check ──
    if [ -n "$SPLIT_MAP" ] && [ -f "$SPLIT_MAP" ] && [ -n "$PYTHON_BIN" ] && [ -f "$SCRIPT_DIR/column_tools.py" ]; then
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" verify "$pdf" --map "$SPLIT_MAP"; then
            echo "   🗑️  Quality-FAIL: One-sided text loss on split page detected"
            return 1
        fi
    fi

    # ── Metric 2: garbage heuristic ──
    _garbage_heuristic "$text" "$pdf"
}

# _garbage_heuristic <text> <pdf_path>
# Internal: computes a garbage score from extracted text.
# Returns 0 (OK) or 1 (garbage detected).
_garbage_heuristic() {
    local text="$1" pdf="$2"

    # Word-shape analysis (isolated fragments, Binnengroßbuchstaben,
    # digit/alpha mixing) runs entirely in Python, not bash: bash's
    # `[[ =~ ]]` bracket expressions with German umlauts (ä/ö/ü/ß) are
    # unreliable — verified empirically, "für" (no uppercase at all)
    # false-matched `[a-zäöüß][A-ZÄÖÜ]` even under LC_ALL=C (likely a
    # multi-byte-UTF-8 handling issue in bash's regex engine). This
    # silently inflated the garbage score specifically on umlaut-heavy
    # legal German (Verwaltungsrecht vocabulary especially: "für",
    # "Behörde", "gemäß", "Zuverlässigkeit", "Verhältnismäßigkeit", ...),
    # causing false quality-gate failures unrelated to actual OCR quality
    # — confirmed: 417 of 529 "mixed-case" hits on one such document had
    # no uppercase letter at all. Python's per-character .islower()/
    # .isupper() are Unicode-correct with no locale dependency.
    local stats total_words isolated mixed_case digit_alpha
    stats=$(printf '%s' "$text" | python3 -c '
import sys

words = [w for w in sys.stdin.read().split() if any(c.isalpha() for c in w)]
total = len(words)
isolated = mixed_case = digit_alpha = 0
for w in words:
    if len(w) <= 2:
        isolated += 1
    if any(a.islower() and b.isupper() for a, b in zip(w, w[1:])):
        mixed_case += 1
    if any(c.isdigit() for c in w) and any(c.isalpha() for c in w):
        digit_alpha += 1
print(total, isolated, mixed_case, digit_alpha)
' 2>/dev/null)

    read -r total_words isolated mixed_case digit_alpha <<< "$stats"

    if [ -z "$total_words" ] || [ "$total_words" -lt 50 ]; then
        # Too few words to compute reliable stats — defer to metric 1
        return 0
    fi

    # Compute ratios
    local isolated_r mixed_r digit_r garbage_score
    isolated_r=$(python3 -c "print(round($isolated / $total_words, 3))" 2>/dev/null || echo "0")
    mixed_r=$(python3 -c "print(round($mixed_case / $total_words, 3))" 2>/dev/null || echo "0")
    digit_r=$(python3 -c "print(round($digit_alpha / $total_words, 3))" 2>/dev/null || echo "0")
    garbage_score=$(python3 -c "print(round(($isolated + $mixed_case * 3 + $digit_alpha * 2) / $total_words, 3))" 2>/dev/null || echo "0")

    echo "   🔬 Garbage Score: $garbage_score (iso=$isolated_r mixed=$mixed_r digit=$digit_r | $total_words words)"

    # Threshold: garbage_score > 0.40 → likely corrupted
    # Set at 0.40 (not lower) to tolerate minor OCR errors in older Hemmer scans.
    # iso-ratio > 0.40 is still caught below as column-mixing (separate check).
    if [ "$(python3 -c "print(1 if $garbage_score > 0.40 else 0)")" = "1" ]; then
        echo "   🗑️  Quality-FAIL: Garbage score $garbage_score > 0.40"
        return 1
    fi

    # Special case: extremely high isolated-char ratio (>40 %) → guaranteed column mixing
    if [ "$(python3 -c "print(1 if $isolated_r > 0.40 else 0)")" = "1" ]; then
        echo "   🗑️  Quality-FAIL: Column mixing detected (iso=$isolated_r)"
        return 1
    fi

    echo "   ✅ Quality gate passed"
    return 0
}

# _ocr_attempt <input.pdf> <output.pdf> <args_array_name>
# Internal: one OCR run plus the quality gate. Returns 0 if <output.pdf>
# passed; otherwise 1 with OCR_FAIL_REASON set. A result that failed the gate
# stays at <output.pdf> (the best effort); a failed run leaves none.
_ocr_attempt() {
    local input="$1" output="$2" args_name="$3"
    if ! run_ocr "$input" "$output" "$args_name"; then
        echo "   ❌ OCR failed"
        rm -f "$output"
        OCR_FAIL_REASON="OCR failed"
        return 1
    fi
    if quality_check "$output"; then
        return 0
    fi
    OCR_FAIL_REASON="quality gate failed"
    return 1
}

# _split_retry <input.pdf> <output.pdf> <args_array_name> <scratch_dir>
# Internal: OCR the input split into column halves with the given args and
# re-merge them. Skipped (return 1) when columns are split anyway, with
# --no-split, or without pikepdf: halves without a split map cannot be merged
# back. Writes <output.pdf> only as a merged result that passed the gate.
_split_retry() {
    local input="$1" output="$2" args_name="$3" scratch_dir="$4"
    if [ "$SPLIT_COLUMNS" = true ] || [ "$no_split" = true ] || [ -z "$PYTHON_BIN" ]; then
        return 1
    fi
    echo "   🔄 Retry with --split-columns..."
    local split_pdf="$scratch_dir/ocr_retry_split.pdf"
    local split_ocr="$scratch_dir/ocr_retry_split_ocr.pdf"
    local merged_tmp="$scratch_dir/ocr_retry_merged.pdf"
    local saved_split_map=$SPLIT_MAP

    # Strip --rotate-pages and --deskew from the caller's args (unreliable
    # per-half orientation detection would break the merge below, and deskew
    # was already done pre-split) without assuming which other flags it built in.
    local split_args=() _orig_arg
    eval "_orig_args=(\"\${${args_name}[@]}\")"
    # _orig_args was assigned via eval string (pass-by-name, bash 3.2).
    # shellcheck disable=SC2154
    for _orig_arg in "${_orig_args[@]}"; do
        [ "$_orig_arg" = "--rotate-pages" ] && continue
        [ "$_orig_arg" = "--deskew" ] && continue
        split_args+=("$_orig_arg")
    done

    local ok=false
    if ! split_two_column_pdf "$input" "$split_pdf"; then
        echo "   ❌ Column split failed"
    elif _ocr_attempt "$split_pdf" "$split_ocr" split_args; then
        # Reassemble the halves before handing the result off — otherwise
        # this internal retry silently doubles the page count, exactly like
        # the bug that corrupted several files in raw/. Unmergeable halves
        # are discarded, never handed off.
        if merge_split_pdf "$split_ocr" "$merged_tmp"; then
            mv "$merged_tmp" "$output"
            ok=true
        fi
    fi
    rm -f "$split_pdf" "$split_ocr" "$merged_tmp"
    # A failed split retry must not leave its map behind: metric 1.5 in
    # quality_check would verify the next, unsplit attempt against it and
    # report a spurious page-count mismatch.
    [ "$ok" = true ] && return 0
    SPLIT_MAP=$saved_split_map
    return 1
}

# ocr_with_retry <pre_ocr.pdf> <output.pdf> <args_array_name> [--no-split]
# Runs OCR with RESOLVED_ENGINE, checks quality and walks the fallback
# matrix on failure (issue #70):
#   Apple Vision → Tesseract → Tesseract with column split
#   Tesseract    → Tesseract with column split → Apple Vision
#   PaddleOCR    → Apple Vision, or Tesseract without it; no column split
# An engine switch rebuilds the args with --force-ocr, since the input may
# carry a text layer from an earlier attempt. Every switch is printed on
# stderr with its reason. Sets OCR_RESULT_DESC and OCR_FALLBACK for the
# summary. <args_array_name> is passed by name (bash 3.2).
# Returns 0 if the final result passes quality, 1 if all attempts fail
# (<output.pdf> then holds the best effort, if any).
#
# Scratch files are placed in $WORK_DIR (set by the caller, cleaned via its
# EXIT trap) rather than next to $output — otherwise an interrupted run can
# leave a "<output>.tmp_qc.pdf" behind, which ends in .pdf and gets picked up
# as an input on the next run.
ocr_with_retry() {
    local input="$1" output="$2" args_name="$3"
    # Read by _split_retry (bash locals are dynamically scoped).
    local no_split=false
    [ "${4:-}" = "--no-split" ] && no_split=true

    local scratch_dir="${WORK_DIR:-}"
    local own_scratch_dir=false
    if [ -z "$scratch_dir" ]; then
        scratch_dir=$(mktemp -d)
        own_scratch_dir=true
    fi
    local ocr_tmp="$scratch_dir/ocr_retry_tmp_qc.pdf"
    local engine="$RESOLVED_ENGINE" status=1
    OCR_RESULT_DESC="$ENGINE_DESC"
    OCR_FALLBACK=false
    OCR_FAIL_REASON=""

    echo "   🔤 OCR (attempt 1): $ENGINE_DESC"
    if _ocr_attempt "$input" "$ocr_tmp" "$args_name"; then
        status=0
    elif [ "$engine" = tesseract ] && _split_retry "$input" "$ocr_tmp" "$args_name" "$scratch_dir"; then
        OCR_RESULT_DESC="$ENGINE_DESC, column split retry"
        status=0
    else
        local fallback from to
        fallback=$(fallback_engine "$engine")
        from=$(engine_label "$engine")
        if [ -z "$fallback" ]; then
            echo "   ⚠️  No fallback engine: Apple Vision is not installed" >&2
        else
            to=$(engine_label "$fallback")
            echo "   🔄 Fallback: $from → $to ($OCR_FAIL_REASON)" >&2
            OCR_RESULT_DESC="$to (fallback from $from: $OCR_FAIL_REASON)"
            OCR_FALLBACK=true
            # retry_args is passed by name to build_ocr_args/run_ocr
            # (pass-by-name, bash 3.2) — usage is not seen by shellcheck.
            # shellcheck disable=SC2034
            local retry_args=()
            local retry_flags=(--engine "$fallback" --force-ocr --clean)
            if [ "$SPLIT_COLUMNS" = true ]; then
                retry_flags+=(--no-rotate --no-deskew)
            fi
            build_ocr_args retry_args "${retry_flags[@]}"
            if _ocr_attempt "$input" "$ocr_tmp" retry_args; then
                status=0
            # Tesseract as a fallback keeps its split retry — except in
            # PaddleOCR's chain, which never adds an implicit split.
            elif [ "$fallback" = tesseract ] && [ "$engine" != paddle ] \
                && _split_retry "$input" "$ocr_tmp" retry_args "$scratch_dir"; then
                OCR_RESULT_DESC="$OCR_RESULT_DESC, column split retry"
                status=0
            fi
        fi
    fi

    if [ "$status" -ne 0 ]; then
        echo "   ⚠️  All OCR attempts exhausted — using best-effort result"
        OCR_FALLBACK=false
        OCR_RESULT_DESC="$ENGINE_DESC"
    fi
    # Save the last attempt even if its quality check failed; last resort:
    # the input as-is.
    if [ -f "$ocr_tmp" ]; then
        mv "$ocr_tmp" "$output"
    elif [ "$status" -ne 0 ]; then
        cp "$input" "$output"
    fi
    [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
    return "$status"
}

# ════════════════════════════════════════════════════════════
#  PIPELINE
# ════════════════════════════════════════════════════════════

# run_pdf_pipeline [--force-ocr] [--no-clean] <output.pdf> <input.pdf>...
# The one merge-to-OCR sequence of pdf-auto, pdf-combine and pdf-workflow
# (issue #50): merge the inputs in the given order, fix the MediaBox,
# downscale, split columns (SPLIT_COLUMNS), OCR with the quality gate
# (unless NO_QUALITY_GATE), re-merge the halves and publish the result.
# The CLIs only find and group their inputs.
#
# Options are the OCR flags that differ between the CLIs: --force-ocr
# replaces --skip-text, --no-clean leaves out unpaper's --clean.
#
# Temporary files: every intermediate, including the split map and the
# quality gate's retries, lives in one run folder under $WORK_DIR (the
# caller's, which its EXIT trap removes on interrupt). The run folder is
# removed on every return. The result is renamed onto <output.pdf> last, so
# <output.pdf> is the finished file or absent — never unmerged halves.
#
# Sets PIPELINE_PAGES to the page count of the merged input, and
# OCR_RESULT_DESC to the engine behind the result (with any fallback, see
# ocr_with_retry). Returns 0 when
# <output.pdf> was written; otherwise prints a ❌ line, removes
# <output.pdf> and returns 1. Callers run it in a condition (`if`, `||`),
# where `set -e` is off, so every step is checked explicitly.
run_pdf_pipeline() {
    # Read by _pdf_pipeline_steps (bash locals are dynamically scoped).
    local pipeline_force_ocr=false pipeline_clean=true
    while [ $# -gt 0 ]; do
        case "$1" in
            --force-ocr) pipeline_force_ocr=true; shift ;;
            --no-clean)  pipeline_clean=false; shift ;;
            *) break ;;
        esac
    done
    local output="$1"; shift

    local run_dir status=0
    run_dir=$(mktemp -d "${WORK_DIR:?}/pipeline.XXXXXX") || {
        echo "   ❌ Cannot create a work folder in $WORK_DIR"; return 1
    }
    _pdf_pipeline_steps "$run_dir" "$output" "$@" || status=1
    rm -rf "$run_dir"
    # The map was in the run folder; the next run must not verify against it.
    SPLIT_MAP=""
    [ "$status" -eq 0 ] || rm -f "$output"
    return "$status"
}

# _pdf_pipeline_steps <run_dir> <output.pdf> <input.pdf>...
# Internal: the steps of run_pdf_pipeline, which owns cleanup.
_pdf_pipeline_steps() {
    local run_dir="$1" output="$2"; shift 2

    # ── Merge ──
    local merged="$run_dir/merged.pdf"
    if [ $# -eq 1 ]; then
        cp "$1" "$merged" || { echo "   ❌ Cannot read $1"; return 1; }
    elif ! qpdf --empty --pages "$@" -- "$merged"; then
        echo "   ❌ Merging $# files failed (qpdf)"; return 1
    fi
    local npages_status=0
    PIPELINE_PAGES=$(qpdf --show-npages "$merged" 2>/dev/null) || npages_status=$?
    # Exit 3: the count was printed, with warnings (common on damaged scans).
    if [ "$npages_status" -ne 0 ] && [ "$npages_status" -ne 3 ]; then
        PIPELINE_PAGES="?"
    fi
    echo "   🔗 Merged $# file(s): $PIPELINE_PAGES pages"

    # ── MediaBox fix, then downscale ──
    # The fix must come first: oversized pages rasterize at 140 MP at 300 DPI.
    local pre_ocr="$run_dir/fixed.pdf"
    fix_mediabox "$merged" "$pre_ocr"
    if [ "$TARGET_DPI" -gt 0 ]; then
        gs_downscale "$pre_ocr" "$run_dir/downscaled.pdf" "$TARGET_DPI"
        pre_ocr="$run_dir/downscaled.pdf"
    else
        echo "   ⏭️  Downscaling skipped (--dpi 0)"
    fi

    # ── Column split ──
    # --no-rotate/--no-deskew: per-half orientation detection is unreliable
    # and a rotated half would break the re-merge.
    local flags=()
    if [ "$pipeline_force_ocr" = true ]; then flags+=(--force-ocr); fi
    if [ "$pipeline_clean" = true ]; then flags+=(--clean); fi
    if [ "$SPLIT_COLUMNS" = true ]; then
        if ! split_two_column_pdf "$pre_ocr" "$run_dir/split.pdf"; then
            echo "   ❌ Column split failed — no file written"; return 1
        fi
        pre_ocr="$run_dir/split.pdf"
        flags+=(--no-rotate --no-deskew)
    fi

    # ── OCR with quality gate ──
    # ocr_args is passed by name to build_ocr_args/run_ocr
    # (pass-by-name, bash 3.2) — usage is not seen by shellcheck.
    # shellcheck disable=SC2034
    local ocr_args
    build_ocr_args ocr_args ${flags[@]+"${flags[@]}"}
    local result="$run_dir/ocr.pdf"
    if [ "$NO_QUALITY_GATE" = true ]; then
        echo "   🔤 OCR: $ENGINE_DESC"
        OCR_RESULT_DESC="$ENGINE_DESC"
        OCR_FALLBACK=false
        if ! run_ocr "$pre_ocr" "$result" ocr_args; then
            echo "   ❌ OCR failed — no file written"; return 1
        fi
    else
        # WORK_DIR for this call only: the retries' scratch files and split
        # map land in the run folder, not in the caller's WORK_DIR.
        # ocr_with_retry returns 1 on a best-effort (quality-gate-failed) result.
        if ! WORK_DIR="$run_dir" ocr_with_retry "$pre_ocr" "$result" ocr_args; then
            echo "   ❌ Quality gate failed — no file written"; return 1
        fi
    fi

    # ── Re-merge the halves (merge_split_pdf copies them with --keep-split) ──
    if [ "$SPLIT_COLUMNS" = true ]; then
        if ! merge_split_pdf "$result" "$run_dir/remerged.pdf"; then
            # Handing off the unmerged halves would silently double the page count.
            echo "   ❌ Re-merge failed — split result discarded, no file written"; return 1
        fi
        result="$run_dir/remerged.pdf"
    fi

    # ── Publish ──
    # Moved beside the output first (the run folder may be on another
    # volume), then renamed: <output> is never a half-copied file.
    local staged="$output.partial"
    if ! { mv "$result" "$staged" && mv "$staged" "$output"; }; then
        rm -f "$staged"
        echo "   ❌ Cannot write $output"; return 1
    fi
    if [ "$OCR_FALLBACK" = true ]; then
        FALLBACK_COUNT=$((FALLBACK_COUNT + 1))
    fi
}

# ════════════════════════════════════════════════════════════
#  OUTPUT HELPERS
# ════════════════════════════════════════════════════════════

print_summary() {
    local success="$1" fail="$2" output_dir="$3"
    echo ""
    echo "═══════════════════════════════════════════"
    echo "✅ Batch complete"
    echo "═══════════════════════════════════════════"
    echo "🧠 Engine:      $ENGINE_DESC"
    echo "📊 Successful: $success"
    if [ "$FALLBACK_COUNT" -gt 0 ]; then
        echo "🔄 Fallbacks:  $FALLBACK_COUNT (engine switched after a failure, see above)"
    fi
    [ "$fail" -gt 0 ] && echo "❌ Failed: $fail"
    echo "📁 Output:     $output_dir"
}

# ════════════════════════════════════════════════════════════
#  INIT: Run common setup (call once at script start)
# ════════════════════════════════════════════════════════════

# require_paddle_engine_for_mode <engine>
# Usage error for --paddle-mode without --engine paddle.
require_paddle_engine_for_mode() {
    if [ -n "$PADDLE_MODE" ] && [ "$1" != paddle ]; then
        usage_error "--paddle-mode needs --engine paddle"
    fi
}

# check_engine <engine>
# `reprocess-raw --check-engine`: resolves <engine> with the same checks as
# lib_init (tools, Apple Vision, PaddleOCR readiness; not pikepdf, which only
# --split-columns needs) and exits without touching a file: 0 if a run would start on it,
# 4 (`check-failed`, contracts/cli-contract.json) with the reason on stderr
# otherwise. The plugin asks this before it offers an engine.
check_engine() {
    local engine="${1:-auto}"
    require_paddle_engine_for_mode "$engine"
    check_deps ocrmypdf qpdf gs pdftotext || exit 4
    detect_apple_ocr
    if [ "$engine" = paddle ]; then
        detect_paddle_ocr
    fi
    resolve_engine "$engine" || exit 4
    echo "✅ Engine usable"
    echo "   🧠 Engine:    $ENGINE_DESC"
    exit 0
}

# lib_init <engine_string> [--fast]
# One-shot: dependency check + engine resolve + optimizer detection.
# Call this once per script after argument parsing.
lib_init() {
    local engine="${1:-auto}" fast="${2:-false}"

    # A usage error, so it comes before any check or processing.
    require_paddle_engine_for_mode "$engine"

    # PaddleOCR orders both columns itself; splitting them loses words and
    # order (#153, bench/ERGEBNIS.md, Nachtrag 26). A fallback engine then
    # reads whole pages too.
    if [ "$engine" = paddle ] && [ "$SPLIT_COLUMNS" = true ]; then
        local split_flag=--split-columns
        [ "$SPLIT_ALL_PAGES" = true ] && split_flag=--split-columns-all
        echo "⚠️  PaddleOCR reads whole pages; ignoring $split_flag" >&2
        SPLIT_COLUMNS=false
        SPLIT_ALL_PAGES=false
    fi

    echo "🔍 Checking dependencies..."
    check_deps ocrmypdf qpdf gs pdftotext || exit 1

    # Apple Vision is probed for every engine: it is also a fallback.
    detect_apple_ocr
    if [ "$engine" = paddle ]; then
        detect_paddle_ocr
    fi
    detect_optimizers

    # ── Python binary with pikepdf (for column_tools.py) ──
    PYTHON_BIN=""
    for candidate in "${VENV_ROOT:-$HOME/.venvs}/ocrmypdf/bin/python3" "python3"; do
        if "$candidate" -c "import pikepdf" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON_BIN" ]; then
        # Fail before any processing: without pikepdf there is no split map,
        # and an unmergeable split returns doubled half-pages (issue #47).
        if [ "$SPLIT_COLUMNS" = true ]; then
            echo "❌ pikepdf not found — required by --split-columns / --split-columns-all" >&2
            echo "   Install: ./setup.sh (or pip install pikepdf into the ocrmypdf venv)" >&2
            exit 1
        fi
        echo "   ⚠️  pikepdf not found — automatic column-split retry disabled"
    fi

    if ! resolve_engine "$engine"; then
        exit 1
    fi

    # --fast presets and the RAM-aware jobs default fill in only what the
    # user did not set explicitly: `--jobs 2` stays 2 on every machine.
    if [ "$fast" = "true" ]; then
        [ "$DPI_SET" = true ] || TARGET_DPI=$FAST_DPI
        [ "$JOBS_SET" = true ] || JOBS=$FAST_JOBS
    elif [ "$JOBS_SET" != true ]; then
        detect_safe_jobs
    fi

    echo "✅ Tools OK"
    echo "   🧠 Engine:    $ENGINE_DESC"
    echo "   📦 Optimize:  $OPTIMIZE_LEVEL"
    echo "   ⚙️  Jobs: $JOBS | DPI: $TARGET_DPI"
    if [ "$RESOLVED_ENGINE" = paddle ]; then
        echo "   ⚙️  PaddleOCR runs one OCR job; --jobs applies to a fallback engine"
    fi
    # NOTE: these must not be the last statement in the function — under
    # `set -e`, "[ false-cond ] && echo ..." returns 1 when the condition
    # is false, which becomes lib_init's own return status and kills the
    # calling script right here (silently) whenever the flag isn't set.
    if [ "$fast" = "true" ]; then
        echo "   🏃 --fast active"
    fi
    if [ "$SPLIT_COLUMNS" = true ]; then
        echo "   📐 --split-columns active (split + re-merge)"
    fi
    return 0
}

