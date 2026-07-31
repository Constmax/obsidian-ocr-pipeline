---
name: pdf-jura-workflow
description: Verarbeite PDFs und Bilder (Scans, Skripte, Urteile) für den Obsidian-Jura-Vault mit OCR, Kompression und automatischer Gruppierung. Nutze diesen Skill IMMER wenn der Nutzer PDFs/Scans verarbeiten, zusammenfügen, OCR-en, komprimieren oder in seinen Jura-Vault importieren will — auch bei Phrasen wie "neue Scans", "Hemmer-Skript digitalisieren", "raw/assets verarbeiten", "Batch-Ingest", "Klausur einscannen" oder wenn im Vault-Kontext (PW/) unverarbeitete Bilder/PDFs in raw/assets liegen. Der Skill kennt die Vault-Struktur (raw/, wiki/), die PDF-Workflow-Scripts (pdf-auto, pdf-workflow, pdf-combine) und die Integration in den Ingest-Workflow.
---

# PDF Jura Workflow

Dieser Skill verbindet drei Shell-Scripts (`pdf-auto`, `pdf-workflow`, `pdf-combine`) mit dem PW-Vault-Workflow. Er wird bei PDF-Verarbeitungsaufgaben im Jura-Kontext aktiv.

## Kernprinzip

**Scripts einfach ausführen. Keine Preflight-Checks.** Die Scripts prüfen Abhängigkeiten selbst und geben klare Fehler aus. Unnötige `brew list`, `command -v`, `pip list` oder `ls`-Aufrufe verschwenden Turns.

Der Skill besteht aus:
1. **Entscheidungsbaum**: welches Script für welchen Input
2. **Vault-Integration**: wo die Outputs hin müssen (raw/, nicht irgendwo)
3. **Script-Flags-Matrix**: welche Flags für welche Situation

## Workflow-Entscheidung

```
Input-Typ                           → Script          → Output
─────────────────────────────────────────────────────────────────
Bilder (jpg/png/tiff) + evtl. PDFs  → pdf-workflow    → 1 PDF
Nur PDFs, eine Datei draus machen   → pdf-combine     → 1 PDF
Ordner mit vielen PDFs, batch       → pdf-auto        → mehrere PDFs
```

### Keyword → Script

| Nutzer sagt… | Script | Zusatzflags |
|---|---|---|
| "Neue Scans in raw/assets" | `pdf-auto` | — |
| "Hemmer-Skript digitalisieren" | `pdf-workflow` oder `pdf-auto` | `--engine tesseract --split-columns` |
| "Klausur einscannen" | `pdf-workflow` | `--engine tesseract` (bei Zweispalter: `+ --split-columns`) |
| "Urteile zu einer Sammlung" | `pdf-combine` | `--engine apple` (Fließtext) |
| "Batch-Ingest", "ganzes Semester" | `pdf-auto` | `--fast` |
| "Bestehende PDF ist nicht durchsuchbar" | `pdf-combine` | `--force-ocr` |

**`--cleanup` ist kein Default:** Das Flag (Originale → `_archive/`) nur setzen, wenn der User Archivierung explizit bestätigt oder selbst "aufräumen"/"archivieren" sagt — nie ungefragt Originale verschieben.

## Scripts

Alle Scripts liegen in `~/bin/` (Symlinks auf `.claude/skills/pdf-jura-workflow/scripts/`) und sind auf dem System installiert.

### `pdf-auto` — Batch-Verarbeitung

```bash
pdf-auto <ordner> [--output-dir <dir>] [--engine auto|apple|tesseract] \
                  [--dpi N] [--jobs N] [--cleanup] [--fast] \
                  [--split-columns] [--no-quality-gate]
```

