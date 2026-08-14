# Scripts — komplette Flag-Referenz

## Gemeinsame Flags

Alle drei Scripts teilen diese Flags:

| Flag | Default | Bedeutung |
|---|---|---|
| `--engine auto\|apple\|tesseract` | `auto` | OCR-Engine |
| `--dpi N` | `300` | Pre-OCR Downscaling (0 = aus) |
| `--jobs N` | `2` | Parallele OCR-Worker |
| `--split-columns` | aus | Zweispaltige Seiten automatisch erkennen, trennen, danach zurück ins Originalformat mergen |
| `--split-columns-all` | aus | Wie `--split-columns`, aber ohne Erkennung — jede Seite wird getrennt |
| `--keep-split` | aus | Re-Merge unterdrücken (Output bleibt in Halbseiten) |
| `--no-quality-gate` | aus | Qualitäts-Check + Auto-Retry deaktivieren |

### Engine-Auswahl

- `auto`: Apple Vision wenn `ocrmypdf-appleocr` installiert, sonst Tesseract
- `apple`: Erzwingt Apple Vision (fehlt das Plugin → Fehler)
- `tesseract`: Erzwingt Tesseract (nutzt automatisch `--tesseract-pagesegmode 1` für Spaltenerkennung und `--clean` wenn `unpaper` installiert)

### DPI-Tuning

- `300`: Sweet Spot für OCR — Tesseracts optimale Auflösung, nötig für Hemmer-Kleindruck (Default)
- `200`: RAM-sparsamer, leichte Qualitätseinbußen bei Kleindruck (--fast Default)
- `150`: Absolutes Minimum — nur bei OOM-Killings mit `--jobs 1`
- `0`: Downscaling komplett aus

Das Downscaling nutzt standardmäßig **Bicubic**-Resampling (`/Bicubic`) statt Ghostscripts Default `/Subsample`, um Textkanten-Schärfe zu erhalten.

### Jobs-Tuning

- `auto`: Automatisch erkannt via `detect_safe_jobs()`: ≤ 8 GB RAM → 1 Job, ≤ 16 GB → 2, > 16 GB → 4. Überschreibbar mit `--jobs N`.
- `2`: Default, sicher auf Apple Silicon mit ≥ 16 GB
- `1`: Single-Threaded, erzwungen auf 8-GB-Macs (M1 Air etc.)
- `4-8`: Nur bei großen Maschinen / viel RAM

## pdf-auto

```bash
pdf-auto <ordner> [--output-dir <dir>] [--engine ...] [--dpi N] [--jobs N] \
                  [--cleanup] [--fast] \
                  [--split-columns] [--split-columns-all] [--keep-split] \
                  [--no-quality-gate]
```

### Spezifische Flags

- `--output-dir <dir>`: Custom Output-Pfad (Default: `<ordner>/_processed/`)
- `--cleanup`: Originale nach Erfolg in `<ordner>/_archive/` verschieben (Input-Ordner wird leer, was die Wiederholbarkeit erleichtert)
- `--fast`: Presets für große Batches:
  - `--dpi 200` (statt 300)
  - `--jobs 1` (stabiler)
- `--split-columns`: Zweispaltige Seiten pro Seite erkennen, trennen, nach OCR zurück ins Originalformat mergen — strukturelle Lösung gegen Spaltenvermischung bei Hemmer/Kaiser, auch in gemischten Dokumenten (siehe "Column-Splitting" unten)
- `--split-columns-all`: erzwingt den Split auf jeder Seite (kein Auto-Detect) — Fallback falls die Erkennung bei ungewöhnlichem Layout danebenliegt
- `--keep-split`: unterdrückt das Re-Merge, Output bleibt in (doppelt so vielen) Halbseiten
- `--no-quality-gate`: Überspringt den automatischen Qualitäts-Check und Engine-Retry nach OCR

### Qualitäts-Gate

Nach jedem OCR-Durchlauf prüft die Pipeline automatisch:
1. **Zeichen/Seite** ≥ 200 (fängt Totalausfälle)
2. **Garbage-Score** < 0.40 (fängt Spaltenvermischung, §→88-Korruption, Binnengroßbuchstaben)
3. **iso-Ratio** < 0.40 (Sonder-Check: >40 % 1-2-Zeichen-Wörter = garantierte Spaltenvermischung)

