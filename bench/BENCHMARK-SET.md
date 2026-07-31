# Benchmark-Set — OCR-Kandidatenvergleich

Je Seite `<name>.png` (300 dpi, Input fuer die Kandidaten) und
`<name>.baseline.txt` (was die Tesseract-Pipeline heute liefert).

## 01-zweispalter-handschrift
- Quelle: `raw/OeR/Verwaltungsrecht-AT/Verwaltungsprozessrecht.pdf` — PDF-Seite 10
- Testet: Zweispalter, dichter Kleindruck, Fussnotenblock, handschriftliche Marginalie, leichte Schraeglage
- Baseline (Tesseract heute): 8591 Zeichen

## 02-zweispalter-dicht
- Quelle: `raw/OeR/Verwaltungsrecht-AT/Allgemeines-Verwaltungsrecht-Skript.pdf` — PDF-Seite 15
- Testet: Zweispalter, verschachtelte Gliederung (3./a./b.), eingerueckte Zitatbloecke, Fussnoten
- Baseline (Tesseract heute): 7041 Zeichen

## 03-durchschlag-handschrift
- Quelle: `raw/StR/Strafrecht-AT/Strafrecht AT II.pdf` — PDF-Seite 8
- Testet: HAERTEFALL: Rueckseiten-Durchschlag als Geistertext, Handschrift, Ordnerrand, viel Weissraum
- Baseline (Tesseract heute): 755 Zeichen

## 04-einspaltig-sauber
- Quelle: `raw/ZR/Arbeitsrecht/skript-arbeitsrecht-xxl-issa.pdf` — PDF-Seite 12
- Testet: Baseline: einspaltig, sauber, Blocksatz, Fettungen, Urteilszitate
- Baseline (Tesseract heute): 2709 Zeichen

## 05-diagramm-sekundaeranspruch
- Quelle: `raw/ZR/Schuldrecht-AT/schuldrecht-at-teil-2-sekundaeransprueche-2026.pdf` — PDF-Seite 2
- Testet: Diagramm: Anspruchsbaum + 4-Spalten-Zuordnung Ruecktrittsrecht (Pfeile tragen die Bedeutung)
- Baseline (Tesseract heute): 912 Zeichen

## 06-diagramm-kausalitaet
- Quelle: `raw/ZR/Schuldrecht-AT/schuldrecht-at-teil-5-schadensrecht-2026.pdf` — PDF-Seite 5
- Testet: Diagramm: Kausalitaetsbaum, Kaesten mit Fan-Out-Pfeilen
- Baseline (Tesseract heute): 989 Zeichen
