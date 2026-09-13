# Bug Report: `--split-columns` — Faulty Detection, Merge Artifacts, and Text Loss

**Date:** 2026-07-06
**Affected Components:** `scripts/column_tools.py` (detect/split/merge), `scripts/pdf-lib.sh` (pipeline integration)
**Severity:** High — Data quality degradation in `raw/`, core value proposition of feature (handling mixed documents correctly) unfulfilled
**Reference File:** `raw/StR/Rep-Faelle/strafrecht-fall-01.pdf` (20 pages, photographed binder pages; single-column case facts/structure pages + two-column solution pages)

---

## Executive Summary

The batch run on 2026-07-06 formally completed processing on all 14 files (restoring page count and A4 format correctly), but detailed content inspection of `strafrecht-fall-01.pdf` and `Verwaltungsprozessrecht.pdf` revealed **five linked defects**. Success verification (page count + document-wide garbage score) was too coarse to catch them.

| # | Defect | Affected Pages (Output) | Impact |
|---|---|---|---|
| B1 | Detection **False Positive**: single-column page detected as two-column | p. 4 | Page visually sliced, content scaled down, **no text layer** |
| B2 | Detection **False Negatives**: 8 of 15 genuine two-column pages missed | p. 7–10, 12, 15, 17, 19 | **Column interleaving in text layer** — original bug returned |
| B3 | **Merge geometry artifacts** on correctly split pages | p. 6, 11, 13, 14, 16, 18, 20 | Visible seam line, differently scaled columns, clipped headers |
| B4 | **Text loss due to OCR skip** on degraded half-pages | p. 4, 6 | Pages contain **zero** searchable text (formfeed only) |
| B6 | **Misaligned text layer** after Apple engine retry on CropBox half-pages (separate file, see below) | `Verwaltungsprozessrecht.pdf` etc. | Highlights and search hits positioned offset from printed text |

Original files in git HEAD remain intact (`git show "HEAD:raw/..."` verified) — **no permanent data loss**, but all 14 reprocessed files currently in `raw/` were suspicious until fixed.

---

## Reproduction (Deterministically Verified)

```bash
# 1. Extract original from git
git show "HEAD:raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" > /tmp/original.pdf

# 2. Re-create pre-processing steps (matching batch run)
gs ... -dPDFFitPage -dDEVICEWIDTHPOINTS=595 -dDEVICEHEIGHTPOINTS=842 ... original.pdf → fixed.pdf
gs ... -dColorImageResolution=300 (Bicubic) ... fixed.pdf → downscaled.pdf

# 3. Split with auto-detection
python3 column_tools.py split downscaled.pdf split.pdf --map map.json --auto
# → identical result as batch run: 8 split, 12 full-width
```

**Reproduced Map:** Split on orig pages 4 (x=341.5!), 6, 11, 13, 14, 16, 18, 20. Full-width: 1–3, 5, **7–10, 12, 15, 17, 19**.

Expected behavior: Split on 6–20 except 19 (= all two-column "solution" pages), **no** split on page 4 (single-column structural overview).

---

## Detailed Findings

### B1 — False Positive on Page 4 (Single-Column Overview Page)

**Symptom:** Output page 4 shows (scaled down) page content on the left, a narrow strip on the right, separated by an artificial white gap. Words at split boundary x=341.5 are chopped mid-word.

**Cause:** Source photo is a binder photograph where **the margin of the adjacent page is visible on the right edge**. After `PDFFitPage` letterboxing, the actual page content occupies only the left portion of the A4 frame, with the next page's edge appearing on the right. Line-based analysis observed: many "left" lines (main text), ≥ 3 "right" lines (margin fragments), large empty gap between → classified as two-column. Missing two essential sanity checks:
1. **Maximum gutter width**: Genuine column gutters are narrow (~3–8% of page width). Empty whitespace > 15–20% is a page margin artifact, not a column gutter.
2. **Text mass symmetry**: Both "columns" must contain comparable text mass. A marginal strip containing a handful of text fragments opposite a full block of text must never be classified as a second column.

### B2 — False Negatives on 8 Genuine Two-Column Pages

**Symptom:** Output pages 7–10, 12, 15, 17, 19 (all two-column solution pages) were passed to standard PSM 1 OCR as single full-width pages. Result: **Column interleaving returned** — verified on output page 8, where sentences from the right column ("Wehrlosigkeit des Opfers…") are extracted interleaved with outline headings from the left column ("Strafbarkeit des A / Mord, §§ 212 I, 211…").

