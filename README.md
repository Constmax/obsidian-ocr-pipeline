# obsidian-ocr-pipeline

OCR-Pipeline für gescannte juristische Skripte, Fälle und Klausuren — von der
Ordnerfotografie bis zur durchsuchbaren Markdown-Seite im Obsidian-Vault.

Entstanden als Werkzeugkasten innerhalb eines Jura-Vaults, hier herausgelöst,
weil es Code ist und in ein Notizen-Repo nicht gehört. **Fernziel: ein
Obsidian-Plugin** — siehe [docs/plugin-roadmap.md](docs/plugin-roadmap.md).

## Zwei Stufen

| | Stufe 1 — `bin/` | Stufe 2 — `pdf2md/` |
|---|---|---|
| Ausgabe | durchsuchbares PDF (Textlayer) | Markdown |
| Engine | Tesseract / Apple Vision (via ocrmypdf) | PaddleOCR-VL 1.5 4bit via MLX |
| Zustand | **stabil, im täglichen Einsatz** | funktioniert, Zusammenbau-Schicht jung |
| Laufzeit | Sekunden bis Minuten/Datei | 15–60 s/Seite auf M1 |
| Plattform | macOS + Linux (Apple-Engine nur macOS) | Apple Silicon (MLX) |

Die Stufen sind unabhängig. Stufe 1 macht Scans durchsuchbar und archivfähig,
Stufe 2 macht sie **lesbar in Obsidian**. Für das Plugin ist Stufe 2 der
interessante Teil.

## Stufe 1 — PDF → durchsuchbares PDF

Vier CLIs über einer gemeinsamen Bibliothek (`bin/pdf-lib.sh`):

```
Input                                 → Script          → Output
─────────────────────────────────────────────────────────────────
Bilder (jpg/png/tiff) + evtl. PDFs    → pdf-workflow    → 1 PDF
Mehrere PDFs zu einem zusammenfassen  → pdf-combine     → 1 PDF
Ordner voller PDFs, Batch             → pdf-auto        → n PDFs
Bestehende Datei neu verarbeiten      → reprocess-raw   → dieselbe Datei
```

Was die Pipeline über nacktes `ocrmypdf` hinaus tut:

- **MediaBox-Korrektur** — Hemmer-PDFs kommen mit 72-PPI-Seitengeometrie; ohne
  Korrektur rastert Ghostscript bei 300 DPI 140-Megapixel-Seiten.
- **Downscaling vor OCR** — hält den RAM-Bedarf unter der macOS-OOM-Grenze.
- **Spaltentrennung** (`--split-columns`) — zweispaltige Skriptseiten werden
  erkannt, in Halbseiten geschnitten, einzeln OCR-t und wieder zusammengesetzt.
  Ohne das vermischt jede Engine die Spalten im Textlayer. Die Erkennung sitzt
  in `bin/column_tools.py`; die Schwellwerte sind an 14 handgeprüften Seiten
  kalibriert (Begründung in [bench/ERGEBNIS.md](bench/ERGEBNIS.md)).
- **Quality-Gate mit Auto-Retry** — misst das Ergebnis und läuft bei schlechtem
  Ausgang automatisch mit der anderen Engine nochmal.
- **B5-Gate** in `reprocess-raw` — überschreibt das Original nur, wenn
  Seitenzahl exakt erhalten ist *und* jede einzelne Seite Text hat. Ein
  dokumentweiter Durchschnitt versteckt sonst eine komplett leere Seite.

Warum das B5-Gate existiert: [docs/BUGREPORT-2026-07-06-split-merge.md](docs/BUGREPORT-2026-07-06-split-merge.md).

## Stufe 2 — PDF → Markdown

`pdf2md/pdf2md.py` rendert jede Seite, kachelt bei hoher Textdichte, schickt sie
durch PaddleOCR-VL und baut die Zeilen anhand ihrer `<|LOC|>`-Bounding-Boxes zu
Markdown zusammen. Seiten mit brauchbarem Textlayer werden verlustfrei
übernommen statt neu gelesen; Diagrammseiten kommen als Bild plus Text in einem
eingeklappten Callout.

Ergebnis der Engine-Auswahl, gemessen auf 6 repräsentativen Seiten:

