#!/usr/bin/env python3
"""Generiert die Fixture-Datei plugin/test/fixtures/beispiel-vorschau.md
aus dem reinen Zusammenbau (zusammenbau Module + fixe Eingaben).

Nutzt die reinen Funktionen frontmatter_bauen, seitenmarker und
dokument_bauen aus zusammenbau.py — dieselben, die pdf2md.py main() aufruft.
Damit macht eine Format-Änderung in pdf2md.py den Test test_vorschau_fixture.py
rot (Fixture-Drift → CI rot).

`fixture_text()` liefert den vollständigen Text; erzeugt wird nur unter
`__main__`, damit der Test das Modul importieren kann, ohne zu schreiben.
"""

import sys
from pathlib import Path

# Modul-Pfad sicherstellen (pdf2md liegt zwei Verzeichnisse über diesem Script)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pdf2md"))

from zusammenbau import frontmatter_bauen, seitenmarker, dokument_bauen


# ---------------------------------------------------------------------------
# Feste Eingaben — der Zusammenbau selbst lebt in zusammenbau.frontmatter_bauen.
# ---------------------------------------------------------------------------
PDF_STEM = "Verwaltungsrecht AT Fall 8"
PDF_PFAD = Path("raw/VwR/Verwaltungsrecht AT Fall 8.pdf")
SEITEN = 3
SEITEN_TEXTLAYER = 1
SEITEN_OCR = 1
SEITEN_DIAGRAMM = 1
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"


def fixture_text() -> str:
    """Baut den vollständigen Vorschau-Text aus den reinen Funktionen.

    Fixe Werte (Datum/Uhrzeit), damit der eingecheckte Stand deterministisch
    ist — der Test vergleicht ihn byte-genau.
    """
    frontmatter_text = frontmatter_bauen(
        titel=PDF_STEM,
        quelle_pdf_pfad=PDF_PFAD,
        seiten=SEITEN,
        seiten_textlayer=SEITEN_TEXTLAYER,
        seiten_ocr=SEITEN_OCR,
        seiten_diagramm=SEITEN_DIAGRAMM,
        ocr_modell=MODEL,
        ocr_datum="2026-07-30",
        ocr_zeitpunkt="2026-08-14T15:30:29",
    )

    quelle_text = f"Quelle: [[{PDF_PFAD.as_posix()}]]\n"

    # Jeder Block enthält den Marker (via seitenmarker) UND den nachfolgenden
    # Content — genau wie in pdf2md.py main() ablegen()-Closure.
    #
    # Seite 1: Textlayer-Herkunft (neu: | textlayer)
    block1 = seitenmarker(1, "textlayer") + (
        "**A. Zulässigkeit der Klage**\n\n"
        "Die Klage ist zulässig, wenn der Verwaltungsrechtsweg eröffnet ist.[^1]\n\n"
        "[^1]: § 40 Abs. 1 S. 1 VwGO."
    )
    # Seite 2: OCR-Seite mit Layout-Zusatz, Codeblock, gleiche Fußnote [^1]
    block2 = seitenmarker(2, "ocr | zweispaltig, senkrecht @48%") + (
        "**I. Verwaltungsakt**\n\n"
        "Ein Verwaltungsakt liegt vor, § 35 S. 1 VwVfG. Ein Beispiel für einen "
        "Marker in einem Codeblock, der keine Seitengrenze sein darf:\n\n"
        "```\n%% S. 99 %%\nnoch immer Seite 2\n```\n"
        "Nach dem Codeblock geht Seite 2 weiter.[^1]\n\n"
        "[^1]: BVerwGE 100, 83."
    )
    # Seite 3: Diagrammseite als Callout
    block3 = seitenmarker(3, "diagramm") + (
        "![[Verwaltungsrecht-AT-Fall-8-s003.png]]\n\n"
        "> [!note]- Text der Seite (Reihenfolge nicht verlässlich)\n"
        "> Prüfungsaufbau Anfechtungsklage\n"
        "> >\n"
        "> 1. Zulässigkeit\n"
        "> > 2. Begründetheit"
    )

    return dokument_bauen(frontmatter_text, quelle_text, [block1, block2, block3])


if __name__ == "__main__":
    ziel = Path(__file__).resolve().parent.parent.parent / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
    ziel.write_text(fixture_text(), encoding="utf-8")
    print(f"→ Fixture geschrieben nach {ziel}")