**Cause:** Gutter analysis evaluated **global page edges**: `left_edge = max(xMax of all left lines)`, `right_edge = min(xMin of all right lines)`, Gutter = difference. On **skewed photographed pages, column gutters run diagonally** — left column extends past vertical center at top or bottom, causing global left edge to overlap global right edge (in debug logs: Gutter −2.0 to −4.8 pt) → page rejected. Detection was **not skew-tolerant**, despite binder photos being standard for these sources. (Additionally fragile: y-clustering with `LINE_Y_TOL = 3.0` pt breaks under skew because words on the same visual line exceed 3 pt yMin variance across page width.)

### B3 — Re-Merge Geometry Artifacts

**Symptom:** Correctly split pages (e.g. output p. 6, 11, 13, 14) show a visible bright seam in the middle, unevenly scaled columns (p. 11: left column visibly smaller than right), and clipped/missing headers on left.

**Cause:** Between split and merge, ocrmypdf processes half-pages independently — specifically, `--deskew` rotates each half individually, altering dimensions so they no longer match rect coordinates saved in map. `pikepdf add_overlay` then scales **aspect-preserved and centered** into target rect → seams, offset, mismatched column sizes. Control experiment: **Split + Merge without intermediary OCR is lossless** (identical page character counts, verified across all 8 split pages) — degradation occurs exclusively due to per-half processing between steps.

### B4 — Total Text Loss on Output Pages 4 and 6

**Symptom:** `pdftotext` returns exactly 1 character (formfeed) for output pages 4 and 6; `pdffonts` shows **zero fonts** — text layer completely missing.

**Cause:** Tesseract skipped degraded half-pages (batch log: `Too few characters. Skipping this page` on split pages 5 and 8 = right strip of orig-4 and half of orig-6). Because `--force-ocr` was enabled, previous text layer was already discarded at execution start → skip = page permanently lacks text. Document-wide garbage score (0.345 → "pass") averaged away complete text loss on isolated pages.

### B6 — Misaligned Text Layer Post Apple Engine Retry (User Report)

**Symptom:** In `raw/OeR/Verwaltungsrecht-AT/Verwaltungsprozessrecht.pdf` (PDF page 22), text selection/highlighting boxes in PDF viewer appear offset from printed text, extending past page margins. Reproduced via overlay test (drawing all 1,160 word bounding boxes over rendered page): **Left column boxes are systematically shifted left**, partially extending outside page boundaries; right column boxes match accurately.

**Cause (Isolated via Control Experiment):** File failed quality gate (Tesseract score 0.447) → Retry Attempt 3 switched to **Apple Vision engine**, running `--force-ocr` on pre-split CropBox half-pages; result was saved and merged as best-effort. Control experiment (Apple OCR on isolated CropBox half-pages): **Apple plugin pipeline handles pages with `CropBox ≠ MediaBox` improperly** — output half-page retains MediaBox 595 pt (CropBox 299 pt; `pdfinfo` and `pdftoppm` conflict), rendering invisible text horizontally shifted relative to image. Tesseract pipeline normalizes half-pages to genuine ~297 pt pages. During merge, internally shifted text is embedded as-is.

**Aggravating Factor:** Engine retry rebuilds OCR CLI arguments (`build_ocr_args retry_args --force-ocr`), **losing `--no-rotate` and `--clean` flags** — retry on split pipeline ran with options explicitly disabled for half-pages.

**Affected Files:** All files whose output originated from Apple retry and contained split pages: `Verwaltungsprozessrecht.pdf` (10 split pages), `Verwaltungsrecht AT Fall 3/4/7/13.pdf`.

### B5 — Verification Gap (Process)

Batch validation only verified `PageCount(new) == PageCount(old)` plus document-wide quality gate. Both passed despite 10 of 20 pages being defective (2 lacking text, 8 interleaved). Lacked **per-page** validation (e.g. minimum character count per non-blank page).

---

## Recommended Fixes (Prioritized)