Schwellwert 0.40 statt 0.30: Toleriert die unvermeidbaren OCR-Fehler bei älteren Hemmer-Scans (z. B. „eaglen" für „hemmer") und greift nur bei echten Strukturproblemen.

Bei Fehlschlag: Auto-Retry mit Spalten-Split (bei Tesseract, inkl. Re-Merge zurück ins Originalformat), dann Retry mit alternativer Engine (apple ↔ tesseract).

### Teil-Detection

Regex: `^(.+)[[:space:]]+[Tt]eil[[:space:]]+([0-9]+)\.[Pp][Dd][Ff]$`

- ✅ `Verwaltungsrecht AT Skript Teil 1.pdf`
- ✅ `Strafrecht BT TEIL 12.pdf`
- ❌ `Verwaltungsrecht-Teil-1.pdf` (keine Leerzeichen)
- ❌ `Part 1 ....pdf` (Englisch)

Teile werden alphabetisch + numerisch sortiert gemerged.

### Output-Namen

- `Foo Teil 1.pdf` + `Foo Teil 2.pdf` → `Foo.pdf` (Teil-Suffix entfernt)
- `Urteil BGH 2024.pdf` (kein Teil-Muster) → `Urteil BGH 2024.pdf`

## pdf-workflow

```bash
pdf-workflow <ordner> <output-name> [--engine ...] [--dpi N] [--jobs N] \
             [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### Akzeptierte Inputs

- Bilder: `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif` (case-insensitive)
- PDFs: vorhandene PDFs werden angehängt

### Sortierung

Natural Sort (`sort -V`):
- `(1).jpeg`, `(2).jpeg`, ..., `(10).jpeg` → korrekt sortiert
- `seite_01.jpg`, `seite_02.jpg` → auch korrekt
- `img1.jpg`, `img10.jpg`, `img2.jpg` → dank `-V` als 1,2,10

### Output-Name-Handling

- `.pdf`-Endung wird automatisch gestripped
- Leerer Name → Fehler
- Kollision mit Input: Output wird aus PDF-Liste ausgeschlossen (verhindert Infinite Loop bei Re-Runs)

## pdf-combine

```bash
pdf-combine <ordner> <output-name> [--force-ocr] [--engine ...] [--dpi N] [--jobs N] \
            [--split-columns] [--split-columns-all] [--keep-split] [--no-quality-gate]
