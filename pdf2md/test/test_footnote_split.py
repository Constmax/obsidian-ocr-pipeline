#!/usr/bin/env python3
"""Footnote definition splitting: never inside citations.

Pure: no fitz, no model, no vault.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assembly import _footnotes_obsidian, _split_footnote_defs


def test_citation_number_does_not_start_definition():
    parts = _split_footnote_defs(
        "8 VGH Mannheim; Kopp/Ramsauer, § 35 VwVfG, Rn. 18. "
        "9 Kopp/Ramsauer, § 35 VwVfG, Rn. 56.")
    assert parts == ["8 VGH Mannheim; Kopp/Ramsauer, § 35 VwVfG, Rn. 18. ",
                     "9 Kopp/Ramsauer, § 35 VwVfG, Rn. 56."]


def test_comma_before_number_does_not_split():
    parts = _split_footnote_defs(
        "6 Stellhorn, BayVBl. 2016, 77 (78); grundlegend VG München.")
    assert parts == ["6 Stellhorn, BayVBl. 2016, 77 (78); "
                     "grundlegend VG München."]


def test_roman_numeral_citation_does_not_split():
    parts = _split_footnote_defs("9 Kopp/Ramsauer, Art. 3 III 1 GG.")
    assert parts == ["9 Kopp/Ramsauer, Art. 3 III 1 GG."]


def test_real_definitions_still_split():
    out = _footnotes_obsidian(
        ["Text.8", "8 VGH Mannheim, § 35 VwVfG, Rn. 18.",
         "9 Kopp/Ramsauer, Art. 3 III 1 GG."])
    assert out == ["Text.[^8]", "",
                   "[^8]: VGH Mannheim, § 35 VwVfG, Rn. 18.",
                   "[^9]: Kopp/Ramsauer, Art. 3 III 1 GG."]


def test_no_phantom_definitions():
    out = _footnotes_obsidian(
        ["Text.29", "29 EuGH, JuS 2013, 1051; Stellhorn, BayVBl. 2016, "
         "77 (78); Michl, Jura 2015."])
    assert out == ["Text.[^29]", "",
                   "[^29]: EuGH, JuS 2013, 1051; Stellhorn, BayVBl. 2016, "
                   "77 (78); Michl, Jura 2015."]


def test_duplicate_number_appends_with_its_digits():
    # A number stands once on a page; a second definition keeps its digits
    # so no text is lost (Issue #14).
    out = _footnotes_obsidian(["Text.1", "1 First source.", "1 Second source."])
    assert out == ["Text.[^1]", "", "[^1]: First source. 1 Second source."]


def test_mark_after_abbreviation_ending_in_roman_letter():
    out = _footnotes_obsidian(
        ["Vorlage nach Art. 267 AEUV12 ist zulässig.", "12 Vgl. EuGH."])
    assert out == ["Vorlage nach Art. 267 AEUV[^12] ist zulässig.", "",
                   "[^12]: Vgl. EuGH."]