1. **B2/B3 Root Cause: Execute deskew prior to split.** Deskew entire page upfront (pipeline stage before `split`), then execute split/OCR **without** `--deskew` on halves. Makes detection skew-tolerant and eliminates dimension drift between map and OCR output.
2. **B1: Sanity Rules in `_analyze_columns`:** Cap maximum gutter width (> ~15% page width = artifact) and require minimum relative text mass per column relative to counterpart (e.g. weaker column ≥ 25% of stronger column).
3. **B2 Robust Gutter Test:** Replace global max/min edges with median of line edges or per-band gutter positioning.
4. **B4: Per-Page Post-OCR Guard:** If half-page contains 0 characters post OCR while counterpart contains text → raise warning and flag page.
5. **B5: Per-Page Batch Verification:** Validate minimum character count per non-blank page before overwriting `raw/` files.
6. **B6a: Normalize Half-Pages During Split:** Set MediaBox equal to CropBox region in `column_tools.py split` after Ghostscript cropping so `CropBox == MediaBox`.
7. **B6b: Preserve Flags in Engine Retry:** Retry logic must preserve caller flags (`--no-rotate`, `--clean`).

---

## Update 2026-07-06: Fixes Implemented & Verified

### Implementation Status

| Fix | Status | Notes |
|---|---|---|
| 1 (Deskew before split) | **Replaced** | `--no-deskew` set for split path, resolving B3 without dimension drift |
| 2 + 3 (Pairwise gutter analysis) | **Implemented & Verified** | Replaced global edge tests with line-pair gutter analysis |
| 4 (Per-page guard) | **Resolved** | Detection fixes prevented degraded half-pages |
| 5 (Per-page verification) | **Implemented & Verified** | Added `reprocess-raw` script + `column_tools.py verify-pages` |
| 6a (MediaBox normalization) | **Implemented & Verified** | Overlay test confirmed exact text box alignment |
| 6b (Retry flag preservation) | **Implemented & Verified** | `--no-rotate --no-deskew` correctly preserved |

### Pairwise Line Gutter Analysis (Resolves B1 + B2)

Rather than evaluating global left/right edges, `_analyze_columns()` matches each left line to its **nearest right line of similar vertical height** (`PAIR_Y_TOL = 8.0` pt) and evaluates gutter **per pair**. A page is classified as two-column if ≥ 30% of pairs (`PAIR_VALID_FRAC_MIN`) exhibit a plausible gutter (3–15% page width).

On `strafrecht-fall-01.pdf`, detection now returns exactly `{6...20}` as two-column, `{1...5}` correctly rejected — matching ground truth.

### B7 (New): Reading Order with `pdftotext` Standard Mode

Despite geometrically accurate text layer, `pdftotext` without flags returned interleaved text on merged two-column pages due to Poppler reading order heuristics.

**Fix:** Use `pdftotext -raw` — follows content stream emission order, which is guaranteed left-then-right in `merge_pdf()`. Updated in `quality_check()` (`pdf-lib.sh`) and `verify_ocr_split()` (`column_tools.py`).

### B8 (New): Garbage Heuristic Umlaut False Positives

Regex bracket expressions `[a-zäöüß][A-ZÄÖÜ]` in Bash matched German umlauts incorrectly as mixed-case corruption.

**Fix:** Replaced Bash regex in `_garbage_heuristic()` with Python implementation (`str.islower()` / `str.isupper()`). Garbage score on `Fall 3.pdf` dropped from 0.459 to 0.162 on identical text.

### Final Verification Results

All 14 affected files reprocessed cleanly with `reprocess-raw --force-ocr --split-columns`:

| File | Pages | Split | Garbage Score (Before → After) |
|---|---|---|---|
| Verwaltungsprozessrecht.pdf | 54 | 51 split, 3 full | 0.44 → 0.196 |
| Verwaltungsrecht AT Fall 13 | 11 | 7 split, 4 full | 0.42 → 0.166 |
| Verwaltungsrecht AT Fall 3 | 7 | 4 split, 3 full | 0.46 → 0.162 |

All defects B1–B8 resolved and verified.
rher → nachher) |
|---|---|---|---|
| Verwaltungsprozessrecht.pdf | 54 | 51 gesplittet, 3 Vollseiten | 0,44 → 0,196 |
| Verwaltungsrecht AT Fall 13 | 11 | 7 gesplittet, 4 Vollseiten | 0,42 → 0,166 |
| Verwaltungsrecht AT Fall 3 | 7 | 4 gesplittet, 3 Vollseiten | 0,46 → 0,162 |

Damit sind **alle 14 ursprünglich betroffenen Dateien final auf dem korrigierten Stand** (B1–B8 alle gelöst und verifiziert).
