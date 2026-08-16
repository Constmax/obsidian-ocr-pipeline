#!/usr/bin/env python3
"""Seam deduplication tests.

  python3 -m pytest pdf2md/test/test_naht.py
"""
from ocr import trim_overlap


def z(text, y=0):
    return [text, (0, y, 1000, y + 10), False]


def test_continuation_preserved():
    top = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen "
             "ist L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
    bottom = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
                "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
                "Paragraf 935 BGB."),
              z("Fussnote 13: BGH NJW 2014, 1524.")]
    out = trim_overlap(top, bottom)
    assert out[0][0] == "dem M abhanden gekommen ist, richtet sich nach " \
                        "Paragraf 935 BGB."


def test_followup_line_untouched():
    top = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen "
             "ist L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
    bottom = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
                "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
                "Paragraf 935 BGB."),
              z("Fussnote 13: BGH NJW 2014, 1524.")]
    out = trim_overlap(top, bottom)
    assert len(out) == 2


def test_full_duplicate_removed():
    top = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.")]
    bottom = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor."),
              z("Ein Schaden ist entstanden.")]
    assert [x[0] for x in trim_overlap(top, bottom)] \
        == ["Ein Schaden ist entstanden."]


def test_without_seam_unchanged():
    top = [z("Der Anspruch ist nach Paragraf 985 BGB begruendet und faellig.")]
    bottom = [z("Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung.")]
    assert [x[0] for x in trim_overlap(top, bottom)] \
        == ["Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung."]


def test_markers_do_not_trigger_trim():
    top = [z("aa) bb) cc) dd) ee) ff)")]
    bottom = [z("aa) bb) cc) dd) ee) ff)"), z("Weiterer Text.")]
    assert len(trim_overlap(top, bottom)) == 2


def test_rest_preserved_despite_reading_error():
    top = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
             "Besitzverschaiung folgenden Anscheins nicht nachgeforscht "
             "hat.")]
    bottom = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
                "Besitzverschaffung folgenden Anscheins nicht nachgeforscht "
                "hat. Fuer eine Nachforschungsobliegenheit spricht wenig.")]
    out = trim_overlap(top, bottom)
    assert "Nachforschungsobliegenheit" in out[0][0]


def test_empty_left():
    bottom = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
                "Besitzverschaffung folgenden Anscheins nicht nachgeforscht "
                "hat. Fuer eine Nachforschungsobliegenheit spricht wenig.")]
    assert trim_overlap([], bottom) == bottom


def test_empty_right():
    top = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
             "Besitzverschaiung folgenden Anscheins nicht nachgeforscht "
             "hat.")]
    assert trim_overlap(top, []) == []
