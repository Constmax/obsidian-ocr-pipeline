"""Truth construction, matching, metrics and gate of bench/reading_order.py.

Pure: no OCR, no vault, no OCRmyPDF.
"""

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reading_order as ro  # noqa: E402


def region(role, box, column=None):
    spec = {"role": role, "box": box}
    if column:
        spec["column"] = column
    return spec


def line(text, x0, y0, x1, y1):
    return {"text": text, "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def truth(*texts, role="body", column=None, section=0, full_width=False):
    return [ro.TruthLine(i, text, ro.normalize(text), role, column, section, full_width, ())
            for i, text in enumerate(texts)]


# ── Normalization and matching ─────────────────────────────────────────────


def test_normalize_keeps_letters_digits_and_section_sign():
    assert ro.normalize("§ 823 Abs. 1 BGB – Straße, ÄÖÜ!") == "§823abs1bgbstraßeäöü"
    assert ro.normalize("ﬁnden") == "finden"  # NFKC ligature


def test_exact_and_noisy_occurrences_are_found():
    haystack = ro.normalize("Vorher steht etwas. Der Anspruch aus § 280 Abs. 1 BGB besteht. Ende")
    index = ro.gram_index(haystack)
    exact = ro.occurrences(ro.normalize("Der Anspruch aus § 280 Abs. 1 BGB"), index)
    noisy = ro.occurrences(ro.normalize("Der Ansprnch aus § 280 Abs. l BGB"), index)
    assert [start for start, _ in exact] == [haystack.index("deranspruch")]
    assert exact[0][1] == 1.0
    assert len(noisy) == 1 and abs(noisy[0][0] - exact[0][0]) <= 2
    assert ro.occurrences(ro.normalize("Völlig anderer Satz ohne Treffer"), index) == []


def test_repeated_text_yields_one_occurrence_per_copy():
    haystack = ro.normalize("Diese Zeile kommt doppelt vor. " * 2)
    found = ro.occurrences(ro.normalize("Diese Zeile kommt doppelt vor."), ro.gram_index(haystack))
    assert sorted(start for start, _ in found) == [0, len(haystack) // 2]


def test_too_short_needles_match_nothing():
    assert ro.occurrences("abc", ro.gram_index("abcabc")) == []


# ── Page scores ────────────────────────────────────────────────────────────


LEFT = ["erste Zeile links oben", "zweite Zeile links unten"]
RIGHT = ["dritte Zeile rechts oben", "vierte Zeile rechts unten"]


def two_columns():
    return [replace(t, column="L" if t.order < 2 else "R") for t in truth(*LEFT, *RIGHT)]


def test_column_order_scores_perfectly():
    scores = ro.score_page(two_columns(), "\n".join(LEFT + RIGHT))
    assert scores["accuracy"] == 1.0
    assert (scores["matched"], scores["unmatched"], scores["duplicated"]) == (4, 0, 0)
    assert scores["interleaved_sections"] == 0


def test_interleaved_columns_are_detected_and_cost_pairs():
    scores = ro.score_page(two_columns(), "\n".join([LEFT[0], RIGHT[0], LEFT[1], RIGHT[1]]))
    assert scores["accuracy"] == pytest.approx(5 / 6)
    assert scores["interleaved_sections"] == 1


def test_right_column_first_counts_as_wrong_order():
    scores = ro.score_page(two_columns(), "\n".join(RIGHT + LEFT))
    assert scores["accuracy"] == pytest.approx(2 / 6)
    assert scores["interleaved_sections"] == 1


def test_columns_are_checked_per_section():
    # Two sections, each reading its left column first: L R | L R.
    lines = [replace(t, column=column, section=section) for t, (column, section)
             in zip(truth(*LEFT, *RIGHT), [("L", 0), ("R", 0), ("L", 1), ("R", 1)])]
    assert ro.score_page(lines, "\n".join(LEFT + RIGHT))["interleaved_sections"] == 0


def test_unmatched_and_duplicated_lines_are_reported_apart_from_order():
    lines = truth("eine lange erste Zeile", "eine lange zweite Zeile", "fehlt im Ergebnis ganz")
    extracted = "eine lange erste Zeile\neine lange zweite Zeile\neine lange erste Zeile"
    scores = ro.score_page(lines, extracted)
    assert (scores["matched"], scores["unmatched"], scores["duplicated"]) == (2, 1, 1)
    assert scores["accuracy"] == 1.0


def test_identical_truth_lines_are_not_duplicates():
    lines = truth("Wiederholte Randzeile", "Wiederholte Randzeile")
    scores = ro.score_page(lines, "Wiederholte Randzeile\nWiederholte Randzeile")
    assert (scores["matched"], scores["duplicated"], scores["accuracy"]) == (2, 0, 1.0)


def test_short_line_inside_a_longer_line_keeps_its_own_copy():
    body, footnote = truth("BVerwG, NVwZ 2017, 489: Sinn des § 44a VwGO", "BVerwG, NVwZ 2017, 489.")
    lines = [replace(body, column="L"), replace(footnote, column="R")]
    scores = ro.score_page(lines, "BVerwG, NVwZ 2017, 489: Sinn des § 44a VwGO\nBVerwG, NVwZ 2017, 489.")
    assert (scores["accuracy"], scores["interleaved_sections"], scores["duplicated"]) == (1.0, 0, 0)


def test_short_lines_are_not_scored():
    scores = ro.score_page(truth("§ 1", "eine lange Zeile Text"), "eine lange Zeile Text § 1")
    assert (scores["lines"], scores["eligible"], scores["matched"]) == (2, 1, 1)


def test_misplaced_full_width_line_is_counted():
    lines = [
        *truth("linke Spalte oben", column="L"),
        replace(truth("Zwischenüberschrift", role="heading", full_width=True)[0], order=1),
        replace(truth("linke Spalte unten", column="L", section=1)[0], order=2),
    ]
    good = ro.score_page(lines, "linke Spalte oben\nZwischenüberschrift\nlinke Spalte unten")
    bad = ro.score_page(lines, "Zwischenüberschrift\nlinke Spalte oben\nlinke Spalte unten")
    assert (good["full_width"], good["full_width_misplaced"]) == (1, 0)
    assert bad["full_width_misplaced"] == 1


def test_precedence_on_common_lines_only():
    positions = {0: 10, 1: 5, 2: 20}
    assert ro.precedence(positions) == pytest.approx(2 / 3)
    assert ro.precedence(positions, {0, 2}) == 1.0
    assert ro.precedence({0: 1}) is None


# ── Truth construction ─────────────────────────────────────────────────────


def test_lines_go_to_the_smallest_region_and_regions_keep_their_order():
    regions = [
        region("body", [0, 0, 500, 1000], "L"),
        region("note", [0, 400, 100, 500], "L"),
        region("heading", [0, 0, 1000, 100]),
        region("body", [500, 0, 1000, 1000], "R"),
    ]
    lines = [
        line("rechts", 600, 500, 900, 540),
        line("Titel", 300, 20, 700, 60),
        line("links unten", 150, 800, 450, 840),
        line("Notiz", 10, 850, 90, 870),
        line("links oben", 150, 300, 450, 340),
        line("draussen", 10, 2100, 90, 2140),
    ]
    result, unassigned = ro.truth_lines(regions, lines, 1000, 2000)
    assert [t.text for t in result] == ["links oben", "links unten", "Notiz", "Titel", "rechts"]
    # Sections count the full-width regions before a column region.
    assert [(t.column, t.section, t.full_width) for t in result] == [
        ("L", 0, False), ("L", 0, False), ("L", 0, False), (None, 0, True), ("R", 1, False)]
    assert unassigned == [5]


def test_rows_follow_the_left_end_of_skewed_lines():
    slope = math.tan(math.radians(4))

    def skewed(text, x0, y0, x1, height=40):
        return {"text": text, "polygon": [[x0, y0], [x1, y0 + (x1 - x0) * slope],
                                          [x1, y0 + (x1 - x0) * slope + height], [x0, y0 + height]]}

    lines = [skewed(f"Zeile {i}", 260, 400 + i * 60, 2260) for i in range(4)]
    lines.append(skewed("2.", 200, 520 + 4, 245))
    ordered = [entry["text"] for entry in ro.read_rows(list(reversed(lines)))]
    assert ordered == ["Zeile 0", "Zeile 1", "2.", "Zeile 2", "Zeile 3"]


def test_truth_file_validation(tmp_path):
    def write(regions):
        path = tmp_path / "truth.json"
        path.write_text(json.dumps({"pages": [
            {"id": "t", "source": "raw/x.pdf", "page": 1, "layout": "", "regions": regions}]}))
        return path

    assert ro.load_truth(write([region("body", [0, 0, 500, 1000], "L"),
                                region("footer", [0, 900, 1000, 1000])]))
    with pytest.raises(ValueError, match="need column"):
        ro.load_truth(write([region("body", [0, 0, 500, 1000], "L"),
                             region("note", [0, 0, 50, 50])]))
    with pytest.raises(ValueError, match="take no column"):
        ro.load_truth(write([region("heading", [0, 0, 1000, 100], "L")]))
    with pytest.raises(ValueError, match="unknown role"):
        ro.load_truth(write([region("sidebar", [0, 0, 1000, 100])]))


# ── Gate ───────────────────────────────────────────────────────────────────


def summary(**values):
    return {"full_width_misplaced": 0, "full_width_duplicated": 0, "full_width_unmatched": 0,
            "interleaved_pages": 0, **values}


def test_gate_supports_unsplit_within_one_point_of_the_best_split_baseline():
    summaries = {"split-apple": summary(median=0.97), "split-tesseract": summary(median=0.95),
                 "unsplit-paddle": summary(median=0.961)}
    assert ro.decide(summaries, {}) == ("unsplit is supported", [])


@pytest.mark.parametrize("overrides, checks, reason", [
    (dict(median=0.95), {}, "more than one point below the split baseline 97.0%"),
    (dict(full_width_misplaced=1), {}, "1 full-width lines misplaced"),
    (dict(full_width_unmatched=2), {}, "2 full-width lines not found"),
    (dict(interleaved_pages=1), {}, "columns interleaved on 1 pages"),
    ({}, {"unsplit-paddle": {"t01": ["B5: fewer than 50 characters"]}}, "page checks failed"),
])
def test_gate_keeps_split_mode_on_any_failure(overrides, checks, reason):
    summaries = {"split-apple": summary(median=0.97),
                 "unsplit-paddle": summary(**{"median": 0.97, **overrides})}
    decision, reasons = ro.decide(summaries, checks)
    assert decision == "keep split mode"
    assert any(reason in r for r in reasons)


def test_gate_without_measurements_keeps_split_mode():
    assert ro.decide({"unsplit-paddle": summary(median=0.99)}, {})[0] == "keep split mode"
