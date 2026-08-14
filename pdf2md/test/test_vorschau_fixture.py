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