```

### Spezifische Flags

- `--force-ocr`: OCR auch auf Seiten mit existierender Textebene (Default: `--skip-text`)
  - Nutzen bei schlechter bestehender OCR
  - Verwirft alten Text, macht neuen

### Sortierung

Alphabetisch mit Natural Sort. Für gewünschte Reihenfolge Präfixe nutzen: `01_`, `02_`, ...

## reprocess-raw

```bash
reprocess-raw <raw-pdf-datei> [pdf-combine-Optionen] [--min-chars N] [--allow-pages LISTE]
```

Wrapper um `pdf-combine` für den Spezialfall „bestehende `raw/`-Datei mit der aktuellen Pipeline neu verarbeiten" (typisch: nach einem Bugfix am Skill selbst). Ablauf:

1. Kopiert die Quelldatei in ein Temp-Verzeichnis, ruft dort `pdf-combine` mit den durchgereichten Optionen auf.
2. **Check 1 — Seitenzahl:** Output muss exakt so viele Seiten haben wie das Original. Abweichung → Original bleibt unverändert, Ergebnis wird als `<name>_FAILED_pagecount.pdf` neben die Quelle gelegt.
3. **Check 2 — B5-Gate (`column_tools.py verify-pages`):** Jede Seite muss ≥ `--min-chars` Zeichen haben (Default 50, via `pdftotext -raw`). Ein dokumentweiter Zeichen-Durchschnitt (wie ihn das normale Quality-Gate prüft) kann eine einzelne komplett textlose Seite in einem sonst großen, guten Dokument verstecken — genau das ist der Fehler, der am 2026-07-06 vierzehn `raw/`-Dateien korrumpiert hat (siehe `BUGREPORT-2026-07-06-split-merge.md` in diesem Verzeichnis). Abweichung → Original bleibt unverändert, Ergebnis wird als `<name>_FAILED_pages.pdf` abgelegt, betroffene Seiten werden einzeln gemeldet.
4. Nur wenn beide Checks bestehen: Original wird überschrieben.

`--allow-pages "1,5-7"` nimmt bekannte Deckblatt-/Grafik-Seiten ohne nennenswerten Fließtext von Check 2 aus (z. B. reine Diagramm- oder Kursplan-Seiten). Ohne verfügbares `pikepdf` (Python-Dependency von `column_tools.py`) bricht das Script sicherheitshalber ab, statt das B5-Gate stillschweigend zu überspringen.

### `column_tools.py verify-pages`

```bash
column_tools.py verify-pages <pdf> [--min-chars N] [--allow-pages LISTE]
```

Die zugrunde liegende Prüfung, auch einzeln nutzbar: extrahiert jede Seite via `pdftotext -raw`, meldet alle Seiten unter der Zeichen-Schwelle (außer den in `--allow-pages` genannten) auf stderr und liefert Exit-Code 1 bei mindestens einem Verstoß.

## Pre-OCR Pipeline (Automatisch)

Vor dem OCR durchläuft jedes PDF drei Stufen — automatisch, keine Flags nötig:

```
Stage 1: MediaBox-Fix       Stage 2: Downscale         Stage 3: Column-Split
┌──────────────────┐       ┌─────────────────┐        ┌──────────────────┐
│ Seite > 650×900   │  →    │ 300 DPI          │   →    │ (wenn --split-   │
│ pts?              │       │ Bicubic          │        │  columns aktiv)  │
│ → skaliere auf A4  │       │                  │        │ linke + rechte   │
│   (595×842 pts)   │       │                  │        │ Halbseite        │
└──────────────────┘       └─────────────────┘        └──────────────────┘
```

### Stage 1: MediaBox-Fix

**Problem**: Manche PDFs (typisch: Hemmer-Scans) haben ihre MediaBox auf die Pixel-Dimensionen des Bildes gesetzt (z. B. 2439×3413 pts @ 72 PPI). Die logische Seitengröße beträgt damit 33,9 × 47,4 Zoll. Bei 300 DPI OCR-Rasterung entstehen 144 Megapixel pro Seite — das sprengt jeden RAM (auch 16 GB).

**Lösung**: `fix_mediabox()` erkennt Seiten > 650×900 pts und skaliert sie via Ghostscript `-dPDFFitPage` auf A4 (595×842 pts). Danach sind es bei 300 DPI nur noch 8,7 Megapixel/Seite — problemlos für 8 GB RAM.

Kein Flag nötig — läuft automatisch vor dem Downscaling.

### Stage 2: Pre-OCR Downscaling

Implementiert via Ghostscript-Zwischenschritt zwischen MediaBox-Fix und OCRmyPDF:

```bash
gs -sDEVICE=pdfwrite \
   -dDownsampleColorImages=true -dColorImageResolution=300 \
   -dColorImageDownsampleType=/Bicubic \
   -dDownsampleGrayImages=true  -dGrayImageResolution=300 \
   -dGrayImageDownsampleType=/Bicubic \
   -dDownsampleMonoImages=true  -dMonoImageResolution=300 \
   -dMonoImageDownsampleType=/Bicubic \
   -sOutputFile=downscaled.pdf input.pdf
