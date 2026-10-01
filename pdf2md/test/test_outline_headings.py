#!/usr/bin/env python3
"""Plain outline labels become headings unless the line ends like body text (#164).

  python3 -m pytest pdf2md/test/test_outline_headings.py
"""
from assembly import _format_headings as format_headings


def test_heading_ending_in_a_legal_form():
    assert format_headings(["B. Ansprüche gegen den e.V.",
                            "C. Ansprüche gegen den e. V."]) \
        == ["## B. Ansprüche gegen den e.V.",
            "## C. Ansprüche gegen den e. V."]


def test_sentence_ending_in_another_abbreviation_stays_body_text():
    assert format_headings(["a) Das gilt nach h.M.",
                            "b) Das gilt, z. B.",
                            "A. Das gilt (s.o.)."]) \
        == ["**a)** Das gilt nach h.M.",
            "**b)** Das gilt, z. B.",
            "**A.** Das gilt (s.o.)."]


def test_heading_phrased_as_a_question():
    assert format_headings(["II. Anspruch aus § 1 XG?",
                            "d) Einwand nach § 2 XG?",
                            "III. Anspruch aus „§ 3 XG?“"]) \
        == ["### II. Anspruch aus § 1 XG?",
            "##### d) Einwand nach § 2 XG?",
            "### III. Anspruch aus „§ 3 XG?“"]


def test_numbered_question_stays_body_text():
    assert format_headings(["2. Wer haftet hier wofür?",
                            "3) Wer haftet hier?",
                            "(4) Wer haftet hier?"]) \
        == ["2. Wer haftet hier wofür?",
            "3) Wer haftet hier?",
            "**(4)** Wer haftet hier?"]


def test_sentence_still_stays_body_text():
    assert format_headings(["a) Der Anspruch besteht."]) \
        == ["**a)** Der Anspruch besteht."]
