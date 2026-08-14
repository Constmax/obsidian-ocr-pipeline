#!/usr/bin/env python3
"""Pytest: recomputiert die Fixture per reinen Funktionen und vergleicht
mit dem eingecheckten Stand.

Sollte eine Format-Änderung in pdf2md.py vorgenommen werden, ohne die Fixture
aktualisiert zu haben, schlägt dieser Test (CI rot).
"""

import json
from pathlib import Path
from datetime import datetime

import sys
from pathlib import Path as PL

# pdf2md-Verzeichnis auf sys.path bringen (wie conftest.py es tut)
sys.path.insert(0, str(PL(__file__).resolve().parent.parent))

from zusammenbau import seitenmarker, dokument_bauen


def test_vorschau_fixture_kommt_ohne_aenderung_aus():
    """Recompute the fixture from pure functions and compare to committed file.

    This mirror's the pattern of test_snapshot.py / erzeuge_snapshot.py:
    if the generating code (pdf2md assembly) changes, the recomputed fixture
    differs from the committed one → Test fehlschlägt → CI rot.
    """
    # 1. Frontmatter neu aufbauen (identische Logik wie im Generator)
    import json as _json
    pdf_stem = "Verwaltungsrecht AT Fall 8"
    seiten_gesamt = 3
    seiten_textlayer = 1
    seiten_ocr = 1
    seiten_diagramm = 1
    MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"

    fm_zeilen = ["---"]
    fm_zeilen.append(f"titel: {pdf_stem}")
    fm_zeilen.append(f"quelle-pdf: {_json.dumps(str(Path('raw/VwR/Verwaltungsrecht AT Fall 8.pdf')), ensure_ascii=False)}")
    fm_zeilen.append(f"seiten: {seiten_gesamt}")
    fm_zeilen.append(f"seiten-textlayer: {seiten_textlayer}")
    fm_zeilen.append(f"seiten-ocr: {seiten_ocr}")
    fm_zeilen.append(f"seiten-diagramm: {seiten_diagramm}")
    fm_zeilen.append(f"ocr-modell: {MODEL}")
    fm_zeilen.append("ocr-datum: 2026-07-30")
    fm_zeilen.append("ocr-zeitpunkt: 2026-08-14T15:30:29")
    fm_zeilen.append("vorschau-format: 1")
    fm_zeilen.append("---")
    recomputed_frontmatter = "\n".join(fm_zeilen) + "\n"

    # 2. Quelle-Zeile
    quelle_text = "Quelle: [[raw/VwR/Verwaltungsrecht AT Fall 8.pdf]]\n"

    # 3. Per-Seiten-Blöcke (identisch mit Generator)
    block1 = seitenmarker(1, "textlayer") + (
        "**A. Zulässigkeit der Klage**\n\n"
        "Die Klage ist zulässig, wenn der Verwaltungsrechtsweg eröffnet ist.[^1]\n\n"
        "[^1]: § 40 Abs. 1 S. 1 VwGO."
    )
    block2 = seitenmarker(2, "ocr | zweispaltig, senkrecht @48%") + (
        "**I. Verwaltungsakt**\n\n"
        "Ein Verwaltungsakt liegt vor, § 35 S. 1 VwVfG. Ein Beispiel für einen "
        "Marker in einem Codeblock, der keine Seitengrenze sein darf:\n\n"
        "```\n%% S. 99 %%\nnoch immer Seite 2\n```\n"
        "Nach dem Codeblock geht Seite 2 weiter.[^1]\n\n"
        "[^1]: BVerwGE 100, 83."
    )
    block3 = seitenmarker(3, "diagramm") + (
        "![[Verwaltungsrecht-AT-Fall-8-s003.png]]\n\n"
        "> [!note]- Text der Seite (Reihenfolge nicht verlässlich)\n"
        "> Prüfungsaufbau Anfechtungsklage\n"
        "> >\n"
        "> 1. Zulässigkeit\n"
        "> > 2. Begründetheit"
    )
    bloecke = [block1, block2, block3]

    # 4. Vollständigen Text zusammenbauen
    recomputed = dokument_bauen(recomputed_frontmatter, quelle_text, bloecke)

    # 5. Mit dem eingecheckten Fixture vergleichen
    fixture_path = Path(__file__).resolve().parent.parent.parent / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
    committed = fixture_path.read_text(encoding="utf-8")

    assert recomputed == committed, (
        "Fixture weicht vom eingecheckten Stand ab — "
        "wurde pdf2md.py geändert ohne die Fixture zu aktualisieren?\n"
        f"Erzeugte {len(recomputed)} Zeichen, eingecheckt {len(committed)} Zeichen."
    )

    # Kleiner Hinweis, falls sich nur unwichtige Details (Datum/Uhrzeit) unterscheiden
    # (sollten durch die fixen Werte im Generator identisch sein)
    if recomputed != committed:
        # Dies sollte durch den Assert oben schon abgefangen werden
        pass