```

**Warum**: iLovePDF & Handy-Scans produzieren oft 400-600 DPI → 300+ Megapixel/Seite → PIL-Speichergrenzen sprengt → OOM-Kill. 300 DPI ist Tesseracts optimale Arbeitsauflösung; Bicubic-Resampling erhält Textkanten besser als Ghostscripts Standard `/Subsample`.

**Achtung**: Ghostscript kann die Dateigröße vorübergehend **erhöhen** wenn Input bereits JPEG-komprimiert war. Die finale Größe nach OCR+Optimize ist trotzdem kleiner.

## Column-Splitting (`--split-columns`)

Aktiviert via `--split-columns`-Flag in allen drei Scripts. Für zweispaltige Layouts (Hemmer-Skripte, Kaiser-Klausuren, Fachzeitschriften) wird jede **als zweispaltig erkannte** Seite vor dem OCR vertikal getrennt, separat OCR-t und danach wieder zu einer Seite in Originalgröße zusammengesetzt:

```
Original (N Seiten, gemischt)         Pro Seite: Erkennung → ggf. Split → OCR → Merge
┌──────────┬──────────┐    zwei-      ┌──────────┐  ┌──────────┐    ┌──────────┬──────────┐
│  Linke   │  Rechte  │  spaltig →    │  Linke   │  │  Rechte  │ →  │  Linke   │  Rechte  │
│  Spalte  │  Spalte  │   erkannt     │  Spalte  │  │  Spalte  │    │  Spalte  │  Spalte  │
└──────────┴──────────┘               └──────────┘  └──────────┘    └──────────┴──────────┘

┌────────────────────┐    einspaltig  ┌────────────────────┐
│   Fließtext-Seite   │  → erkannt →  │   Fließtext-Seite   │  (unverändert durchgereicht)
└────────────────────┘                └────────────────────┘