| | PaddlePaddle CPU | MLX ohne Kachelung | **MLX + Kachelung** |
|---|---|---|---|
| Sek./Seite, dichte Zweispalter | 9.226 | 111 (kollabiert) | **54–61** |
| Peak-RSS | 5.679 MB | 1.138 MB | **1.138 MB** |
| Hochrechnung 2.922 Seiten | ~312 Tage | unbrauchbar | **~30 h** |

Vollständig mit Fehlerklassen: [bench/ERGEBNIS.md](bench/ERGEBNIS.md).

**Wichtig:** OCR-Wortfehler (`Verhaltungsakte`, `Rechtsbehelsfebehrung`) sind
nicht mechanisch korrigierbar. Die Original-PDFs bleiben die Quelle; jede
erzeugte `.md` trägt einen Rücksprung-Link im Frontmatter.

## Installation

```bash
./install.sh
```

Legt Symlinks für `pdf-auto`, `pdf-combine`, `pdf-workflow`, `reprocess-raw` in
`~/bin` an und prüft die Abhängigkeiten. Details und Troubleshooting:
[docs/installation.md](docs/installation.md).

Kurzfassung der Systempakete:

```bash
brew install ocrmypdf img2pdf qpdf tesseract-lang ghostscript pngquant jbig2enc unpaper
```

Für Stufe 2 zusätzlich ein Python-3.12-venv mit `mlx-vlm` — `pdf2md/setup.sh`
richtet es ein (`VAULT_ROOT=<pfad> ./pdf2md/setup.sh`).

## Verwendung

```bash
# Ordner mit Scans batch-verarbeiten, Originale archivieren
pdf-auto ~/scans --cleanup --engine tesseract

# Zweispaltiges Skript sauber durch die Pipeline
pdf-combine ~/scans/skript skript-arbeitsrecht --split-columns

# Bestehende Datei neu verarbeiten, nur bei bestandenem Gate überschreiben
reprocess-raw "raw/StR/Rep-Faelle/fall-01.pdf" --force-ocr --split-columns

# PDF → Markdown
source .venv-mlxocr/bin/activate
python pdf2md/pdf2md.py "raw/ZR/skript.pdf" --out _ocr-vorschau
```

Komplette Flag-Referenz: [docs/scripts-detail.md](docs/scripts-detail.md).

## OCR-Engine wählen (Stufe 1)

| Situation | Engine | Grund |
|---|---|---|
| Fließtext, Urteile, saubere Scans | `apple` | schnell, exzellente Umlaute |
| Zweispaltiges Layout (Hemmer, Kaiser) | `tesseract` + `--split-columns` | Vision verliert die Spaltenstruktur |
| Handschriftliche Notizen | `apple` | Vision liest Handschrift, Tesseract nicht |
| Tabellen mit Gitternetz | `tesseract` | PSM 1 erkennt Zellstruktur besser |

## Repo-Aufbau

```
bin/         Stufe 1 — pdf-lib.sh + 4 CLIs + column_tools.py
pdf2md/      Stufe 2 — pdf2md.py, setup.sh
bench/       Benchmark-Harness und Messergebnisse
docs/        Installation, Flag-Referenz, Bugreport, Vault-Integration
skill/       Claude-Code-Skill (SKILL.md) zum Einbinden in einen Vault
```

Die Benchmark-**Seitenbilder** liegen bewusst nicht im Repo: sie sind Scans aus
urheberrechtlich geschütztem Kursmaterial und mit `bench/build_bench.py` aus dem
eigenen Bestand reproduzierbar. Siehe [bench/BENCHMARK-SET.md](bench/BENCHMARK-SET.md).

## Stand

Stufe 1 läuft produktiv über ~1.500 Scanseiten. Stufe 2 ist entschieden und
implementiert; die Markdown-Zusammenbau-Schicht ist die jüngste und
unfertigste Komponente. Gemessen: 98,5 % Wortgenauigkeit auf gesunden Seiten,
aber **15 % der Seiten entgleisen** (Wiederholungsschleife oder Abbruch) und
drücken die Gesamtzahl auf 93,3 %. Das Abfangen dieser Fälle ist der nächste
Schritt.

Offene Fehler, was noch nicht gebaut ist und die Reihenfolge:
[docs/plugin-roadmap.md](docs/plugin-roadmap.md).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
