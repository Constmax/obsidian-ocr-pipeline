# Bug Report: `--split-columns` — fehlerhafte Erkennung, Merge-Artefakte und Textverlust

**Datum:** 2026-07-06
**Betroffene Komponenten:** `scripts/column_tools.py` (detect/split/merge), `scripts/pdf-lib.sh` (Pipeline-Integration)
**Schweregrad:** Hoch — Datenqualitätsverlust in `raw/`, Kernversprechen des Features (Mischdokumente korrekt behandeln) nicht eingelöst
**Referenzdatei:** `raw/StR/Rep-Faelle/strafrecht-fall-01.pdf` (20 Seiten, Hemmer-Ordnerfotos; einspaltige Sachverhalts-/Schema-Seiten + zweispaltige Lösungsseiten)

---

## Zusammenfassung

Der Batch-Lauf vom 2026-07-06 hat alle 14 Dateien formal „erfolgreich" verarbeitet (Seitenzahl und A4-Format korrekt wiederhergestellt), aber die inhaltliche Prüfung von `strafrecht-fall-01.pdf` und `Verwaltungsprozessrecht.pdf` zeigt **fünf miteinander verkettete Defekte**. Die Erfolgs-Verifikation (Seitenzahl + dokumentweiter Garbage-Score) war zu grob, um sie zu erkennen.

| # | Defekt | Betroffene Seiten (Output) | Folge |
|---|---|---|---|
| B1 | Detection-**False-Positive**: einspaltige Seite als Zweispalter erkannt | S. 4 | Seite optisch zerteilt, Inhalt verkleinert, **kein Text-Layer** |
| B2 | Detection-**False-Negatives**: 8 von 15 echten Zweispalter-Seiten nicht erkannt | S. 7–10, 12, 15, 17, 19 | **Spaltenvermischung im Text-Layer** — das Originalproblem ist zurück |
| B3 | **Merge-Geometrie-Artefakte** auf korrekt gesplitteten Seiten | S. 6, 11, 13, 14, 16, 18, 20 | Sichtbare Naht, unterschiedlich skalierte Spalten, beschnittene Kopfzeilen |
| B4 | **Textverlust durch OCR-Skip** auf degenerierten Halbseiten | S. 4, 6 | Seiten haben **null** durchsuchbaren Text (nur Formfeed) |
| B6 | **Fehlpositionierter Text-Layer** nach Apple-Engine-Retry auf CropBox-Halbseiten (andere Datei, siehe unten) | `Verwaltungsprozessrecht.pdf` u. a. | Markierungen/Suchtreffer sitzen neben bzw. außerhalb des Druckbilds |

Die Originale sind unversehrt in git HEAD (`git show "HEAD:raw/..."` verifiziert) — **kein irreversibler Datenverlust**, aber die aktuell in `raw/` liegenden 14 reprocessten Dateien sind allesamt verdächtig und sollten bis zum Fix nicht als verlässliche Quelle gelten.

---

## Reproduktion (deterministisch verifiziert)

```bash
# 1. Original aus git extrahieren
git show "HEAD:raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" > /tmp/original.pdf

# 2. Pipeline-Vorstufen nachstellen (wie im Batch)
gs ... -dPDFFitPage -dDEVICEWIDTHPOINTS=595 -dDEVICEHEIGHTPOINTS=842 ... original.pdf → fixed.pdf
gs ... -dColorImageResolution=300 (Bicubic) ... fixed.pdf → downscaled.pdf

# 3. Split mit Auto-Detection
python3 column_tools.py split downscaled.pdf split.pdf --map map.json --auto
# → identisches Ergebnis wie im Batch: 8 gesplittet, 12 Vollseiten
```

**Reproduzierte Map:** Split auf Orig-Seiten 4 (x=341,5!), 6, 11, 13, 14, 16, 18, 20. Vollseiten: 1–3, 5, **7–10, 12, 15, 17, 19**.

Erwartung wäre gewesen: Split auf 6–20 außer 19 (= alle „Lösung"-Seiten, die zweispaltig sind), **kein** Split auf 4 (einspaltiges Schema „Vorbemerkung Seite 2").

---

## Befunde im Detail