Output: N Seiten, Originalgröße — pro Seite garantiert korrekte Lesereihenfolge
```

Die Spaltenvermischung (Sätze der linken und rechten Spalte im Wechsel) wird für erkannte Zweispalter-Seiten strukturell unmöglich; einspaltige Seiten (Deckblätter, Schemata, eingestreute Urteile) laufen unangetastet durch OCR. Das Output-PDF hat exakt so viele Seiten wie das Original.

### Erkennung

Läuft zeilenbasiert auf `pdftotext -bbox` (falls schon ein Textlayer existiert, z. B. beim internen Auto-Retry nach fehlgeschlagenem Quality-Gate): Wortpositionen werden zu visuellen Zeilen geclustert; jede Zeile gilt als „links", „rechts" oder „voll" (über die Seitenmitte hinausgehend, z. B. Kopfzeilen).

Die eigentliche Zweispalter-Entscheidung matcht danach jede linke Zeile mit ihrer **nächstgelegenen rechten Zeile gleicher Zeilenhöhe** (nicht: globale Min/Max-Kanten über die ganze Seite) und prüft den Gutter **pro Zeilenpaar** gegen ein plausibles Fenster (3–15 % der Seitenbreite). Eine Seite gilt als zweispaltig, wenn ein Mindestanteil dieser Paare (≥ 30 %) einen plausiblen Gutter zeigt. Dieser paarweise Ansatz ist notwendig, weil eine globale Kante bei schräg fotografierten Ordnerseiten kollabiert (der Spaltensteg verläuft dann diagonal über die Seite und eine globale Min/Max-Berechnung liefert einen negativen „Gutter", obwohl die Seite echt zweispaltig ist) — verifiziert an echtem Hemmer-Material. Derselbe paarweise Test verwirft nebenbei auch Sliver-Fehlerkennungen (z. B. wenn ein Ordnerfoto den Rand der nächsten Blattseite mit einfängt): Ein Bild-Artefakt ohne echte zweite Spalte erzeugt keine konsistenten, plausiblen Zeilenpaare.

**Vor dem ersten OCR gibt es noch keinen Textlayer** — das ist der Normalfall beim Haupt-Pipeline-Durchlauf. Dann rastert die Erkennung die Seite stattdessen als Bild (`pdftoppm`) und sucht nach einem durchgehenden hellen Spaltensteg.

`--split-columns-all` überspringt die Erkennung und splittet jede Seite (Fallback für Layouts, bei denen die Heuristik danebenliegt).

Implementiert via Ghostscript-CropBox (Split) und pikepdf `add_overlay` (Merge); keine zusätzlichen Dependencies über das ocrmypdf-venv hinaus (`pikepdf`, `PIL`).

### Lesereihenfolge nach dem Merge (`pdftotext -raw`)

Der Textlayer nach Split+Merge ist geometrisch exakt (jedes Wort sitzt visuell an der richtigen Stelle), aber Poppler's Standard-Lesereihenfolgen-Heuristik (verwendet von `pdftotext` ohne Flags) erkennt die rekonstruierte Zweispalten-Struktur nicht zuverlässig und liefert zeilenweise vermischten Text — trotz korrekter Geometrie. Ursache: `merge_pdf()` bettet linke und rechte Hälfte über zwei unabhängige `add_overlay`-Aufrufe ein; Poppler wirft die daraus resultierende Content-Stream-Reihenfolge im Standard-Modus zugunsten einer eigenen geometrischen Blockerkennung weg, die hier fehlschlägt.

**Fix: `pdftotext -raw` statt Default-Modus.** `-raw` folgt der Content-Stream-Emissionsreihenfolge statt Poppler's rekonstruierter Lesereihenfolge — und die ist bei uns garantiert korrekt, weil `merge_pdf()` immer erst die linke, dann die rechte Hälfte schreibt. Verifiziert: komplette linke Spalte gefolgt von kompletter rechter Spalte, sowohl auf Zweispalter- als auch auf normalen einspaltigen Seiten. Dieser Skill nutzt `-raw` bereits intern überall (`quality_check` in `pdf-lib.sh`, `verify_ocr_split` in `column_tools.py`); bei jeder externen Weiterverarbeitung dieser PDFs (Copy-Paste-Vorbereitung, eigene Scripts, Ingest-Workflows anderer Skills) selbst daran denken.

## Memory-Profil

Mit MediaBox-Fix (automatisch) sind selbst große Scans RAM-sicher:

| Szenario | Pixel/Seite | RAM-Bedarf |
|---|---|---|
| A4-Seite @ 300 DPI (nach MediaBox-Fix) | 8,7 MP | ~150 MB/Seite |
| Hemmer-Zweispalter, Halbseite @ 300 DPI | 4,3 MP | ~80 MB/Hälfte |
| Original (72 PPI MediaBox) @ 300 DPI | 144 MP | **OOM** (>8 GB) |
| 50 Seiten A4, jobs 1 (8 GB Mac) | je 8,7 MP | ~500 MB peak |

**`detect_safe_jobs()`** erkennt die RAM-Größe automatisch und setzt `--jobs` auf 1 für 8-GB-Macs. Kann mit `--jobs N` überschrieben werden.

`ocrmypdf --max-image-mpixels` wird in `build_ocr_args` auf 400 MP gesetzt (genug für Edge-Cases ohne MediaBox-Fix) — als Aufruf-Flag, nicht als Umgebungsvariable, da ocrmypdf `PILLOW_MAX_IMAGE_PIXELS` nicht auswertet.

## Stufe 2: Modulaufteilung (`pdf2md/`, Issue #8)

`pdf2md.py` ist seit dem Split nur noch CLI und Seitenlauf; die übrigen
Schichten liegen in drei Modulen, die Importrichtung läuft strikt einseitig:

```
pdf2md.py (CLI/Orchestrierung)
   ├── layout.py       Geometrie: Spalten, Kästen, Tabellen, Diagramme
   ├── ocr.py          Kachelung, Modellaufruf, Entgleisung/Reparatur
   ├── zusammenbau.py  Markdown-Zusammenbau (reine Funktionen) — testbar
   └── woerterbuch.py  Wörterbuchabgleich nach dem Zusammenbau — testbar