- Gruppiert `<Basis> Teil N.pdf` automatisch
- Standalone-PDFs werden einzeln verarbeitet
- Output default: `<ordner>/_processed/`
- `--cleanup`: ⚠️ Verschiebt Originale nach Verarbeitung in `<ordner>/_archive/` (reversibel, aber manuell — nur einsetzen wenn die Originale nicht mehr gebraucht werden)
- `--fast`: Schnellere Defaults (dpi 200, jobs 1) für große Batches
- `--split-columns`: Zweispaltige Seiten automatisch erkennen (pro Seite!), vor OCR trennen und danach wieder zum Originalformat zusammenführen — Pflicht für Hemmer/Kaiser-Zweispalter, auch in gemischten Dokumenten
- `--split-columns-all`: Wie `--split-columns`, aber ohne Erkennung — jede Seite wird getrennt (Fallback, falls die Erkennung danebenliegt)
- `--keep-split`: Unterdrückt das Re-Merge — Output bleibt in (doppelt so vielen) Halbseiten
- **Quality-Gate**: Prüft automatisch Zeichen/Seite + Garbage-Score; retryed bei Fehlschlag automatisch mit anderer Engine. Deaktivierbar via `--no-quality-gate`.

### `pdf-workflow` — Bilder+PDFs → 1 PDF

