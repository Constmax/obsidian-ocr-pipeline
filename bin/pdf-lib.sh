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
#   - Dependency + engine detection
#   - Ghostscript downscaling (Bicubic)
#   - Two-column page splitting (CropBox)
#   - OCR args construction
#   - Post-OCR quality gate with auto-retry
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
USE_APPLE=false
ENGINE_DESC=""
HAS_JBIG2=false
HAS_PNGQUANT=false
HAS_UNPAPER=false
OPTIMIZE_LEVEL=1
TARGET_DPI=$DEFAULT_DPI
JOBS=$DEFAULT_JOBS
SPLIT_COLUMNS=false       # --split-columns flag
SPLIT_ALL_PAGES=false     # --split-columns-all flag (force --all instead of per-page --auto)
KEEP_SPLIT=false          # --keep-split flag (suppress merge)
SPLIT_MAP=""              # Path to split_map.json (set by split_two_column_pdf)
PYTHON_BIN=""             # Python with pikepdf (set by lib_init)

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

# resolve_engine <engine_string>
# Resolves auto|apple|tesseract → USE_APPLE + ENGINE_DESC.
# Exits if apple is requested but not installed.
resolve_engine() {
    local engine="${1:-auto}"
    USE_APPLE=false
    case "$engine" in
        auto)
            if [ "$APPLE_AVAILABLE" = true ]; then
                USE_APPLE=true
                ENGINE_DESC="Apple Vision (auto)"
            else
                ENGINE_DESC="Tesseract (auto-fallback)"
            fi ;;
        apple)
            if [ "$APPLE_AVAILABLE" = true ]; then
                USE_APPLE=true
                ENGINE_DESC="Apple Vision (manual)"
            else
                echo "❌ Apple Vision plugin not installed." >&2
                echo "   Install: pip install ocrmypdf-appleocr" >&2
                return 1
            fi ;;
        tesseract)
            ENGINE_DESC="Tesseract (manual)" ;;
        *)
            echo "❌ --engine must be 'auto', 'apple', or 'tesseract'" >&2
            return 1 ;;
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
# Called by lib_init when JOBS is still at DEFAULT_JOBS (user didn't override).
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
# Falls back to legacy gs loop (always --all, no per-page detection) if
# PYTHON_BIN is not available.
split_two_column_pdf() {
    local input="$1" output="$2"
    SPLIT_MAP="${input%.pdf}_split_map.json"

    if [ -n "$PYTHON_BIN" ] && [ -f "$SCRIPT_DIR/column_tools.py" ]; then
        local mode_flag="--auto"
        if [ "$SPLIT_ALL_PAGES" = true ]; then
            mode_flag="--all"
        fi
        # Guarded: a failing split must not kill the script under `set -e` —
        # otherwise the fallback to gs below is unreachable dead code.
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" split "$input" "$output" \
            --map "$SPLIT_MAP" "$mode_flag" 2>&1; then
            echo "   ⚠️  column_tools.py split failed" >&2
        fi
        if [ -f "$output" ]; then
            return 0
        fi
        echo "   ⚠️  column_tools.py split yielded no result, fallback to gs" >&2
    fi

    # ── Fallback: legacy Ghostscript loop ──
    local total_pages tmpdir width height half_width page_num
    total_pages=$(pdfinfo "$input" 2>/dev/null | awk '/^Pages:/ {print $2}')
    if [ -z "$total_pages" ] || [ "$total_pages" -lt 1 ]; then
        echo "   ⚠️  Column split: Cannot determine page count" >&2
        cp "$input" "$output"; return 0
    fi
    read -r width height < <(pdfinfo "$input" 2>/dev/null | awk '/^Page size:/ {
        gsub(/pts/, "", $3); gsub(/pts/, "", $5); print int($3), int($5)
    }')
    if [ -z "$width" ] || [ -z "$height" ]; then
        echo "   ⚠️  Column split: Cannot determine page dimensions" >&2
        cp "$input" "$output"; return 0
    fi
    half_width=$((width / 2))
    tmpdir=$(mktemp -d)
    for ((page_num = 1; page_num <= total_pages; page_num++)); do
        gs -sDEVICE=pdfwrite -dNOPAUSE -dQUIET -dBATCH \
           -dFirstPage="$page_num" -dLastPage="$page_num" \
           -sOutputFile="$tmpdir/p$(printf '%04d' "$page_num")_l.pdf" \
           -c "[/CropBox [0 0 $half_width $height] /PAGE pdfmark" \
           -f "$input" 2>/dev/null
        gs -sDEVICE=pdfwrite -dNOPAUSE -dQUIET -dBATCH \
           -dFirstPage="$page_num" -dLastPage="$page_num" \
           -sOutputFile="$tmpdir/p$(printf '%04d' "$page_num")_r.pdf" \
           -c "[/CropBox [$half_width 0 $width $height] /PAGE pdfmark" \
           -f "$input" 2>/dev/null
    done
    local page_list=()
    for ((page_num = 1; page_num <= total_pages; page_num++)); do
        local pf
        pf="p$(printf '%04d' "$page_num")"
        page_list+=("$tmpdir/${pf}_l.pdf" "$tmpdir/${pf}_r.pdf")
    done
    qpdf --empty --pages "${page_list[@]}" -- "$output" 2>/dev/null
    rm -rf "$tmpdir"
    if [ ! -f "$output" ]; then
        echo "   ⚠️  Column split failed, continuing with original"
        cp "$input" "$output"
    fi
    SPLIT_MAP=""  # no map in fallback mode
}

