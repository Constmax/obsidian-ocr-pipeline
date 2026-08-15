#!/usr/bin/env python3
"""Pytest: recomputiert die Fixture über die reinen Funktionen und vergleicht
mit dem eingecheckten Stand.

Sollte eine Format-Änderung in pdf2md.py vorgenommen werden, ohne die Fixture
aktualisiert zu haben, schlägt dieser Test (CI rot). Gleiche Mechanik wie
test_snapshot.py: erzeuge_vorschau_fixture.fixture_text() liefert den Stand,
der gegen die eingecheckte Datei läuft.
"""

from pathlib import Path

from erzeuge_vorschau_fixture import fixture_text
from zusammenbau import frontmatter_bauen


def test_frontmatter_abgebrochen_nur_wenn_gesetzt():
    """Issue #25: abgebrochen-Feld erscheint nur bei einem Abbruch."""
    ohne = frontmatter_bauen(
        titel="t", quelle_pdf_pfad=Path("a.pdf"), seiten=10,
        seiten_textlayer=5, seiten_ocr=5,
        ocr_datum="2026-08-15", ocr_zeitpunkt="2026-08-15T10:00:00")
    assert "abgebrochen:" not in ohne

    mit = frontmatter_bauen(
        titel="t", quelle_pdf_pfad=Path("a.pdf"), seiten=10,
        seiten_textlayer=5, seiten_ocr=5,
        ocr_datum="2026-08-15", ocr_zeitpunkt="2026-08-15T10:00:00",
        abgebrochen="seite 5 von 10")
    assert "abgebrochen: seite 5 von 10" in mit


def test_vorschau_fixture_kommt_ohne_aenderung_aus():
    recomputed = fixture_text()

    fixture_path = (
        Path(__file__).resolve().parent.parent.parent
        / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
    )
    committed = fixture_path.read_text(encoding="utf-8")

    assert recomputed == committed, (
        "Fixture weicht vom eingecheckten Stand ab — "
        "wurde pdf2md.py geändert ohne die Fixture zu aktualisieren?\n"
        f"Erzeugte {len(recomputed)} Zeichen, eingecheckt {len(committed)} Zeichen."
    )