### B1 — False Positive auf Seite 4 (einspaltige Schema-Seite)

**Symptom:** Output-Seite 4 zeigt links den (verkleinerten) Seiteninhalt, rechts einen schmalen Streifen, dazwischen eine künstliche weiße Lücke. Wörter sind an der Schnittkante x=341,5 mitten im Wort zerteilt.

**Ursache:** Das Quellfoto ist eine Ordner-Aufnahme, bei der **rechts der Rand der nächsten Blattseite sichtbar ist**. Nach dem `PDFFitPage`-Letterboxing belegt die eigentliche Seite nur den linken Teil des A4-Rahmens; der Folgeseiten-Streifen liegt rechts davon. Die zeilenbasierte Analyse sieht: viele „linke" Zeilen (Hauptinhalt), ≥ 3 „rechte" Zeilen (Textfragmente des Streifens), großer Leerraum dazwischen → klassifiziert als Zweispalter. Es fehlen zwei Plausibilitätsprüfungen:
1. **Maximale Gutter-Breite**: Ein echter Spaltensteg ist schmal (~3–8 % der Seitenbreite). Ein Leerband von > 15–20 % ist ein Seitenrand-Artefakt, kein Gutter.
2. **Massen-Symmetrie**: Beide „Spalten" müssen vergleichbar viel Text tragen. Ein Streifen mit einer Handvoll Fragmenten gegenüber einem vollen Textblock darf nie als zweite Spalte zählen.

### B2 — False Negatives auf 8 echten Zweispalter-Seiten

