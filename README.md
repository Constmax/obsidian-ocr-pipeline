# obsidian-ocr-pipeline

![CI](https://github.com/Constmax/obsidian-ocr-pipeline/actions/workflows/ci.yml/badge.svg)

OCR-Pipeline für gescannte juristische Skripte, Fälle und Klausuren — von der
Ordnerfotografie bis zur durchsuchbaren Markdown-Seite im Obsidian-Vault.

Entstanden als Werkzeugkasten innerhalb eines Jura-Vaults, hier herausgelöst,
weil es Code ist und in ein Notizen-Repo nicht gehört. **Fernziel: ein
Obsidian-Plugin** — siehe [docs/plugin-roadmap.md](docs/plugin-roadmap.md).

## Three Stages

| | Stage 1 — `bin/` | Stage 2 — `pdf2md/` | Stage 3 — `plugin/` |
|---|---|---|---|
| Output | searchable PDF (text layer) | Markdown | review inside the vault |
| Engine | Tesseract / Apple Vision (via ocrmypdf) | PaddleOCR-VL 1.5 4bit via MLX | calls Stage 1 and 2 |
| State | **stable, in daily use** | works, assembly layer is young | usable, in development |
| Runtime | seconds to minutes per file | 15–60 s/page on M1 | — |
| Platform | macOS + Linux (Apple engine macOS only) | Apple Silicon (MLX) | Obsidian desktop |

Stages 1 and 2 are independent. Stage 1 makes scans searchable and
archivable, Stage 2 makes them **readable in Obsidian**. Stage 3 is a thin
client: the plugin starts the installed CLIs and reads their output according
to the contract in [docs/cli-contract.md](docs/cli-contract.md).

## Stufe 1 — PDF → durchsuchbares PDF

Vier CLIs über einer gemeinsamen Bibliothek (`bin/pdf-lib.sh`):

```
Input                                 → Script          → Output
─────────────────────────────────────────────────────────────────
Bilder (jpg/png/tiff) + evtl. PDFs    → pdf-workflow    → 1 PDF
Mehrere PDFs zu einem zusammenfassen  → pdf-combine     → 1 PDF
Ordner voller PDFs, Batch             → pdf-auto        → n PDFs
Bestehende Datei neu verarbeiten      → reprocess-raw   → dieselbe Datei
                                        (--output X)    → neue Datei X
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
  Mit `--output X` bleibt das Original unangetastet: `X` erscheint erst nach
  bestandenem Gate, wird nie überschrieben und fehlt bei Fehler oder Abbruch
  ganz.

Warum das B5-Gate existiert: [docs/BUGREPORT-2026-07-06-split-merge.md](docs/BUGREPORT-2026-07-06-split-merge.md).

## Stufe 2 — PDF → Markdown

`pdf2md/pdf2md.py` rendert jede Seite, kachelt bei hoher Textdichte, schickt sie
durch PaddleOCR-VL und baut die Zeilen anhand ihrer `<|LOC|>`-Bounding-Boxes zu
Markdown zusammen. Seiten mit brauchbarem Textlayer werden verlustfrei
übernommen statt neu gelesen; Diagrammseiten kommen als Bild plus Text in einem
eingeklappten Callout.

Eingabe ist ein PDF oder ein einzelnes Seitenbild (`.png`, `.jpg`, `.jpeg`,
`.tif`, `.tiff`, `.bmp`): Bilder werden an der Eingabegrenze zu einer
einseitigen PDF normalisiert, in voller Auflösung, sodass dahinter alles
PDF-only bleibt. Ein Bild ist genau eine Seite — ein mehrseitiges TIFF wird
abgelehnt statt halb verarbeitet. Details in `docs/scripts-detail.md`.

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
derselben Seiten (`bench/bench_ocr.py`, gemessen gegen Commit `ddf69e9`):

| | Wortgenauigkeit | Zitattreue | Reihenfolge |
|---|---|---|---|
| alle 40 Seiten (`ddf69e9`) | **98,2 %** | **92,0 %** | **98,1 %** |

Vollständig mit Fehlerklassen, Vorher-Zahlen und Historie:
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
[docs/review-view.md](docs/review-view.md).

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
VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh --enable   # Plugin (Stufe 3)
```

- `install.sh` verlinkt `pdf-auto`, `pdf-combine`, `pdf-workflow`,
  `reprocess-raw` nach `~/bin` und prüft die Abhängigkeiten.
- `plugin/install-plugin.sh` kopiert `main.js`, `manifest.json` und
  `styles.css` nach `$VAULT_ROOT/.obsidian/plugins/<id>/` — die ID steht nur in
  `plugin/manifest.json` (ohne Build, kein Node nötig; `--build` baut aus `src/`
  auf der Dev-Maschine). `--enable` trägt das Plugin in
  `community-plugins.json` ein und schaltet die alte ID `ocr-vorschau` ab.
  Kopie ist Default (Symlinks verlieren in iCloud Dateien), `--symlink` bleibt
  als Dev-Opt-in.

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
pdf2md "raw/ZR/skript.pdf" --out _ocr-preview

# PDF → Markdown, nur bestimmte Seiten
pdf2md "raw/ZR/skript.pdf" --seiten "1,3-5,8" --out _ocr-preview

# Passende Seitenergebnisse werden automatisch wiederverwendet; Seite 12 neu rechnen
pdf2md "raw/ZR/skript.pdf" --out _ocr-preview --neu 12
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
bin/             Stufe 1 — pdf-lib.sh + 4 CLIs + column_tools.py
ocrmypdf_paddle/ Stufe 1 — OCRmyPDF-Engine-Plugin mit PaddleOCR (RapidOCR),
                 noch nicht von setup.sh installiert (docs/paddle-textlayer.md)
pdf2md/          Stufe 2 — pdf2md.py (CLI) + conversion.py (Runner) + layout.py
                 + ocr.py + assembly.py + dictionary.py + page_cache.py,
                 Testsuite in pdf2md/test/
plugin/          Stufe 3 — Abgleich-Ansicht (Obsidian-Plugin, TypeScript)
contracts/       CLI-Vertrag zwischen den Stufen und dem Plugin (docs/cli-contract.md)
bench/           Benchmark-Harness und Messergebnisse; alte Experimente in bench/archive/
docs/            Installation, Flag-Referenz, Formate, Vault-Integration
skill/           Claude-Code-Skill (SKILL.md) zum Einbinden in einen Vault
setup.sh         Einmal-Setup (Einstiegstür): Brewfile + venvs + Links + Plugin
Brewfile         Systempakete für das Setup (brew bundle)
Makefile         make check / make test-fast — dieselben Schritte wie die CI
```

Die Benchmark-**Seitenbilder** liegen bewusst nicht im Repo: sie sind Scans aus
urheberrechtlich geschütztem Kursmaterial und mit `bench/build_bench.py` aus dem
eigenen Bestand reproduzierbar. Die unterstützten Befehle stehen in
[bench/README.md](bench/README.md); die Seitenauswahl steht in
[bench/BENCHMARK-SET.md](bench/BENCHMARK-SET.md).

## CI

One command checks everything CI checks:

```bash
make check      # plugin (tsc, eslint, tests, build, main.js), shellcheck, pytest, OCRmyPDF tests
make test-fast  # fast tests only (pytest -m "not slow" + plugin tests), a few seconds
```

Every pull request and every push to `main` runs four jobs in
`.github/workflows/ci.yml`; each calls one `make` target, and CI only installs
the tools. Feature branches run through their PR — an unrestricted `push`
would start every job twice.

- **plugin** (`make plugin`) — `npm ci`, tsc, eslint, tests, build and the
  core check: `main.js` must be versioned *and* match the build from `src/`.
  A PR that changes `src/` without rebuilding turns red, as does one that
  removes `main.js` from version control.
- **shell** (`make shellcheck`) — shellcheck (pinned version) over all
  tracked shell scripts (`setup.sh`, `install.sh`, `bin/*.sh`, `bin/pdf2md`,
  `plugin/install-plugin.sh`).
- **python** (`make test-py`) — `pytest pdf2md/test bin/test`: Stage 2
  without a model or vault material (seam deduplication, margin marks, loops,
  dictionary, golden snapshot, CLI contract) and Stage 1 with stubbed tools;
  plus the import smoke test of the benchmark entry points.
- **ocrmypdf** (`make test-ocrmypdf`) — with the pinned ocrmypdf 17.8.0: the
  hOCR text-layer order and the PaddleOCR engine plugin
  (`ocrmypdf_paddle/test`, without RapidOCR or models). Locally `make` uses
  `~/.venvs/ocrmypdf` once pytest is installed there; otherwise these tests
  are skipped.

## Stand

Stufe 1 läuft produktiv über ~1.500 Scanseiten. Stufe 2 ist entschieden und
implementiert; die Markdown-Zusammenbau-Schicht ist die jüngste Komponente.

Die Entgleisungen, die zuletzt 15 % der Seiten trafen und die Gesamtzahl auf
93,3 % drückten, sind abgefangen: **98,2 % über alle 40 Benchmarkseiten** (gemessen gegen Commit `ddf69e9`), keine
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