```

Der Zusammenbau ist die testbare Schicht: `python3 -m pytest pdf2md/test -q`
läuft ohne MLX, ohne fitz und ohne Vault-Bestand (Golden-Snapshot in
`pdf2md/test/daten/snapshot.json`; `pytest` steht in
`pdf2md/requirements.txt`). Die schweren Importe (fitz, numpy, PIL, mlx_vlm)
liegen in allen Modulen funktionslokal — nur so bleibt der Modulimport
abhängigkeitsfrei.

**Vault-Kopie**: `.ocr-bench/` im Vault ist flach (siehe `bench/pfade.py`,
Zwei-Orte-Konvention) und braucht **fünf** Dateien: `pdf2md.py`, `layout.py`,
`ocr.py`, `zusammenbau.py`, `woerterbuch.py`. Fehlt eine, schlägt der nächste
Lauf mit `ModuleNotFoundError` fehl. Aus demselben Grund steht die
juristische Begriffsliste im Modul und nicht in einer Datei daneben — ein
`daten/`-Ordner ginge beim flachen Kopieren stillschweigend verloren.

## Stufe 2: Wörterbuchabgleich (`woerterbuch.py`)

Läuft nach dem Zusammenbau über **jede OCR-Seite** — nicht über Textlayer-
Seiten, deren Text exakt ist und dort nur Fehlalarme erzeugen würde. Was im
Wörterbuch fehlt, kommt als `⌕`-Zeile ins Protokoll und als
`woerter-verdaechtig` ins Frontmatter.

| Flag | Wirkung |
|---|---|
| *(Voreinstellung)* | nur melden, Text bleibt unangetastet |
| `--woerterbuch-korrigieren` | eindeutige Fälle ersetzen (siehe unten) |
| `--woerterbuch <datei>` | zusätzliche Wortliste oder `.dic`, mehrfach möglich |
| `--woerterbuch-bericht <datei>` | alle Befunde mit Seitenzahl als JSON |
| `--kein-woerterbuch` | Abgleich ganz abschalten |

**Eindeutig** heißt: das Wort steht nicht im Wörterbuch, und genau *eine*
Variante aus der Verwechslungstabelle (`m`/`rn`, `ff`/`i`, `l`/`1`, `u`/`ü`, …)
steht darin. Gibt es zwei (`Hans`/`Haus`), bleibt das Wort stehen und wird nur
gemeldet. Zitate, Zahlen, Abkürzungen, Tabellen, Wikilinks und Fußnotenmarken
werden gar nicht erst geprüft — die Restfehlerklasse „römisch I als 1/l/|" ist
ausdrücklich **nicht** Gegenstand dieses Moduls.

**Wörterbuchquellen**, in dieser Reihenfolge: `--woerterbuch`, dann
`$PDF2MD_WOERTERBUCH` (mit `:` getrennt), dann das erste gefundene
Systemwörterbuch (`/opt/homebrew/share/hunspell`, `/usr/share/hunspell`,
`~/Library/Spelling`, LibreOffice-Bundle). Steht `hunspell` mit deutschem
Wörterbuch bereit, entscheidet es zuerst — es wertet die Affixregeln aus und
ist damit genauer als die Ersatzregeln des Moduls. Findet sich gar nichts,
sagt der Lauf das und überspringt den Abgleich.

Ohne installiertes Wörterbuch genügt eine Datei:

```bash
curl -o ~/.local/share/de_DE.dic \
  https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.dic
curl -o ~/.local/share/de_DE.aff \
  https://raw.githubusercontent.com/LibreOffice/dictionaries/master/de/de_DE_frami.aff
