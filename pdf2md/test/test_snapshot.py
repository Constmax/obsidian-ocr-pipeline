#!/usr/bin/env python3
"""Der Golden-Snapshot gegen die geteilten Module.

erzeuge_snapshot.py haelt Eingabe und Ausgabe aller reinen Funktionen in
daten/snapshot.json fest. Dieser Test rechnet denselben Stand gegen die
Module neu und stellt sicher, dass nichts abweicht — jede
Verhaltensaenderung nach dem Split (Issue #8) schlaeggt hier an.

  python3 -m pytest pdf2md/test/test_snapshot.py
"""
import json
from pathlib import Path

from erzeuge_snapshot import ergebnis, module, vergleichen

DATEN = Path(__file__).resolve().parent / "daten" / "snapshot.json"


def test_snapshot_unveraendert():
    alt = json.loads(DATEN.read_text(encoding="utf-8"))
    fehler = vergleichen(ergebnis(module()), alt)
    assert not fehler, "\n".join(
        f"{pfad}\n  neu: {n!r}\n  alt: {a!r}" for pfad, n, a in fehler[:5])
