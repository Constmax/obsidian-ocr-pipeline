#!/usr/bin/env python3
"""Plain outline labels become headings unless the line ends a sentence (#164).

  python3 -m pytest pdf2md/test/test_outline_headings.py
"""
from assembly import format_headings


def test_heading_ending_in_an_abbreviation():
    assert format_headings(["B. Anspruch der Eltern gegen den e.V."]) \
        == ["## B. Anspruch der Eltern gegen den e.V."]


def test_heading_phrased_as_a_question():
    assert format_headings(["III. Anspruch aus § 831 BGB?",
                            "e) Mitverschulden des A, § 254 BGB?"]) \
        == ["### III. Anspruch aus § 831 BGB?",
            "##### e) Mitverschulden des A, § 254 BGB?"]


def test_question_closed_by_a_quote():
    assert format_headings(["III. Anspruch aus „§ 831 BGB?“"]) \
        == ["### III. Anspruch aus „§ 831 BGB?“"]


def test_numbered_question_stays_body_text():
    assert format_headings(["1. Warum entfällt für den Betreuer § 31 BGB?",
                            "(1) Warum entfällt § 31 BGB?"]) \
        == ["1. Warum entfällt für den Betreuer § 31 BGB?",
            "**(1)** Warum entfällt § 31 BGB?"]


def test_sentence_still_stays_body_text():
    assert format_headings(["a) Eine Körperverletzung liegt vor."]) \
        == ["**a)** Eine Körperverletzung liegt vor."]
    assert format_headings(["a) Der Verein haftet (s.o.)."]) \
        == ["**a)** Der Verein haftet (s.o.)."]