# merge_split_pdf <ocr.pdf> <output.pdf>
# Reassembles half-page pairs back to original full-page format.
# Uses the split_map.json from split_two_column_pdf.
# No-op if no map exists or --keep-split is set.
merge_split_pdf() {
    local input="$1" output="$2"

    if [ "$KEEP_SPLIT" = true ]; then
        echo "   ⏭️  Re-merge skipped (--keep-split)"
        [ "$input" != "$output" ] && cp "$input" "$output"
        return 0
    fi

    if [ -z "$SPLIT_MAP" ] || [ ! -f "$SPLIT_MAP" ]; then
        [ "$input" != "$output" ] && cp "$input" "$output"
        return 0
    fi

    if [ -n "$PYTHON_BIN" ] && [ -f "$SCRIPT_DIR/column_tools.py" ]; then
        # Guarded: a failing merge (corrupt map, unreadable input) must not
        # kill the caller under `set -e` — fall through to the warning below.
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/column_tools.py" merge "$input" "$output" \
            --map "$SPLIT_MAP" 2>&1; then
            echo "   ⚠️  column_tools.py merge failed" >&2
        fi
        if [ -f "$output" ]; then
            return 0
        fi
    fi

    echo "   ⚠️  Merge not possible — keeping split version" >&2
    [ "$input" != "$output" ] && cp "$input" "$output"
    return 0
}

# ════════════════════════════════════════════════════════════
#  OCR EXECUTION
# ════════════════════════════════════════════════════════════