```bash
pdf-workflow <ordner> <output-name> [--engine ...] [--dpi N] [--jobs N] \
             [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### `pdf-combine` — Mehrere PDFs → 1 PDF

```bash
pdf-combine <ordner> <output-name> [--force-ocr] [--engine ...] [--dpi N] [--jobs N] \
            [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### `reprocess-raw` — bestehende `raw/`-Datei sicher neu verarbeiten

```bash
reprocess-raw <raw-pdf-datei> [pdf-combine-Optionen] [--min-chars N] [--allow-pages LISTE]
```

Für den Fall, dass eine bereits in `raw/` liegende Datei mit der aktuellen Pipeline neu durchlaufen werden soll (z. B. nach einem Bugfix am Skill selbst). Verarbeitet eine Kopie und überschreibt das Original **nur** wenn:
1. die Seitenzahl exakt erhalten bleibt (kein stiller Halbseiten-Bug),
2. jede Seite ≥ `--min-chars` Zeichen hat (Default 50 — Schutz gegen einzelne komplett textlose Seiten, die ein dokumentweiter Zeichen-Durchschnitt verstecken kann).

Bei Fehlschlag bleibt die Quelldatei unverändert; das fehlerhafte Ergebnis landet zur Inspektion daneben (`<name>_FAILED_*.pdf`). `--allow-pages "1,5-7"` nimmt bekannte Deckblatt-/Grafik-Seiten ohne Fließtext von Check 2 aus. Alle unbekannten Flags gehen 1:1 an `pdf-combine` durch.

**Bei jedem Batch-Reprocessing mehrerer bestehender `raw/`-Dateien immer `reprocess-raw` statt eines eigenen Ad-hoc-Kopier-Scripts verwenden** — der ungeprüfte Direkt-Überschreiben-Ansatz hat bereits einmal 14 Dateien mit einem stillen Halbseiten-Bug korrumpiert (siehe `references/BUGREPORT-2026-07-06-split-merge.md`).

## Vault-Integration (PW/)

### VAULT_ROOT — einmalig setzen

Alle Pfadbefehle verwenden `$VAULT_ROOT` statt des manuellen `<vault>`-Platzhalters. Wert deterministisch ermitteln (lokal vor iCloud, kein Raten):

```bash
[ -d ~/JuraExamenVault ] \
  && VAULT_ROOT=~/JuraExamenVault \
  || VAULT_ROOT=~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/JuraExamenVault
```

Bei Pfaden mit Leerzeichen immer in Anführungszeichen: `"$VAULT_ROOT/raw/assets/"`.

### Typische Pfade

```
$VAULT_ROOT/raw/            # finale, durchsuchbare PDFs (dort sollen Outputs landen)
$VAULT_ROOT/raw/assets/     # unverarbeitete Inputs (Bilder, mehrteilige PDFs)
$VAULT_ROOT/wiki/           # Wiki-Seiten (Claude verarbeitet raw/ → wiki/)
```

### Standard-Ablauf für neue Scans

```
1. Nutzer legt Scans in raw/assets/<thema>/
2. pdf-auto "$VAULT_ROOT/raw/assets"              # --cleanup nur nach User-OK
   # Quality-Gate läuft automatisch — kein manueller Check nötig
3. _processed/*.pdf nach raw/ verschieben
4. Ingest-Workflow gemäß CLAUDE.md starten
```

### Toolchain-Übersicht

| Script | Typ | Einsatz |
|---|---|---|
| `pdf-auto` / `pdf-workflow` / `pdf-combine` | **Aktuelle Toolchain** (in `~/bin/`) | Alle neuen Workflows — empfohlen |
| `process_pdfs.sh` (Vault-Root) | Legacy — nutzt `ocrmypdf` direkt | Nur noch als Fallback wenn `pdf-auto` fehlt |

`process_pdfs.sh` ist kompatibel zu den `pdf-*`-Scripts, aber weniger mächtig (kein Batch-Grouping, kein `--fast`). Bei Fehlern in `pdf-auto` kann es als Einzel-PDF-Fallback genutzt werden.

### Wenn der Vault eine CLAUDE.md hat

Lies zuerst `<vault>/CLAUDE.md` — sie definiert Ingest-Workflow, Seitentypen, Namenskonventionen. Dieser Skill kümmert sich **nur** um die PDF-Verarbeitung, der CLAUDE.md-Workflow übernimmt danach.

## OCR-Engine wählen

**Default ist `auto` → Apple Vision falls verfügbar, sonst Tesseract.**

| Situation | Engine | Grund |
|---|---|---|
| Standard-Text, Urteile, Fließtext | `apple` | Schnell, exzellent bei sauberen Scans |
| **Zweispaltiges Layout (Hemmer, Kaiser)** | `tesseract` + `--split-columns` | Zweispalter-Seiten werden erkannt, vor OCR getrennt, danach zurück ins Originalformat gemerged — Vermischung strukturell unmöglich |
| Fotografierte Buchseiten, schief | `apple` | Deskew gut, Neural Engine |
| Handschriftliche Notizen | `apple` | Vision kann Handschrift, Tesseract nicht |
| Tabellen mit Gitternetz | `tesseract` | PSM 1 erkennt Zellstruktur besser |

**`--split-columns` ist der Default-Weg für Zweispalter — inklusive gemischter Dokumente.** Es ersetzt die alte Heuristik „Tesseract PSM 1 und hoffen": Vor dem OCR wird jede Seite per Zeilen-Analyse geprüft (Textzeilen, die sauber auf eine Seitenhälfte beschränkt sind, vs. Zeilen, die über die volle Breite laufen). Nur echte Zweispalter-Seiten werden getrennt-OCR-t; einspaltige Seiten (Deckblätter, Schemata, eingestreute Urteile) laufen unverändert durch. Nach dem OCR wird automatisch wieder zum **Originalformat** zusammengeführt (gleiche Seitenzahl, gleiche Seitengröße wie das Quell-PDF) — kein manueller Nacharbeitsschritt nötig.

Bei einem PDF ganz ohne Textlayer (der Normalfall vor dem ersten OCR) greift automatisch eine Bild-basierte Fallback-Erkennung (rasterisiert die Seite und sucht nach einem durchgehenden hellen Spaltensteg). `--split-columns-all` erzwingt das Splitten aller Seiten, falls die Erkennung bei ungewöhnlichem Layout danebenliegt; `--keep-split` unterdrückt das abschließende Re-Merge (Debugging/Altverhalten).

Wenn Nutzer Qualitätsprobleme meldet → probiere die andere Engine. Das Quality-Gate in `pdf-auto` retryed automatisch (inkl. Spalten-Retry bei Bedarf).

**Textextraktion aus gesplitteten PDFs: `pdftotext -raw` verwenden, nicht den Default-Modus.** Auf gesplitteten+gemergten Seiten ist der Textlayer geometrisch exakt (jedes Wort sitzt visuell korrekt), aber Poppler's Standard-Lesereihenfolgen-Heuristik erkennt die rekonstruierte Zweispalten-Struktur nicht zuverlässig und liefert zeilenweise vermischten Text (linke/rechte Spalte alternierend). `-raw` folgt stattdessen der Content-Stream-Reihenfolge — und die ist bei uns garantiert korrekt, weil `merge_pdf()` (`column_tools.py`) beim Zusammenführen immer erst die linke, dann die rechte Hälfte einbettet. Verifiziert: `-raw` liefert auf Zweispalter-Seiten die komplette linke Spalte gefolgt von der kompletten rechten Spalte, und funktioniert genauso korrekt auf normalen einspaltigen Seiten. Dieser Skill nutzt intern bereits überall `-raw` (Quality-Gate, Split-Verifikation); bei manueller Weiterverarbeitung (Copy-Paste-Vorbereitung, eigene Scripts) selbst daran denken.

## Memory & Performance

Defaults sind auf OCR-Qualität optimiert (300 DPI, Bicubic-Downsampling). **Die Pipeline erkennt die RAM-Größe automatisch** (`detect_safe_jobs`): ≤ 8 GB → 1 Job, ≤ 16 GB → 2 Jobs, > 16 GB → 4 Jobs. Kann mit `--jobs N` überschrieben werden.

Das Bicubic-Resampling erhält Textkanten-Schärfe besser als Ghostscripts Standard `/Subsample` — kritisch für Hemmer-Kleindruck.

**Automatischer MediaBox-Fix**: PDFs mit übergroßen Seiten (Hemmer-Scans: MediaBox = Pixel-Dimensionen @ 72 PPI) werden vor dem Downscaling automatisch auf A4 skaliert. Das reduziert die Raster-Größe von 144 MP auf 8,7 MP pro Seite und verhindert OOM-Kills selbst auf 8-GB-Macs.

**Bei Problemen:**
- `zsh: killed` → `--jobs 1 --dpi 200` (200 statt 150 — nie unter 200 mit Tesseract wegen Qualitätseinbußen)
- Extrem große Batches → `--fast` (dpi 200, jobs 1)
- Hochwertige Originale erhalten → `--dpi 0` (kein Downscaling)

## Pfade mit Leerzeichen

iCloud-Pfade immer in Anführungszeichen. Häufige Pfade:
- `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/<vault>/`
- `~/Library/Mobile Documents/iCloud~com~ilovepdf~www/Documents/`

## Niemals `/tmp/...` an tesseract übergeben (macOS)

Leptonicas `genPathname()` schreibt Pfade, die mit `/tmp` beginnen, auf macOS **absichtlich** auf das User-Tempdir (`$TMPDIR` = `/var/folders/.../T/`) um. `tesseract /tmp/bild.png` sucht die Datei also unter `$TMPDIR/bild.png` und scheitert mit der irreführenden Meldung `image file not found` (Exit-Code trotzdem 0!). Kein Bug, kein Sandbox-Problem — dokumentiertes leptonica-Verhalten.

- OCR-Zwischendateien nie unter `/tmp/` oder `/private/tmp/` per Hand ablegen — `mktemp -d` verwenden (liefert auf macOS `$TMPDIR`-Pfade, die funktionieren) oder ein Arbeitsverzeichnis im Vault.
- Die Skill-Scripts (`pdf-auto`, `pdf-workflow`, …) nutzen bereits `mktemp -d` und sind nicht betroffen.

## Beispiele

### Nutzer: "Ich habe neue Hemmer-Skripte in raw/assets gedumpt, bitte verarbeiten"
```bash
pdf-auto "$VAULT_ROOT/raw/assets" --engine tesseract --split-columns
# --split-columns: erkennt Zweispalter-Seiten automatisch, trennt + merged
# zurück ins Originalformat (Mischdokumente mit Einzelspalten-Seiten sind ok)
# Quality-Gate + Auto-Retry läuft automatisch
# → Output in raw/assets/_processed/
# Danach: mv "$VAULT_ROOT/raw/assets/_processed/"*.pdf "$VAULT_ROOT/raw/"
```

### Nutzer: "Ich habe eine Klausur als Foto-Serie"
```bash
pdf-workflow "$VAULT_ROOT/raw/assets/klausur-xyz" klausur-xyz --engine tesseract
mv "$VAULT_ROOT/raw/assets/klausur-xyz/klausur-xyz.pdf" "$VAULT_ROOT/raw/"
# Dann: Klausur-Ingest-Workflow gemäß CLAUDE.md
```

### Nutzer: "Hier sind 8 BGH-Urteile, mach eine Sammlung"
```bash
pdf-combine ~/Downloads/Urteile bgh_urteile_sammlung
mv ~/Downloads/Urteile/bgh_urteile_sammlung.pdf "$VAULT_ROOT/raw/"
```

### Nutzer: "Das komplette Semester-Skript-Paket importieren, Originale kannst du archivieren"
```bash
pdf-auto "$VAULT_ROOT/raw/assets" --cleanup --fast --engine tesseract --split-columns
# --fast: dpi 200, jobs 1
# --split-columns: erkennt + trennt Zweispalter-Seiten, merged zurück (Original-Seitenzahl)
# --cleanup nur, weil der User die Archivierung explizit freigegeben hat
# Danach gemäß Batch-Ingest-Workflow in CLAUDE.md
```

## Fehlerbehandlung

| Fehler | Antwort |
|---|---|
| `❌ Ordner nicht gefunden` | Pfad mit Leerzeichen → Anführungszeichen |
| `zsh: killed` | `--jobs 1 --dpi 200` nachreichen (detect_safe_jobs sollte das verhindern) |
| Spalten werden vermischt | `--engine tesseract --split-columns` nachreichen (Erkennung + strukturelle Trennung, Original-Seitenzahl bleibt erhalten) |
| Erkennung trifft bei ungewöhnlichem Layout nicht zu | `--split-columns-all` erzwingt Split auf allen Seiten |
| `pdftotext` liefert vermischte Zeilen trotz korrekt aussehender PDF | `pdftotext -raw` statt Default-Modus (Poppler-Lesereihenfolge erkennt rekonstruierte Zweispalten nicht zuverlässig, siehe oben) |
| OCR-Qualität schlecht (Garbage-Score > 0.40) | Quality-Gate retryed automatisch; manuell: Engine wechseln + `--dpi 0` |
| OCR-Qualität akzeptabel, aber Gate schlägt an | Schwellwert ist 0,40 — bewusst toleranter für alte Hemmer-Scans |
| `PriorOcrFoundError` | `--force-ocr` nachreichen |
| Umlaute falsch | `--engine apple` (besser bei deutschen Umlauten) |
| `No module named expat` | Python 3.14 Bug — siehe `references/installation.md` |
| Quality-Gate schlägt trotz gutem OCR fehl | `--no-quality-gate` setzen (false positive bei kurzen/grafischen Dokumenten) |
| `DecompressionBombWarning` | Tritt bei oversized MediaBox auf — `fix_mediabox()` verhindert das automatisch |

## Weiterführende Referenzen

- `references/scripts-detail.md` — komplette Flag-Referenz aller Scripts
- `references/installation.md` — Setup und Troubleshooting
- `references/vault-integration.md` — Wie der Skill mit CLAUDE.md-Workflows zusammenarbeitet

## TL;DR

1. Keywords lesen → Script wählen
2. Pfade quoten
3. Engine wählen: Zweispalter → `--engine tesseract --split-columns`, sonst `auto`
4. Ausführen (Quality-Gate läuft automatisch)
5. Output nach `raw/` verschieben (wenn im Vault-Kontext)
6. Ingest-Workflow gemäß vault/CLAUDE.md weitermachen