export PDF2MD_WOERTERBUCH=~/.local/share/de_DE.dic
```

Die `.aff` daneben ist kein Beiwerk: aus ihrer `SET`-Zeile kommt der
Zeichensatz, und `de_DE_frami.dic` ist ISO-8859-1. Fehlt sie, wird die Datei
als UTF-8 gelesen und jedes Wort mit Umlaut fällt aus dem Wörterbuch.

**Gemessen** an 202 Wörtern echter Gutachtenprosa gegen `de_DE_frami`:
0 Fehlalarme, 6 von 7 eingestreuten Lesefehlern gefunden. Der siebte
(`Verhaltungsakte`) ist die dokumentierte Grenze — ein morphologisch
wohlgeformtes Scheinwort zerlegt die Kompositumsregel in `verhalten` + `Akte`.
Enger gestellt wäre jedes zweite `-ung`-Substantiv ein Fehlalarm, weil die
`.dic`-Dateien diese Ableitung ihren Affixregeln überlassen.

## --fortschritt (Stufe 2)

Maschinenlesbarer Fortschritt als JSON-Zeilen auf stderr. Standardmäßig bleibt
die Ausgabe auf die Konsole ausgerichtet (deutsche Sätze, Emoji, Pfeile). Mit
`--fortschritt` werden zusätzlich je Ereignis eine JSON-Zeile nach stderr
geschrieben, ohne stdout zu beeinflussen.

### Emittierte Ereignisse

Es wird genau pro Lauf eine Zeile pro Ereignistyp geschrieben. Unbekannte
Felder sind zukünftig zulässig und müssen von Lesern ignoriert werden.

```json
{"typ":"start","datei":"…","seiten":42,"dpi":150}
{"typ":"seite","nr":7,"von":42,"sekunden":31.2,"herkunft":"ocr","entgleist":false}
{"typ":"seite","nr":8,"von":42,"sekunden":44.1,"herkunft":"ocr","entgleist":true,"grund":"zu lang 324%"}
{"typ":"fertig","ziel":"…","sekunden":1284.0,"entgleist":1}
```

- **start** — nach Analyse des PDFs, bekannt: Dateiname, Seitenanzahl, DPI
- **seite** — pro Seite: Seitennummer, Gesamtseiten, vergangene Sekunden,
  Herkunft (`textlayer`/`ocr`/`diagramm`), Entgleisungs-Flag, optional Grund
- **fertig** — vor der Zusammenfassungs-Ausgabe: Gesamtsekunden, Ziel-Path,
  Entgleisungs-Gesamtanzahl

### Versprechen

Es dürfen zukünftig weitere Felder zu den Ereignissen hinzugefügt werden.
Leser (Plugin, UI, Drittanbieter) müssen solchen ihnen unbekannten Felder
ignorieren und dürfen das Vorhandensein dieser Felder nicht als Fehler
werten.

## --check (Stufe 2)

Preflight-Prüfung ohne Modelllauf und ohne Eingabedatei. Antwortet in unter
einer Sekunde. `--check` weist ein überflüssiges PDF-Argument mit Fehler
zurück. Prüfungen:

| Name | Was | Fehlt wenn |
|---|---|---|
| `python` | Python-Version | < 3.9 |
| `fitz` | PyMuPDF importierbar | Import schlägt fehl |
| `mlx_vlm` | MLX-VLM importierbar | Import schlägt fehl |
| `modell` | Modell im HuggingFace-Cache | Kein Cache-Eintrag (respektiert `$HF_HUB_CACHE`, `$HF_HOME`) |
| `ausgabe` | Schreibrecht auf `--out` | Verzeichnis nicht anlegbar / nicht beschreibbar |
| `speicher` | Gesamt-RAM (macOS `sysctl`) | `n/a` = unbekannt trotzdem ok; unter 8 GiB = Warnung, kein Abbruch |

Ausgabe (Mensch): eine Zeile je Prüfung mit `[ ok ]` oder `[fehlt]`, plus
Warn-Zeilen und Zusammenfassung.

```bash
pdf2md.py --check --out _ocr-vorschau
# [ ok ] python: 3.12.4
# [ ok ] fitz: 1.24.10
# [fehlt] mlx_vlm: nicht installiert
# [fehlt] modell: nicht im Cache (~/.cache/huggingface/hub/models--...)
# [ ok ] ausgabe: .../_ocr-vorschau
# [ ok ] speicher: 16.0 GiB
# —
# fehlgeschlagen
```

`--check --fortschritt` liefert dasselbe als ein JSON-Dokument auf stdout:

```json
{"typ":"check","ok":false,"checks":[{"name":"python","ok":true,"detail":"3.12.4"},…],"warnungen":["speicher: …"]}
```

### Exit-Codes

- **0** — alle Prüfungen ok
- **2** — argparse-Fehler (z. B. PDF-Argument bei `--check`)
- **4** — mindestens eine Prüfung fehlgeschlagen
- **1** — (Wrapper) MLX-venv nicht vorhanden

Die Wrapper-Logik in `bin/pdf2md` ueberspringt bei `--check` den eigenen
fitz-Import-Check und laesst die Bewertung komplett an `pdf2md.py` — so
bleibt der Exit-Code einheitlich.