**Symptom:** Output-Seiten 7–10, 12, 15, 17, 19 (alles zweispaltige „Lösung"-Seiten) wurden als Vollseiten durch das normale PSM-1-OCR geschickt. Ergebnis: **Spaltenvermischung im Text-Layer ist zurück** — belegt auf Output-Seite 8, wo Sätze der rechten Spalte („Wehrlosigkeit des Opfers…") mit Gliederungspunkten der linken Spalte („Strafbarkeit des A / Mord, §§ 212 I, 211…") verschränkt extrahiert werden.

**Ursache:** Der Gutter-Test arbeitet mit **globalen Kanten** über die ganze Seite: `left_edge = max(xMax aller linken Zeilen)`, `right_edge = min(xMin aller rechten Zeilen)`, Gutter = Differenz. Bei **schief fotografierten Seiten verläuft der Spaltensteg diagonal** — die linke Spalte ragt oben oder unten über die vertikale Mittellinie, wodurch die globale linke Kante die globale rechte Kante überlappt (in den Debug-Messungen: Gutter −2,0 bis −4,8 pt) → Seite wird verworfen. Die Erkennung ist damit **nicht skew-tolerant**, obwohl schiefe Ordnerfotos der Standardfall dieser Quellen sind. (Zusätzlich fragil: Die y-Klusterung mit `LINE_Y_TOL = 3.0` pt zerfällt bei Schieflage, weil Wörter derselben visuellen Zeile > 3 pt yMin-Differenz über die Seitenbreite haben.)

### B3 — Geometrie-Artefakte beim Re-Merge

**Symptom:** Korrekt gesplittete Seiten (z. B. Output-S. 6, 11, 13, 14) zeigen eine sichtbare helle Naht in der Mitte, unterschiedlich skalierte Spalten (S. 11: linke Spalte deutlich kleiner als rechte) und links abgeschnittene/fehlende Kopfzeilen.

**Ursache:** Zwischen Split und Merge verändert ocrmypdf die Halbseiten unabhängig voneinander — insbesondere `--deskew` rotiert jede Hälfte einzeln, wodurch ihre Maße nicht mehr exakt den in der Map gespeicherten Rects entsprechen. `pikepdf add_overlay` skaliert dann **aspekterhaltend und zentrierend** in das Rect → Nähte, Versatz, ungleiche Spaltengrößen. Kontrollexperiment: **Split + Merge ohne OCR dazwischen ist verlustfrei** (Zeichenzahl pro Seite identisch, verifiziert für alle 8 Split-Seiten) — der Schaden entsteht ausschließlich durch die Per-Hälfte-Verarbeitung zwischen den beiden Schritten.

### B4 — Kompletter Textverlust auf Output-Seiten 4 und 6

**Symptom:** `pdftotext` liefert für Output-Seiten 4 und 6 exakt 1 Zeichen (Formfeed); `pdffonts` zeigt **keine einzige Font** — es gibt keinerlei Text-Layer.

**Ursache:** Tesseract übersprang degenerierte Halbseiten (Batch-Log: `Too few characters. Skipping this page` auf Split-Seiten 5 und 8 = rechter Streifen von Orig-4 bzw. Hälfte von Orig-6). Da mit `--force-ocr` gearbeitet wird, ist der alte Text-Layer zu diesem Zeitpunkt bereits verworfen → Skip = Seite endgültig ohne Text. Der dokumentweite Garbage-Score (0,345 → „bestanden") mittelt solche Totalausfälle einzelner Seiten weg.

### B6 — Fehlpositionierter Text-Layer nach Apple-Engine-Retry (Nachtrag, gemeldet vom User)

**Symptom:** In `raw/OeR/Verwaltungsrecht-AT/Verwaltungsprozessrecht.pdf` (PDF-Seite 22, „Verwaltungsprozessrecht, Seite 20") liegen die Textauswahl-/Markierungs-Boxen im Viewer neben dem Druckbild, teils über den Seitenrand hinaus. Per Overlay-Test (alle 1160 Wort-Bboxen auf das Seiten-Rendering gezeichnet) reproduziert: **Die Boxen der linken Spalte sind systematisch nach links verschoben**, Teile hängen außerhalb der Seite; die rechte Spalte passt weitgehend.

**Ursache (isoliert durch Kontrollexperiment):** Die Datei fiel im Quality-Gate durch (Tesseract, Score 0,447) → Retry Versuch 3 wechselte auf die **Apple-Vision-Engine** und lief mit `--force-ocr` auf den bereits gesplitteten CropBox-Halbseiten; das Ergebnis wurde als Best-Effort gespeichert und gemergt. Kontrollexperiment (Apple-OCR auf isolierte CropBox-Halbseiten aus dem Forensik-Split): Der **Apple-Plugin-Pfad behandelt Seiten mit `CropBox ≠ MediaBox` fehlerhaft** — die Output-Halbseite behält MediaBox 595 pt (CropBox 299 pt; `pdfinfo` und `pdftoppm` widersprechen sich bereits), und der unsichtbare Text wird gegenüber dem Bild horizontal versetzt geschrieben. Der Tesseract-Pfad normalisiert die Halbseiten dagegen korrekt auf echte ~297-pt-Seiten. Beim Merge wird der bereits intern versetzte Text mit eingebettet.

**Verschärfend:** Der Engine-Retry baut die OCR-Argumente neu auf (`build_ocr_args retry_args --force-ocr`) und **verliert dabei `--no-rotate` und `--clean`** — im Split-Pfad läuft der Retry also mit Optionen, die für Halbseiten explizit deaktiviert wurden.

**Betroffene Dateien (aus dem Batch-Log rekonstruiert):** Alle Dateien, deren Ergebnis aus dem Apple-Retry stammt und die gesplittete Seiten enthalten: `Verwaltungsprozessrecht.pdf` (10 Split-Seiten), `Verwaltungsrecht AT Fall 3/4/7/13.pdf`. — `BGB AT Fall 1.pdf` lief ebenfalls über den Apple-Retry, hatte aber 0 Split-Seiten (weiterer B2-Beleg: 11-Seiten-Dokument, keine einzige Zweispalter-Seite erkannt) → dort kein B6, aber Apple-Vollseiten-OCR mit Spaltenvermischungs-Risiko.

### B5 — Verifikationslücke (Prozess)

Der Batch-Erfolgstest prüfte nur `Seitenzahl(neu) == Seitenzahl(alt)` plus das dokumentweite Quality-Gate. Beides war grün, obwohl 10 von 20 Seiten defekt sind (2 ohne Text, 8 mit Spaltenvermischung). Es fehlt eine **seitenweise** Mindestprüfung (z. B. Zeichen/Seite > Schwelle für jede Nicht-Leerseite; Stichproben-Rendering).

---

## Auswirkungen

- `strafrecht-fall-01.pdf` (aktueller Stand in `raw/`): 10 von 20 Seiten defekt (siehe Tabelle oben).
- **Alle anderen 13 am 2026-07-06 reprocessten Dateien sind mit denselben Mechanismen verarbeitet worden und daher verdächtig** — insbesondere die Verwaltungsrecht-Dateien, deren Quality-Gate ohnehin nur „Best-Effort" meldete.
- Originale weiterhin vollständig in git HEAD verfügbar; Wiederherstellung jederzeit via `git checkout -- <datei>` möglich.

## Empfohlene Fixes (priorisiert)

1. **B2/B3 an der Wurzel: Deskew vor den Split ziehen.** Die ganze Seite einmal deskewen (eigene Pipeline-Stufe vor `split`), danach Split/OCR **ohne** `--deskew` auf den Hälften. Das macht die Erkennung skew-tolerant (vertikaler Gutter) und eliminiert die Maß-Drift zwischen Map und OCR-Ergebnis (→ keine Nähte/Skalierungssprünge; add_overlay-Rects passen wieder exakt).
2. **B1: Plausibilitätsregeln in `_analyze_columns`:** Gutter-Breite nach oben begrenzen (> ~15 % Seitenbreite = Artefakt, kein Steg) und Mindest-Textmasse pro Spalte relativ zur Gegenspalte fordern (z. B. schwächere Spalte ≥ 25 % der stärkeren).
3. **B2 zusätzlich: Gutter-Test robuster machen** — statt globaler max/min-Kanten z. B. Median der Zeilenkanten oder Gutter-Position pro Zeilenband bestimmen (toleriert Rest-Schieflage).
4. **B4: Per-Seite-Guard nach OCR:** Wenn eine Halbseite nach OCR 0 Zeichen hat, die andere aber Text trägt → Warnung + Seite als problematisch markieren; optional Fallback auf den alten Text-Layer statt `--force-ocr`-Totalverlust (`--redo-ocr` prüfen).
5. **B5: Batch-Verifikation seitenweise:** Vor dem Überschreiben von `raw/`-Dateien pro Seite Mindest-Zeichenzahl prüfen (Leerseiten-Ausnahme) und bei Verstoß nicht überschreiben.
6. **B6a: Halbseiten beim Split normalisieren** — in `column_tools.py split` nach dem gs-Crop die MediaBox jeder Hälfte auf die Crop-Region setzen (Ursprung 0,0), sodass `CropBox == MediaBox`. Damit verschwindet die Box-Ambiguität für **alle** Engines an der Quelle; der Apple-Pfad bekommt normale Seiten.
7. **B6b: Engine-Retry darf Split-Kontext nicht verlieren** — `ocr_with_retry` Versuch 3 muss die Argument-Flags des Aufrufers (`--no-rotate`, `--clean`) übernehmen statt sie neu (und unvollständig) aufzubauen. Zusätzlich diskutieren: Apple-Retry auf gesplitteten Inputs ganz unterbinden, solange B6a nicht verifiziert ist (die Skill-Doku stuft Apple für Spaltenlayouts ohnehin als ungeeignet ein).
8. **Sofortmaßnahme (unabhängig vom Fix):** Die 14 Dateien erneut aus git HEAD wiederherstellen; Neuverarbeitung erst nach Umsetzung von mindestens Fix 1, 2, 5 und 6.

## Positiv verifiziert (kein Handlungsbedarf)

- Seitenzahl- und Formaterhaltung des Merge-Bookkeepings (Map) funktioniert korrekt.
- Split + Merge ist ohne zwischengeschaltetes OCR verlustfrei (Text und Geometrie).
- Auf flach gescannten Seiten (frühere Scratchpad-Tests) arbeiten Erkennung, Split, OCR und Merge korrekt inkl. Highlight-Positionen — die Defekte sind spezifisch für **schiefe Ordnerfotos mit sichtbaren Nachbarseiten**, also genau den Hemmer-Bestand.

---

## Update 2026-07-06 (Folgesession): B1/B2/B4-Quality-Fail-Crash gefixt, B7 neu gefunden

### Status der oben empfohlenen Fixes

| Fix | Status | Anmerkung |
|---|---|---|
| 1 (Deskew vor Split) | **Nicht umgesetzt** — anders gelöst | Deskew wurde stattdessen für den Split-Pfad komplett deaktiviert (`--no-deskew`), löste B3 vollständig, opferte aber den einzigen wirksamen Skew-Fix für B2 (siehe Fix 3) |
| 2 (Plausibilitätsregeln B1) | Erster Versuch (Gutter-Breite + Massen-Symmetrie) **griff nicht** — Sliver bestand beide Checks | Ersetzt durch Fix „paarweise Gutter-Analyse" (siehe unten) |
| 3 (Gutter-Test robuster) | Erster Versuch (95./5.-Perzentil der globalen Kanten) **griff nicht** — Gutter blieb auf Schräg-Seiten negativ | Ersetzt durch Fix „paarweise Gutter-Analyse" (siehe unten) |
| 4 (Per-Seite-Guard B4) | Durch die Detection-Fixe indirekt gelöst | Keine degenerierten Halbseiten mehr → kein OCR-Skip mehr beobachtet |
| 5 (seitenweise Batch-Verifikation) | ✅ Umgesetzt und verifiziert | Neues Script `reprocess-raw` + `column_tools.py verify-pages` (siehe unten) |
| 6a (MediaBox-Normalisierung) | ✅ Umgesetzt und verifiziert (vom Bugfix-Modell) | Overlay-Test bestätigt: Textboxen exakt auf den Wörtern |
| 6b (Flag-Verlust im Retry) | ✅ Umgesetzt und verifiziert | `--no-rotate --no-deskew` werden jetzt korrekt durchgereicht |
| 8 (Sofort-Wiederherstellung) | Ausgeführt, dann erneut reprocesst — siehe unten | |

### Tatsächlicher Fix für B1 + B2: paarweise Zeilen-Gutter-Analyse (ersetzt Fix 2 + 3 komplett)

Statt eine globale linke/rechte Kante zu bestimmen (kollabiert bei Schräglage: Gutter wurde negativ, obwohl die Seite echt zweispaltig war — gemessen −5,9 bis −8,1 pt auf den echten Lösungsseiten), matcht `_analyze_columns()` jetzt jede linke Zeile mit ihrer **nächstgelegenen rechten Zeile gleicher Zeilenhöhe** (`PAIR_Y_TOL = 8.0` pt) und bewertet den Gutter **pro Paar**. Eine Seite gilt als zweispaltig, wenn ≥ 30 % der Paare (`PAIR_VALID_FRAC_MIN`) einen plausiblen Gutter (3–15 % Seitenbreite) zeigen; `split_x` = Median der plausiblen Paar-Mittelpunkte.

Dieser einzelne Algorithmus löst B1 und B2 gleichzeitig, weil Sliver-Fehlerkennungen (B1) strukturell keine konsistenten Paare bilden (valid_frac = 0,0 gemessen), während echte — auch schräge — Zweispalter-Seiten (B2) durchgehend valid_frac ≥ 0,34 zeigen. Verifiziert an `strafrecht-fall-01.pdf`: `detect` liefert jetzt exakt `{6...20}` als zweispaltig, `{1...5}` korrekt verworfen — deckungsgleich mit der seitenweise visuell geprüften Ground Truth. Regressionstests (einspaltiges BGH-Urteil, künstliches Mischdokument) bestehen unverändert.

### Zusätzlich gefixt: zwei weitere `set -e`-Abstürze

- `pdf-workflow.sh`/`pdf-combine.sh`: Nach `rm -f "$OUTPUT_FILE"` bei Quality-Gate-Fehlschlag lief das Script bis zum `du -h`-Aufruf auf der gelöschten Datei weiter → Absturz mit kryptischer Meldung. Jetzt: sauberer `exit 1` direkt nach dem Löschen. Verifiziert mit einer bewusst leeren Test-PDF (Exit-Code 1, klare Meldung, kein Crash).
- `merge_split_pdf()` und `split_two_column_pdf()` in `pdf-lib.sh`: Der interne `column_tools.py`-Aufruf war ungeschützt — ein Fehlschlag hätte das aufrufende Script sofort beendet, **bevor** der jeweils vorgesehene Fallback (Kopie der gesplitteten Version bzw. gs-Loop) überhaupt erreicht werden konnte. Beide jetzt mit `if ! ...; then` abgesichert.

### B7 (neu): Lesereihenfolge bei `pdftotext`-Standardmodus weiterhin vermischt

Trotz geometrisch korrektem Textlayer (Overlay-Test: alle Wortboxen exakt auf den gedruckten Wörtern) liefert `pdftotext` **ohne Flags** auf gemergten Zweispalter-Seiten weiterhin zeilenweise vermischten Text. Ursache: Poppler's Lesereihenfolgen-Heuristik (nicht die Geometrie) erkennt die aus zwei unabhängigen `add_overlay`-Aufrufen rekonstruierte Spaltenstruktur nicht zuverlässig und sortiert stattdessen grob nach Zeilenhöhe über die ganze Seite.

**Fix:** `pdftotext -raw` verwenden — folgt der Content-Stream-Reihenfolge statt Poppler's rekonstruierter Lesereihenfolge, und die ist bei uns garantiert korrekt (`merge_pdf()` schreibt immer erst links, dann rechts). Verifiziert: komplette linke Spalte gefolgt von kompletter rechter Spalte, auch auf normalen einspaltigen Seiten unverändert korrekt. Intern in `quality_check()` (`pdf-lib.sh`) und `verify_ocr_split()` (`column_tools.py`) umgestellt; Doku in SKILL.md und scripts-detail.md ergänzt. **Kein Vault-Script ruft `pdftotext` direkt auf `raw/`-PDFs auf** (Ingest liest PDFs offenbar visuell, nicht textbasiert) — praktischer Impact daher primär auf direkte manuelle Nutzung (Copy-Paste, eigene Scripts) begrenzt.

### Fix 5: `reprocess-raw` + `column_tools.py verify-pages`

Neues Script `scripts/reprocess-raw.sh` (Symlink `~/bin/reprocess-raw`), formalisiert das sichere Neu-Verarbeiten einer bestehenden `raw/`-Datei: verarbeitet eine Kopie, überschreibt das Original **nur** wenn (1) die Seitenzahl exakt erhalten bleibt und (2) `column_tools.py verify-pages` bestätigt, dass jede einzelne Seite ≥ 50 Zeichen hat (via `pdftotext -raw`, konsistent mit dem B7-Fix). Ein dokumentweiter Zeichen-Durchschnitt — wie ihn das normale Quality-Gate prüft — hätte den ursprünglichen B4-Fehler (einzelne komplett textlose Halbseiten in einem sonst großen Dokument) nicht zuverlässig gefangen; das war exakt die Lücke, die am 2026-07-06 die 14 Dateien unbemerkt korrumpiert hat.

Bei Fehlschlag (Seitenzahl-Mismatch, B5-Gate) bleibt die Quelle unverändert, das fehlerhafte Ergebnis wird als `<name>_FAILED_*.pdf` daneben abgelegt. Verifiziert:
- **Fehlerpfad**: bewusst leere Test-PDF → Quelldatei nachweislich unverändert (MD5-Vergleich), Exit-Code 1.
- **Erfolgspfad**: `reprocess-raw "raw/StR/Rep-Faelle/strafrecht-fall-01.pdf" --force-ocr --split-columns` — Split exakt 15/5 (deckungsgleich mit der verifizierten Ground Truth), 20/20 Seiten erhalten, B5-Gate bestanden, Original überschrieben.

`--allow-pages` nimmt bekannte Deckblatt-/Grafik-Seiten ohne Fließtext von Check 2 aus.

### Abschluss: alle 14 Dateien final mit `reprocess-raw` verarbeitet

Alle 14 ursprünglich betroffenen `raw/`-Dateien standen (unabhängig vom Autor — vermutlich durch einen zwischenzeitlichen `git checkout` — wieder auf dem sauberen, unkorrumpierten git-HEAD-Stand, verifiziert vor dem Lauf via `git status --porcelain`) und wurden mit `reprocess-raw --force-ocr --split-columns` final durchlaufen:

- **11 von 14 erfolgreich** (inkl. `strafrecht-fall-01.pdf` einzeln zuvor verifiziert): korrekte Seitenzahl, A4-Format, Quality-Gate + B5-Gate bestanden, überschrieben.
- **3 von 14 zunächst vom Gate abgefangen** — alle drei „Verwaltungsrecht AT"-Dateien (`Verwaltungsprozessrecht.pdf`, `Fall 13`, `Fall 3`): Das dokumentweite Quality-Gate in `pdf-combine` selbst schlug mit Tesseract *und* Apple-Retry fehl (Garbage-Score 0,42–0,46, Grenze 0,40) — noch **vor** dem B5-Gate, `pdf-combine` lieferte gar kein Ergebnis. `reprocess-raw` hat die drei Quellen korrekt unangetastet gelassen (verifiziert: weiterhin git-clean).

### B8 (neu): Garbage-Heuristik falsch-positiv bei deutschen Umlauten — Ursache der 3 „Fehlschläge"

Meine erste Erklärung für die 3 Ausreißer (dekorativer Hemmer-Briefkopf verzerrt den Seitendurchschnitt) war **falsch**. Tatsächliche Ursache, durch Wort-für-Wort-Analyse verifiziert: `_garbage_heuristic()` in `pdf-lib.sh` nutzte Bashs `[[ =~ ]]`-Bracket-Expressions (`[a-zäöüß][A-ZÄÖÜ]`) zur Erkennung von Binnengroßbuchstaben — und diese behandeln deutsche Umlaute fehlerhaft, reproduzierbar sogar unter `LC_ALL=C` (vermutlich ein Multi-Byte-UTF-8-Problem in Bashs Regex-Engine). Konkret: „für" (f-ü-r, kein einziger Großbuchstabe) matchte fälschlich als Binnengroßbuchstaben-Korruption.

An `Verwaltungsrecht AT Fall 3.pdf` gemessen: **417 von 529 (79 %)** als „mixed-case" markierten Wörtern enthielten schlicht einen Umlaut und waren völlig normale deutsche Wörter (für, Behörde, gemäß, künftig, gegenüber, Zuverlässigkeit, Verhältnismäßigkeit, ordnungsgemäß …). Weitere ~17 % waren legitime Verwaltungsrecht-Abkürzungen (BVerwG, VwGO, GewO, VwVfG, NVwZ, WaffG …). Nur ein kleiner Rest (~4 %) war echte (harmlose) OCR-Wortverschmelzung. Da Verwaltungsrecht-Vokabular überdurchschnittlich umlaut- und abkürzungsreich ist, drückte dieser Bug den Score systematisch nur in diesem einen Rechtsgebiet über die 0,40-Schwelle — kein tatsächliches OCR-Qualitätsproblem.

**Fix**: `_garbage_heuristic()` komplett auf Python umgestellt (`str.islower()`/`str.isupper()` pro Zeichen statt Bash-Bracket-Expressions — Unicode-korrekt, keine Locale-Abhängigkeit). Verifiziert: Garbage-Score auf `Fall 3.pdf` fiel von 0,459 auf 0,162 bei identischem Text. Regressionstest bestanden — echte Korruption („riSs" aus einem früheren Spaltenvermischungs-Beispiel) wird weiterhin erkannt, nur die Umlaut-Fehlalarme sind weg.

**Alle 3 Dateien danach erfolgreich mit `reprocess-raw --force-ocr --split-columns` verarbeitet:**

| Datei | Seiten | Split | Garbage-Score (vorher → nachher) |
|---|---|---|---|
| Verwaltungsprozessrecht.pdf | 54 | 51 gesplittet, 3 Vollseiten | 0,44 → 0,196 |
| Verwaltungsrecht AT Fall 13 | 11 | 7 gesplittet, 4 Vollseiten | 0,42 → 0,166 |
| Verwaltungsrecht AT Fall 3 | 7 | 4 gesplittet, 3 Vollseiten | 0,46 → 0,162 |

Damit sind **alle 14 ursprünglich betroffenen Dateien final auf dem korrigierten Stand** (B1–B8 alle gelöst und verifiziert).