# build_ocr_args <output_var_name> [--force-ocr] [--clean]
# Builds the ocrmypdf argument array.
# Sets the variable named by $1 to the array of arguments.
build_ocr_args() {
    local outvar="$1"; shift
    local use_clean=false
    local no_rotate=false
    local no_deskew=false
    local skip_text="--skip-text"
    while [ $# -gt 0 ]; do
        case "$1" in
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
    args+=(
        --optimize "$OPTIMIZE_LEVEL"
        --jobs "$JOBS"
        --max-image-mpixels "$MAX_IMAGE_MPIXELS"
    )

    if [ "$USE_APPLE" = true ]; then
        args=(--plugin ocrmypdf_appleocr "${args[@]}")
    else
        args+=(--tesseract-pagesegmode 1)
        if [ "$use_clean" = true ] && [ "$HAS_UNPAPER" = true ]; then
            args+=(--clean)
        fi
    fi

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

# ocr_with_retry <pre_ocr.pdf> <output.pdf> <other_engine> <args_array_name> [--no-split]
# Runs OCR, checks quality, retries with alternative engine on failure.
# If quality fails and we're using tesseract, also tries with --split-columns.
# $3 = the OTHER engine to try on retry (apple|tesseract)
# $4 = name of the OCR args array variable (bash 3.2: passed by name)
# Returns 0 if final result passes quality, 1 if all attempts fail.
#
# Scratch files are placed in $WORK_DIR (set by the caller, cleaned via its
# EXIT trap) rather than next to $output — otherwise an interrupted run can
# leave a "<output>.tmp_qc.pdf" behind, which ends in .pdf and gets picked up
# as an input on the next run.
ocr_with_retry() {
    local input="$1" output="$2" alt_engine="$3"
    local args_name="$4"   # array variable name, not nameref
    local no_split="${5:-false}"

    local scratch_dir="${WORK_DIR:-}"
    local own_scratch_dir=false
    if [ -z "$scratch_dir" ]; then
        scratch_dir=$(mktemp -d)
        own_scratch_dir=true
    fi

    # ── Attempt 1: primary engine ──
    echo "   🔤 OCR (attempt 1): $ENGINE_DESC"

    local work_pdf="$input"
    local ocr_tmp="$scratch_dir/ocr_retry_tmp_qc.pdf"

    if ! run_ocr "$work_pdf" "$ocr_tmp" "$args_name"; then
        echo "   ❌ OCR (attempt 1) failed"
        rm -f "$ocr_tmp"
    elif quality_check "$ocr_tmp"; then
        mv "$ocr_tmp" "$output"
        [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
        return 0
    fi

    # ── Attempt 2: try --split-columns (if tesseract and not already split) ──
    if [ "$USE_APPLE" = false ] && [ "$SPLIT_COLUMNS" = false ] && [ "$no_split" != "true" ]; then
        echo "   🔄 Retry with --split-columns..."
        local split_pdf="$scratch_dir/ocr_retry_split.pdf"
        split_two_column_pdf "$input" "$split_pdf"

        # Strip --rotate-pages and --deskew from the caller's args for this sub-attempt
        # (unreliable per-half orientation detection would break the merge
        # below, and deskew was already done pre-split) without assuming which other flags the caller built in.
        local split_args=() _orig_arg
        eval "_orig_args=(\"\${${args_name}[@]}\")"
        # _orig_args was assigned via eval string (pass-by-name, bash 3.2).
        # shellcheck disable=SC2154
        for _orig_arg in "${_orig_args[@]}"; do
            [ "$_orig_arg" = "--rotate-pages" ] && continue
            [ "$_orig_arg" = "--deskew" ] && continue
            split_args+=("$_orig_arg")
        done

        if ! run_ocr "$split_pdf" "$ocr_tmp" split_args; then
            echo "   ❌ Split OCR failed"
            rm -f "$ocr_tmp"
        elif quality_check "$ocr_tmp"; then
            # Reassemble the split halves back into original-page format
            # before handing off the result — otherwise this internal
            # auto-retry path silently doubles the page count, exactly
            # like the bug that corrupted several files in raw/.
            local merged_tmp="$scratch_dir/ocr_retry_merged.pdf"
            merge_split_pdf "$ocr_tmp" "$merged_tmp"
            if [ -f "$merged_tmp" ]; then
                mv "$merged_tmp" "$output"
            else
                mv "$ocr_tmp" "$output"
            fi
            rm -f "$split_pdf"
            [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
            return 0
        fi
        rm -f "$split_pdf"
    fi

    # ── Attempt 3: switch engine ──
    if [ "$alt_engine" != "$ENGINE_DESC" ] && [ -n "$alt_engine" ]; then
        echo "   🔄 Retry with alternative engine: $alt_engine"

        local saved_apple=$USE_APPLE saved_desc=$ENGINE_DESC
        if ! resolve_engine "$alt_engine"; then
            USE_APPLE=$saved_apple; ENGINE_DESC=$saved_desc
            echo "   ❌ Alternative engine not available"
            [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
            return 1
        fi

        # Rebuild args with new engine — always use --force-ocr on retry
        # since the input may have residual text from a prior attempt.
        # Preserve split column flags if needed.
        # retry_args is passed by name to build_ocr_args/run_ocr
        # (pass-by-name, bash 3.2) — usage is not seen by shellcheck in
        # build_ocr_args.
        # shellcheck disable=SC2034
        retry_args=()
        local retry_flags=(--force-ocr --clean)
        if [ "$SPLIT_COLUMNS" = true ]; then
            retry_flags+=(--no-rotate --no-deskew)
        fi
        build_ocr_args retry_args "${retry_flags[@]}"

        if ! run_ocr "$input" "$ocr_tmp" retry_args; then
            echo "   ❌ OCR (attempt 3) failed"
            rm -f "$ocr_tmp"
        elif quality_check "$ocr_tmp"; then
            mv "$ocr_tmp" "$output"
            USE_APPLE=$saved_apple; ENGINE_DESC=$saved_desc
            [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
            return 0
        fi
        USE_APPLE=$saved_apple; ENGINE_DESC=$saved_desc
    fi

    echo "   ⚠️  All OCR attempts exhausted — using best-effort result"
    # Save last attempt if any file exists, even if quality check failed
    if [ -f "$ocr_tmp" ]; then
        mv "$ocr_tmp" "$output"
    else
        # Last resort: copy input as-is
        cp "$input" "$output"
    fi
    [ "$own_scratch_dir" = true ] && rm -rf "$scratch_dir"
    return 1
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
    [ "$fail" -gt 0 ] && echo "❌ Failed: $fail"
    echo "📁 Output:     $output_dir"
}

# ════════════════════════════════════════════════════════════
#  INIT: Run common setup (call once at script start)
# ════════════════════════════════════════════════════════════

# lib_init <engine_string> [--fast]
# One-shot: dependency check + engine resolve + optimizer detection.
# Call this once per script after argument parsing.
lib_init() {
    local engine="${1:-auto}" fast="${2:-false}"

    echo "🔍 Checking dependencies..."
    check_deps ocrmypdf qpdf gs pdftotext || exit 1

    detect_apple_ocr
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
        echo "   ⚠️  pikepdf not found — split/merge in fallback mode only"
    fi

    if ! resolve_engine "$engine"; then
        exit 1
    fi

    # Apply --fast overrides
    if [ "$fast" = "true" ]; then
        [ "$TARGET_DPI" = "$DEFAULT_DPI" ] && TARGET_DPI=$FAST_DPI
        [ "$JOBS" = "$DEFAULT_JOBS" ] && JOBS=$FAST_JOBS
    fi

    # RAM-aware default for JOBS (only if user didn't set --jobs)
    if [ "$JOBS" = "$DEFAULT_JOBS" ]; then
        detect_safe_jobs
    fi

    echo "✅ Tools OK"
    echo "   🧠 Engine:    $ENGINE_DESC"
    echo "   📦 Optimize:  $OPTIMIZE_LEVEL"
    echo "   ⚙️  Jobs: $JOBS | DPI: $TARGET_DPI"
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

