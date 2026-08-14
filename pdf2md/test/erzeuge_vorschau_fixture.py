#!/usr/bin/env python3
"""Generiert die Fixture-Datei plugin/test/fixtures/beispiel-vorschau.md
aus dem reinen Zusammenbau (zusammenbau Module + fixe Eingaben).

Nutzt die reinen Funktionen seitenmarker und dokument_bauen aus zusammenbau.py,
damit eine Format-Änderung in pdf2md.py im CI erkannt wird (Fixture-Drift → Test rot).
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Modul-Pfad sicherstellen (pdf2md liegt zwei Verzeichnisse über diesem Script)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pdf2md"))

from zusammenbau import seitenmarker, dokument_bauen


# ---------------------------------------------------------------------------
# Frontmatter – exactes Nachbauen der Logik aus pdf2md.py main() Zeilen 445–465
# ---------------------------------------------------------------------------
pdf_stem = "Verwaltungsrecht AT Fall 8"
seiten_gesamt = 3
seiten_textlayer = 1
seiten_ocr = 1
seiten_diagramm = 1
n_entgleist = 0
n_verdaechtig = 0
n_korrigiert = 0
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"

# Frontmatter-Felder bauen (bedingte Felder nur bei > 0, genau wie in main())
fm_zeilen = ["---"]
# 1. titel
fm_zeilen.append(f"titel: {pdf_stem}")
# 2. quelle-pdf – immer JSON-gequotet (wie main())
fm_zeilen.append(f"quelle-pdf: {json.dumps(str(Path('raw/VwR/Verwaltungsrecht AT Fall 8.pdf')), ensure_ascii=False)}")
# 3. seiten
fm_zeilen.append(f"seiten: {seiten_gesamt}")
# 4. seiten-textlayer
fm_zeilen.append(f"seiten-textlayer: {seiten_textlayer}")
# 5. seiten-ocr
fm_zeilen.append(f"seiten-ocr: {seiten_ocr}")
# 6. seiten-diagramm – nur wenn > 0
if seiten_diagramm:
    fm_zeilen.append(f"seiten-diagramm: {seiten_diagramm}")
# 7. ocr-modell – nur wenn n_ocr > 0
fm_zeilen.append(f"ocr-modell: {MODEL}")
# 8. ocr-datum – fix (Determinismus für Fixture)
fm_zeilen.append("ocr-datum: 2026-07-30")
# 9. ocr-zeitpunkt – fix (Determinismus für Fixture)
fm_zeilen.append("ocr-zeitpunkt: 2026-08-14T15:30:29")
# vorschau-format – neues Versionsfeld
fm_zeilen.append("vorschau-format: 1")
fm_zeilen.append("---")
frontmatter_text = "\n".join(fm_zeilen) + "\n"

# ---------------------------------------------------------------------------
# Quell-Zeile
# ---------------------------------------------------------------------------
quelle_text = "Quelle: [[raw/VwR/Verwaltungsrecht AT Fall 8.pdf]]\n"

# ---------------------------------------------------------------------------
# Per-Seiten-Blöcke bauen
# Jeder Block enthält den Marker (via seitenmarker) UND den nachfolgenden Content.
# So entspricht das der Formatierung in pdf2md.py main() ablegen()-Closure.
# ---------------------------------------------------------------------------

# --- Seite 1: Textlayer-Herkunft (neu: | textlayer) ---
# Der Marker entsteht über die reine Funktion seitenmarker.
block1 = seitenmarker(1, "textlayer") + (
    "**A. Zulässigkeit der Klage**\n\n"
    "Die Klage ist zulässig, wenn der Verwaltungsrechtsweg eröffnet ist.[^1]\n\n"
    "[^1]: § 40 Abs. 1 S. 1 VwGO."
)

# --- Seite 2: OCR-Seite mit Layout-Zusatz, Codeblock, gleiche Fußnote [^1] ---
block2 = seitenmarker(2, "ocr | zweispaltig, senkrecht @48%") + (
    "**I. Verwaltungsakt**\n\n"
    "Ein Verwaltungsakt liegt vor, § 35 S. 1 VwVfG. Ein Beispiel für einen "
    "Marker in einem Codeblock, der keine Seitengrenze sein darf:\n\n"
    "```\n%% S. 99 %%\nnoch immer Seite 2\n```\n"
    "Nach dem Codeblock geht Seite 2 weiter.[^1]\n\n"
    "[^1]: BVerwGE 100, 83."
)

# --- Seite 3: Diagrammseite als Callout ---
block3 = seitenmarker(3, "diagramm") + (
    "![[Verwaltungsrecht-AT-Fall-8-s003.png]]\n\n"
    "> [!note]- Text der Seite (Reihenfolge nicht verlässlich)\n"
    "> Prüfungsaufbau Anfechtungsklage\n"
    "> >\n"
    "> 1. Zulässigkeit\n"
    "> > 2. Begründetheit"
)

# ---------------------------------------------------------------------------
# Dokument zusammenbauen und schreiben
# ---------------------------------------------------------------------------
bloecke = [block1, block2, block3]

volltext = dokument_bauen(frontmatter_text, quelle_text, bloecke)

# Pfad zur Fixture: vom Script herauf bis ins Root, dann plugin/test/fixtures/
ziel = Path(__file__).resolve().parent.parent.parent / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
ziel.write_text(volltext, encoding="utf-8")
print(f"→ Fixture geschrieben nach {ziel}")