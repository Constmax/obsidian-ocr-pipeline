# obsidian-ocr-pipeline

![CI](https://github.com/Constmax/obsidian-ocr-pipeline/actions/workflows/ci.yml/badge.svg)

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

Entgleist die Generierung — eine Wortfolge wiederholt sich, ein Zähler läuft
davon, die Ausgabe bricht ab —, wird das erkannt, die Kachel feiner geschnitten
neu gerechnet und die bessere Fassung genommen. Was sich nicht reparieren lässt,
steht im Lauf-Protokoll; still verworfen wird nichts.

Ergebnis der Engine-Auswahl, gemessen auf 6 repräsentativen Seiten:

| | PaddlePaddle CPU | MLX ohne Kachelung | **MLX + Kachelung** |
|---|---|---|---|
| Sek./Seite, dichte Zweispalter | 9.226 | 111 (kollabiert) | **54–61** |
| Peak-RSS | 5.679 MB | 1.138 MB | **1.138 MB** |
| Hochrechnung 2.922 Seiten | ~312 Tage | unbrauchbar | **~30 h** |

Genauigkeit, gemessen auf 40 Seiten quer durch den Bestand gegen den Textlayer
derselben Seiten (`bench/bench_ocr.py`):

| | Wortgenauigkeit | Zitattreue | Reihenfolge |
|---|---|---|---|
| alle 40 Seiten | **98,5 %** | **92,4 %** | **93,7 %** |

Vollständig mit Fehlerklassen und Vorher-Zahlen:
[bench/ERGEBNIS.md](bench/ERGEBNIS.md).

Zum Schluss läuft ein **Wörterbuchabgleich** über die OCR-Seiten (nicht über
die exakten Textlayer-Seiten). Was kein Wörterbuch kennt, steht als `⌕`-Zeile
im Protokoll und als `woerter-verdaechtig` im Frontmatter — der
Begutachtungsdurchgang weiß damit, wonach er auf der Seite suchen soll.
Ersetzt wird nur auf ausdrückliches Verlangen (`--woerterbuch-korrigieren`)
und nur, wenn genau eine Variante aus der Verwechslungstabelle im Wörterbuch
steht; bei zwei Lesarten bleibt das Wort stehen. Zitate, Zahlen und
Abkürzungen werden gar nicht erst geprüft. Gemessen an 202 Wörtern echter
Gutachtenprosa: 0 Fehlalarme, 6 von 7 eingestreuten Lesefehlern gefunden.
Einrichtung und Grenzen: [docs/scripts-detail.md](docs/scripts-detail.md).

**Wichtig:** Der Abgleich findet Nicht-Wörter (`Besitzverschaiung`,
`Leistuug`), keine morphologisch wohlgeformten Scheinwörter
(`Verhaltungsakte`) und keine Wortauslassungen. Die Original-PDFs bleiben die
Quelle; jede erzeugte `.md` trägt einen Rücksprung-Link im Frontmatter.

## Stufe 3 — Begutachtung

`plugin/` ist ein Obsidian-Plugin: eine dreispaltige Ansicht, die die erzeugte
Markdown-Datei seitenweise neben das Original-PDF stellt — links die
Vorschau-Liste, mittig die Originalseiten, rechts das Markdown, scrollgekoppelt,
mit **Annehmen / Ablehnen** per Tastatur und Rückgängig. Der
Begutachtungs-Durchgang, der heute aus zwei Fenstern nebeneinander besteht,
bekommt damit eine Oberfläche. Zweck und Bedienung:
[docs/review-ansicht.md](docs/review-ansicht.md).

## Neuer Laptop — Einmal-Setup

Vier Schritte, dann ist alles einsatzbereit (Stufe 1 + 2 + 3, Plugin inklusive):

```bash
gh auth login                          # einmalig, Zugriff aufs private Repo
gh repo clone Constmax/obsidian-ocr-pipeline <vault>/obsidian-ocr-pipeline
cd <vault>/obsidian-ocr-pipeline && ./setup.sh
# Obsidian öffnen (Cmd+R) — Plugin ist automatisch aktiv
```

`setup.sh` installiert selbst: Xcode-CLT-Hinweis, Homebrew (falls fehlt),
Systempakete per `brew bundle` (Brewfile), ocrmypdf-venv mit
Apple-Vision-Plugin, `~/bin`-Verknüpfungen inkl. PATH (`.zshrc`), MLX-venv für
Stufe 2 und das Plugin (Kopie, ohne Node) — und aktiviert es. Idempotent: nach
`git pull` einfach erneut ausführen. Auf Intel-Macs ist Stufe 2 (MLX) offen;
das Setup warnt dann nur.

### Bausteine

Die Einzel-Skripte, die `setup.sh` intern zusammenruft — nützlich einzeln nach
einem `git pull`, ausdrücklich **kein paralleler Installationsweg**:

```bash
./install.sh                                # Stufe-1-Symlinks nach ~/bin + Prüfung
VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh   # Plugin (Stufe 3)
```

- `install.sh` verlinkt `pdf-auto`, `pdf-combine`, `pdf-workflow`,
  `reprocess-raw` nach `~/bin` und prüft die Abhängigkeiten.
- `plugin/install-plugin.sh` kopiert `main.js`, `manifest.json` und
  `styles.css` nach `$VAULT_ROOT/.obsidian/plugins/ocr-vorschau/` (ohne Build,
  kein Node nötig; `--build` baut aus `src/` auf der Dev-Maschine). Kopie ist
  Default (Symlinks verlieren in iCloud Dateien), `--symlink` bleibt als
  Dev-Opt-in.

Systempakete installiert `setup.sh` über das `brew bundle` aus dem
[Brewfile](Brewfile). `ocrmypdf` steht dort bewusst **nicht**: der brew-Build
hat auf macOS den pyexpat-Bug — ocrmypdf kommt deshalb aus einem eigenen
Python-3.12-venv (`~/.venvs/ocrmypdf`, via uv). `poppler` (pdfinfo,
pdftotext) gehört dagegen dazu. Troubleshooting:
[docs/installation.md](docs/installation.md).

### venvs

Stufe 1 und 2 laufen in eigenen venvs unter `~/.venvs/` — `ocrmypdf`
(Python 3.12, via uv) und `mlxocr` (Stufe 2). Diese Stelle benennt die
Konvention; überschreibbar via `VENV_ROOT=<pfad> ./setup.sh`. Auch
`bin/pdf2md`, `bin/pdf-lib.sh` und `bin/reprocess-raw.sh` leiten ihre
Kandidaten-Pfade daraus ab.

## Verwendung

```bash
# Ordner mit Scans batch-verarbeiten, Originale archivieren
pdf-auto ~/scans --cleanup --engine tesseract

# Zweispaltiges Skript sauber durch die Pipeline
pdf-combine ~/scans/skript skript-arbeitsrecht --split-columns

# Bestehende Datei neu verarbeiten, nur bei bestandenem Gate überschreiben
reprocess-raw "raw/StR/Rep-Faelle/fall-01.pdf" --force-ocr --split-columns

# PDF → Markdown
pdf2md "raw/ZR/skript.pdf" --out _ocr-vorschau

# PDF → Markdown, nur bestimmte Seiten
pdf2md "raw/ZR/skript.pdf" --seiten "1,3-5,8" --out _ocr-vorschau
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
pdf2md/      Stufe 2 — pdf2md.py (CLI) + layout.py + ocr.py + zusammenbau.py
             + woerterbuch.py, Testsuite in pdf2md/test/ (pytest, ohne MLX)
bench/       Benchmark-Harness und Messergebnisse
plugin/      Stufe 3 — Abgleich-Ansicht (Obsidian-Plugin, TypeScript)
docs/        Installation, Flag-Referenz, Bugreport, Vault-Integration
skill/       Claude-Code-Skill (SKILL.md) zum Einbinden in einen Vault
setup.sh     Einmal-Setup (Einstiegstür): Brewfile + venvs + Links + Plugin
Brewfile     Systempakete für das Setup (brew bundle)
```

Die Benchmark-**Seitenbilder** liegen bewusst nicht im Repo: sie sind Scans aus
urheberrechtlich geschütztem Kursmaterial und mit `bench/build_bench.py` aus dem
eigenen Bestand reproduzierbar. Siehe [bench/BENCHMARK-SET.md](bench/BENCHMARK-SET.md).

## CI

Jeder Pull Request und jeder Push auf `main` läuft durch drei unabhängige Jobs
(`.github/workflows/ci.yml`). Feature-Branches laufen über ihren PR — ein
unbeschränktes `push` würde jeden Job doppelt starten.

- **plugin** — `npm ci`, tsc, eslint, Tests, Build und der Kern: `main.js` muss
  versioniert sein *und* dem Build aus `src/` entsprechen. Ein PR, der `src/`
  ändert ohne neu zu bauen, wird damit rot — ebenso einer, der `main.js` aus
  der Versionierung nimmt.
- **shell** — shellcheck (feste Version) über alle neun Shell-Skripte
  (`setup.sh`, `install.sh`, `bin/*.sh`, `bin/pdf2md`,
  `plugin/install-plugin.sh`).
- **python** — `pytest pdf2md/test`: Nahtentdopplung, Randmarken,
  Schleifenerkennung, Wörterbuchabgleich und der Golden-Snapshot aus Issue #8
  — ohne Modell, ohne Vault-Bestand.

Lokal genügt für den Plugin-Teil `npm run lint && npm run check && npm test &&
npm run build`, für die Skripte `shellcheck -x -P bin setup.sh install.sh
bin/*.sh bin/pdf2md plugin/install-plugin.sh` und für Python
`python3 -m pytest pdf2md/test`.

## Stand

Stufe 1 läuft produktiv über ~1.500 Scanseiten. Stufe 2 ist entschieden und
implementiert; die Markdown-Zusammenbau-Schicht ist die jüngste Komponente.

Die Entgleisungen, die zuletzt 15 % der Seiten trafen und die Gesamtzahl auf
93,3 % drückten, sind abgefangen: **98,5 % über alle 40 Benchmarkseiten**, keine
Seite unter dem Stand davor. Was bleibt, ist gewöhnliche OCR-Ungenauigkeit —
und eine mehrspaltige Seite, deren Lesereihenfolge noch nicht sitzt.

Nicht umgesetzt: der Bild-Fallback für Diagrammseiten. Die Erkennung
(`ist_diagramm`) ist auf handgeprüften Seiten kalibriert, und ein Eingriff ohne
eigene Messreihe würde nur gewonnene Diagrammseiten gegen verlorene Textseiten
tauschen. Der Ausweg bleibt `--diagramm-seiten <nr>`.

Offene Fehler, was noch nicht gebaut ist und die Reihenfolge:
[docs/plugin-roadmap.md](docs/plugin-roadmap.md).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
