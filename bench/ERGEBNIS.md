# Benchmark-Ergebnis: PaddleOCR-VL lokal auf M1 (8 GB)

Stand 2026-07-30. Grundlage: 6 Seiten aus `raw/`, 300 dpi, plus 2 Kachel-Tests.
Rohdaten in `ergebnisse.csv`, Ausgaben in `out-A/` und `out-B/`.

Das Gate-1-Setupskript der Explorationsphase lag bis zur Installationswege-
Konsolidierung (Issue #7, PR #29) unter `pdf2md/setup.sh` (danach entfernt —
Einrichtung nur noch über `./setup.sh` im Repo-Root; Historie in git).

## Entscheidung

**PaddleOCR-VL via MLX, mit Spaltenkachelung** („Pfad C"). Modell:
`mlx-community/PaddleOCR-VL-1.5-4bit`, ~700 MB, mlx-vlm 0.6.8, Python 3.12.

Pfad A (PaddlePaddle CPU) und Pfad B (MLX ohne Kachelung) sind ausgeschieden.

## Messwerte

| | Pfad A | Pfad B | **Pfad C** |
|---|---|---|---|
| Sek./Seite, dichte Zweispalter | 9.226 | 111 (kollabiert) | **54–61** |
| Sek./Seite, duenne Seiten | — | 15–20 | **15–20** |
| Peak-RSS | 5.679 MB | 1.138 MB | **1.138 MB** |
| Modell-Ladezeit | 127 s | 1,6 s | **1,6 s** |
| Hochrechnung 2.922 Seiten | ~312 Tage | unbrauchbar | **~30 Stunden** |

Pfad A war auf 8 GB durchgehend im Swap — ein erheblicher Teil der 9.226 s ist
Plattenzugriff, nicht Rechenzeit. Praktikabel wird es dadurch nicht.

## Warum Pfad B allein scheitert

Der Kollaps folgt der **Textdichte, nicht dem Layout**:

| Seite | Baseline-Zeichen | Ergebnis |
|---|---|---|
| 03 Durchschlag | 754 | sauber |
| 06 Diagramm | 987 | sauber |
| 05 Diagramm | 910 | sauber |
| 04 einspaltig | 2.707 | sauber |
| 02 Zweispalter | 7.040 | **Wiederholungsschleife** |
| 01 Zweispalter | 8.590 | **Fragmentierung** |

Median der Scanseiten im Bestand: **3.662 Zeichen** — in der Grauzone. Etwa die
Haelfte des Bestands waere ohne Kachelung betroffen.

Gekachelt (Mittelschnitt + 2 % Ueberlappung) verschwindet der Effekt:
Seite 02 → 25,2 s + 28,5 s, Seite 01 → 32,0 s + 28,7 s, beide ohne Schleife.

## Was gut funktioniert

- **Geistertext-Unterdrueckung.** Seite 03 (Rueckseiten-Durchschlag) kommt
  komplett ohne eingemischten Spiegeltext durch. Das war das K.o.-Kriterium.
- **Handschrift.** Die Marginalie „→ nicht menschliche Angriffe" wird korrekt
  gelesen.
- **Fussnotenblöcke.** Fussnoten 39–42 auf Seite 01 vollstaendig, alle
  Aktenzeichen korrekt (`6 B 19/81`, `1 K 365/09.TR`, `Kopp/Schenke, § 58 VwGO,
  Rn. 12`).
- **Normzitate.** Auf Seite 02 fehlerfrei: `§ 37 II`, `§ 3a I VwVfG`,
  `§ 74 III 1 HBO`, `§ 49a I VwVfG`, `§ 3a II VwVfG`.
- **Koordinaten.** Jede Zeile kommt mit `<|LOC|>`-Bounding-Box — als Viereck,
  also inklusive Schraeglage. Damit sind Spaltenzuordnung, Leseordnung,
  Trennstrich-Zusammenfuehrung und Deskew nachtraeglich rekonstruierbar.
- **Pfeile auf Diagrammseiten** werden als `↓` mit Position erfasst.

## Bekannte Fehlerklassen

| Klasse | Beispiel | Korrigierbar |
|---|---|---|
| `§` als `$` | `($ 228 BGB)` | ja, mechanisch |
| LaTeX-Artefakte | `$\rightarrow$`, `\underline{...}` | ja, mechanisch |
| Wortfehler | `Verhaltungsakte`, `füßend`, `Rechtsbehelsfebehrung` | **nein** |
| Wortauslassung | `für den Betracht kommenden` (ohne „in") | **nein** |
| Sperrschrift | `S c h u 1 d v e r h ä 1 t n 1 s` | teilweise, ueber Koordinaten |
| Vollbreiten-Elemente | Fusszeile am Kachelschnitt abgeschnitten | ja, ueber Layout-Vorstufe |

Fehlerdichte korreliert mit Scanqualitaet: Seite 02 (sauber) nahezu fehlerfrei,
Seite 01 (schraeg, Kleindruck) mehrere Wortfehler pro Spalte.

**Konsequenz:** Die Original-PDFs duerfen nicht geloescht werden. Cloud-Ablage
mit Ruecksprung-Link im Frontmatter jeder `.md` ist Voraussetzung, nicht
Vorsichtsmassnahme.

## Diagrammseiten

48 von 2.922 Seiten (1,6 %) in 15 von 258 PDFs — Untergrenze, weil die
Heuristik in `pages.json` nur vektorielle PDFs erfasst, nicht die 51 %
Rasterscans. Text kommt durch, die **Box-zu-Box-Zuordnung nicht**: `§ 311a I`
(x=120) und `vorvertraglich,` (x=698) stehen in der Ausgabe verschraenkt. Ueber
die x-Cluster rekonstruierbar, aber nicht ohne Nachbearbeitung.

Empfehlung: Diagrammseiten zusaetzlich als PNG neben der `.md` ablegen.

## Offene Arbeit

1. **Zusammenbau-Schicht** — Koordinaten → Markdown: Spalten-Clustering,
   Leseordnung, Trennstriche, Ueberschriftenebenen, `$`→`§`. Deterministisch,
   keine Inferenzkosten. Validierbar gegen `out-A/04-einspaltig-sauber.md`.
2. **Layout-Vorstufe statt fester Mittelschnitt** — Vollbreiten-Zeilen erkennen,
   damit sie nicht am Kachelrand zerfallen. `column_tools.py` unterscheidet
   spaltengebundene von vollbreiten Zeilen bereits.
3. **Migration** — eigener Plan: 701 `[[raw/...pdf]]`-Wikilinks umschreiben,
   Cloud-Ablage vorher stehen, `raw/` erst nach verifiziertem Durchlauf
   anfassen, Normzitat-Diff als Gate.

## Nachtrag 2026-07-30: zwei echte Dateien durchlaufen

`pdf2md.py` (Pfad C Ende-zu-Ende) an `fall12-begleitskript.pdf` und
`BGB AT Fall 3.pdf`. Vier Laeufe, drei Bugs — alle drei nur an echten Dateien
sichtbar, keiner am 6-Seiten-Benchmark:

1. **Kachelung war eine Dichte- statt Layout-Entscheidung.** `fall12` S. 2 ist
   einspaltig und wurde laengs mittig zerschnitten, quer durch jede Zeile →
   Inhaltsverlust und halluzinierte Halbwoerter. Jetzt: Laengsschnitt nur bei
   erkannten Zweispaltern, dichte einspaltige Seiten waagerecht.
2. **Tintenprofil auf `max()` normiert.** Das Maximum ist bei gescannten
   Skripten der Ringbindungs-Schatten am Blattrand, nicht Text — dadurch galten
   alle 5 Seiten von Fall 3 als einspaltig. Jetzt Median als Referenz, plus
   zweites Signal: Anteil der Zeilen, deren rechte Kante am Steg endet
   (42–49 % bei Zweispaltern, 15–22 % einspaltig; Schwelle 30 %).
3. **Spaltentrennung lief erneut innerhalb der Kachel.** Die Kachel ist bereits
   eine Spalte; der Algorithmus deutete die Gliederungs-Einrueckung als zweite
   Spalte und zog Absatz-Schlusszeilen nach vorne. Jetzt nur bei ungekachelten
   Seiten.

**Messwerte `BGB AT Fall 3.pdf`** (5 S., Zweispalter, schraeg, Ringbindung,
Rueckseiten-Durchschlag): 164,9 s = **33,0 s/Seite**. Zeichenausbeute 0,98–1,04
zur Baseline, keine Degeneration. Gliederung `3. → a) → b) → aa) → bb) →
(1)(2)(3) → II. → B.` vollstaendig erhalten.

**Normzitat-Diff gegen den bestehenden Tesseract-Layer:** 3:1 fuer Pfad C.
Tesseract verschmilzt `§ 119 I` → `§ 119l` (ebenso 142, 812); Pfad C liest diese
korrekt, macht aber aus `§ 130 I` ein `§ 1301`. Dominante Restfehlerklasse
beider Systeme: **roemisches I in Normzitaten** (`1` / `l` / `|`). Teilweise
mechanisch korrigierbar, im Zweifel als unsicher markieren statt raten.

**Architektur-Korrektur:** `fall12` ist born-digital mit perfektem Textlayer —
OCR darauf ist sinnlos und riskant. Rund 49 % der Seiten im Bestand sind
vektoriell. Richtige Aufteilung:

1. born-digital → Text direkt extrahieren, verlustfrei, kein Modell
2. Rasterscan → Layout erkennen → kacheln wenn mehrspaltig → OCR → Zusammenbau

Das halbiert die 23 h und nimmt fuer die Haelfte des Bestands jedes
Fehlerrisiko heraus.

**Noch offen:** vollbreite Kopfzeilen zerfallen am Kachelrand
(`Juristisches Repetitorium Erlangen - F Hamburg - Ha hemmer`); umrandete
Kaesten ohne Schlagwort (`Uebergabesurrogat nach § 931 BGB…`) verschmelzen mit
dem Vorabsatz — braucht Rahmenerkennung, nicht Schlagwortliste.

**Werkzeug:** `detect_test.py` klassifiziert Spaltigkeit ohne Inferenz —
54 Seiten in unter einer Sekunde. Damit laesst sich der Bestand vorab pruefen,
statt Layoutfehler nach 23 h Rechenzeit zu finden.

## Nebenbefund (Geklärt in Nachtrag 16)

140 Scanseiten hatten unter 50 Zeichen im damaligen Erfassungsstand von `pages.json`.
Die vollständige Analyse und Aufschlüsselung findet sich in **Nachtrag 16**: Es handelte
sich um archivierte Rohscans in `_archive`/`repair-stage`, ein inzwischen nach Markdown
überführtes Skript und eine handschriftliche Skizze — im aktiven Bestand fehlt kein Text.

## Nachtrag 2026-07-30 (2): Wie schnell geht Pfad C überhaupt?

Gemessen, nicht geschätzt: `speed_test.py` und `tune_test.py` zerlegen die
33 s/Seite in Prefill (Bildverarbeitung) und Decode (Texterzeugung) und prüfen
jede Stellschraube gegen die Genauigkeit.

### Wo die Zeit hingeht

Eine Spaltenkachel kostet **~10 s Prefill + 10–23 s Decode**. Der Prefill ist
fast konstant, weil der Bildvorverarbeiter bei `max_pixels = 1.003.520` (~1 MP)
deckelt: jede Kachel landet bei **1257 Bildtoken**, egal ob aus 150, 200 oder
300 dpi gerendert. Der Decode skaliert mit der Textmenge.

| Render-dpi | Kachel | Bildtoken | Prefill | Decode | gesamt |
|---|---|---|---|---|---|
| 300 | 4,52 MP | 1257 | 9,9 s | 10,1 s | 20,0 s |
| 200 | 2,01 MP | 1257 | 9,9 s | 10,7 s | 20,6 s |
| **150** | 1,13 MP | 1257 | 9,9 s | 10,1 s | 20,0 s |
| 110 | 0,61 MP | 800 | 5,1 s | 9,1 s | 14,3 s |
| 90 | 0,35 MP | 550 | 3,2 s | 8,5 s | 11,7 s |

**150 dpi ist die richtige Voreinstellung** (vorher 300): identische Modelleingabe,
identische Laufzeit, ein Viertel der Renderarbeit.

### Warum nicht einfach 110 dpi

Weil unter ~141 dpi die Kachel den Deckel unterschreitet und echte Auflösung
verloren geht. Auf der sauberen Seite ist 110 dpi ein Tausch (repariert
`§ 854 I`, bricht Umlaute). Auf dem harten Rasterscan
(`Verwaltungsprozessrecht.pdf` S. 10, native 204 dpi) ist es ein Bruch:

- `Rechtsbehelfs` → `Rechtsbehels` durchgehend, `VwVfG` → `VwVFG`/`VwVG`/`VwFG`
- Aktenzeichen verfälscht: `11 K 4808/10.F` → `4888/10.F`, `9 L 251.16` → `9 L 25.15`
- vierfache Wiederholungsschleife im Fußnotenblock, Fußnoten 40 und 42 verloren
- Wortdeckung 46,2 % → 41,5 %

Bei 90 dpi kollabiert die Seite vollständig (98 s, Ziffernschleife). Die
Auflösungsgrenze ist eine **Kante, kein Gefälle** — deshalb keine dpi-Sparoption.

### Verworfene Stellschrauben (alle gemessen)

| Ansatz | Ergebnis |
|---|---|
| `prefill_step_size` 1024 / 2048 | keine Wirkung |
| `kv_bits` 8 / 4 | 5 % **langsamer** |
| 2 Prozesse parallel (8 Kacheln) | 113,5 s → 102,3 s = nur −10 %, dafür doppelter RAM. GPU ist schon ausgelastet |
| Beschnitt auf den Tintenkasten | Tintenkasten ist **100 %** der Seite — Ringbindungs-Schatten setzt an jedem Rand Tinte |
| Prompt ohne Grounding (`"…no coordinates"`) | −18 %, aber ohne Koordinaten keine Spaltentrennung, keine Absatzlogik, kein Fett |
| Prompt `"OCR:"` / `"Text Recognition:"` | degeneriert bis `max_tokens`, 104 s |

### Der eine große Hebel: nicht rechnen, was man ablesen kann

**48,8 % des Bestands (1.426 von 2.922 Seiten) sind vektoriell** und brauchen
überhaupt kein Modell. `seiten_analysieren()` entscheidet je Seite über den
Bildflächenanteil; vektorielle Seiten gehen durch `textlayer_zeilen()`.

Das ist nicht nur schneller, sondern **besser**: Text exakt statt erkannt,
Koordinaten aus dem Satz statt geschätzt, und **`**fett**` aus dem Font-Flag** —
damit sind fette Wörter *mitten im Satz* erreichbar, was über das Modell
grundsätzlich nicht geht (es liefert Zeilen-, keine Wortboxen).

| | vorher | jetzt |
|---|---|---|
| `fall12-begleitskript.pdf` (2 S., vektoriell) | ~70 s | **0,2 s** |
| `BGB AT Fall 3.pdf` (5 S., Rasterscan) | 165,4 s | 167,6 s |
| Hochrechnung 2.922 Seiten | ~24–27 h | **~13 h** |

### Nebenbefund: ein Fehler, den die Fetterkennung eingebaut hatte

`AUFZAEHLUNG` und `SCHLAGWORT` prüfen `^`-verankert — bekamen aber
`**I. Der Ausgangspunkt…**`. Die Fett-Sternchen haben **jeden Gliederungsmarker
unsichtbar gemacht**, auf beiden Pfaden. Absätze je Seite nach dem Fix:
S. 2 30 → 42, S. 3 15 → 18. Marker werden jetzt vor der Prüfung abgestreift;
eine kurze vollständig fette Zeile gilt als Überschrift und beginnt einen
eigenen Absatz.

### Offen

- 18 vektorielle Seiten mit unter 100 Zeichen gehen vorsorglich ins OCR. Echte
  Leerseiten ließen sich über den Tintenanteil vorher aussortieren (~11 s je Seite).
- Tabellen: Zellen werden in Leserichtung aneinandergehängt, die Zuordnung
  Zeile/Spalte geht verloren. Betrifft beide Pfade.

## Nachtrag 2026-07-30 (3): Tabellen als Markdown-Tabellen

### Wo Tabellen überhaupt sitzen

Zuerst gemessen, dann gebaut. Das Ergebnis hat die Bauentscheidung umgedreht:

- **Das Modell gibt kein Tabellen-Markup aus.** Auf einer Seite mit bekannter
  Tabelle liefert es 0 `|`, 0 `<table>` — nur Zelle für Zelle als eigene Zeile
  mit Koordinaten, in Leserichtung.
- **Echte Tabellen gibt es nur im vektoriellen Bestand:** 40 Kandidaten (≥ 2×2)
  in 21 von 258 Dateien, nach Inhaltsfilter **21 echte Tabellen**.
- **In den Rasterscans gibt es praktisch keine.** Ein Liniendetektor über 120
  Scanseiten findet auf 11,7 % ein Gitter — aber die Sichtprüfung von drei
  Treffern zeigt durchweg **umrandete Kästen** (Rechtsprechung, Prüfungsschema,
  `ÜBERSICHT FALL 12`), keine Tabellen.

Deshalb läuft die Tabellenausgabe über `page.find_tables(strategy="lines_strict")`
im Textlayer-Pfad — exakt, ohne Inferenz — und **nicht** über eine Rekonstruktion
aus OCR-Koordinaten, die auf diesem Material fast nur Fehlalarme produzieren
könnte.

### Zwei Fallen, beide gemessen statt geraten

**`strategy="text"` ist unbrauchbar.** Die Variante, die Spalten aus
Wortabständen ableitet, hat auf einer Hemmer-Seite eine **74×6-Tabelle aus
reinem Fließtext** erfunden. `lines_strict` verlangt echte Trennlinien und liefert
auf derselben Seite korrekt 6×3.

**Gitterform genügt nicht als Kriterium.** Ein einzelner Kasten in einer
Übersicht kommt als 3×2 mit einer gefüllten Zelle heraus:

```
| § 280 I, II, nur wenn Verzug nach § 286 ! |  |
| --- | --- |
|  |  |
```

Als Tabelle formatiert wäre das eine Erfindung. Der Filter streicht darum leere
Zeilen und Spalten und verlangt danach echte Zweidimensionalität — mindestens
zwei Zeilen mit je mindestens zwei gefüllten Zellen. Wirkung: 40 → 21 Tabellen,
`schuldrecht-at-teil-2` von 6 auf 1, `2-BGB_AT_Teil__2` von 5 auf 3. Verworfene
Kästen fließen unverändert durch den normalen Textweg; gegengeprüft, dass die
Ausgabe mit und ohne Tabellenerkennung dort **byte-identisch** ist.

### Was die Tabelle im Durchlauf schützen muss

`saeubern()` verwandelt `|` nach einem Normzitat in ein römisches I — aus
`| § 275 BGB | ja |` würde `§ 275 BGB I ja`. Tabellen laufen darum als **ein
unantastbarer Block** durch die Absatzlogik: keine Säuberung, kein
Boilerplate-Test, keine Fußnoten-Markierung, kein Verschmelzen mit Nachbarn.

**Fett je Zelle** kommt aus den Span-Flags, aber der Zelltext bleibt aus
`extract()`. Den Text aus dem Zell-Clip neu zusammenzusetzen wäre riskanter:
Spans der Nachbarzelle ragen in den Clip und würden Inhalt verfälschen. Fett ist
darum ein Ja/Nein je Zelle — nicht wortweise, aber ohne jedes Risiko für den Text.

### Ergebnis

| | vorher | jetzt |
|---|---|---|
| `fall12` Tabelle | Zellen verschränkt: `**§ 281 BGB** ja – aber nur gegen den BGH V ZR 89/15; … bösgläubigen oder verklagten Besitzer` | korrekte 6×3-Markdown-Tabelle |
| Regressionslauf | — | 256 vektorielle Seiten, 21 Tabellen, **0 Ausnahmen, 0 strukturell kaputte Tabellen** |

Das war kein Formatierungs-, sondern ein **Inhaltsfehler**: die Zellinhalte waren
ineinander verschränkt, das Aktenzeichen stand in der falschen Spalte.

### Offen

- **Flussdiagramme aus Kästen** (`schuldrecht-at-teil-2` S. 4) verschränken sich
  weiter im Fließtext. Das ist das bekannte Box-zu-Box-Problem, keine Tabelle —
  es braucht Kastenerkennung, die den Kasten als eigenen Absatz ausgibt. Der
  Liniendetektor in `gitter_test.py` liefert dafür die Grundlage.
- Tabellen in **Rasterscans** werden nicht erkannt. Nach der Bestandsmessung ist
  dort kaum Material; falls doch eines auftaucht, bleibt es Fließtext.

## Nachtrag 2026-07-30 (4): Kästen als Absätze, Diagramme als Bild

### Zwei Sorten Rechteck, zwei Behandlungen

| | Erkennung | Behandlung |
|---|---|---|
| **gestapelter Kasten** (Rechtsprechung, Prüfungsschema) | Breiten praktisch gleich | Kasten = eigener Absatz |
| **Diagramm** (Flussdiagramm, Pfeilbaum) | Breiten streuen **oder** ≥ 2 Schräglinien | Seitenbild + Text im eingeklappten Callout |

Bestand: **80 Diagrammseiten (2,7 %)** in 23 Dateien, **1.036 Seiten mit
gestapelten Kästen**.

### Das entscheidende Merkmal war nicht das erwartete

Zuerst gebaut: „zwei Kästen auf gleicher Höhe nebeneinander → Diagramm", mit
Spaltenbezug über `layout_erkennen()`. Das lag in **beide** Richtungen falsch —
es hielt einen Hemmer-Zweispalter mit gestapelten Kästen für ein Diagramm und ein
einspaltiges Diagramm für einen Zweispalter. `layout_erkennen()` ist auf Prosa
abgestimmt; auf Diagrammseiten ist es unbrauchbar.

Die Messung zeigte ein viel schärferes Merkmal: **die Streuung der
Kastenbreiten.**

| | Breiten-Cluster | Streuung |
|---|---|---|
| Diagramme (4 Seiten) | 4–5 | 0,15–0,22 |
| gestapelte Kästen (7 Seiten) | 1–2 | 0,000–0,028 |

Der Grund ist strukturell und darum belastbar: **ein umrandeter Kasten füllt die
Textspalte, ein Diagrammkasten ist auf seinen Inhalt zugeschnitten.** Steg- und
Nachbarschaftsprüfung sind ersatzlos entfallen.

### Zweites Merkmal für rahmenlose Diagramme

Das Breiten-Kriterium sieht `schuldrecht-at-teil-2` S. 2 nicht — ein Pfeilbaum
ohne Kästen, nur Text und Pfeile. Ergänzung: **Zeichenbefehle mit echter
Schräglinie.** Gemessen: Prosa 0, Tabellen 0, Diagramme 1–6.

Kurven müssen dabei **draußen bleiben**. Zählt man sie mit, kommt die 36×5-Tabelle
`Kursplan_KLK_H_2026.pdf` auf 58 Treffer und würde durch ein Bild ersetzt statt
tabelliert. Zusätzlich sind Tabellenflächen von der Kastensuche ausgenommen —
sonst gelten 47 Tabellenzellen als 47 Kästen. Gegengeprüft: der Kursplan
enthält weiter 36 Tabellenzeilen und 0 Bilder.

Für Rasterscans trägt nur das erste Merkmal — Zeichenbefehle gibt es dort nicht.

### Bild UND Text, nicht Bild statt Text

`semantic_search.py` indiziert Text, keine Bilder. Eine Diagrammseite nur als
Bild abzulegen macht sie unfindbar — genau das, was die Umstellung auf `.md`
vermeiden soll. Darum: Bild zuerst, darunter der Text in einem eingeklappten
Callout mit dem Hinweis, dass die Reihenfolge nicht verlässlich ist. `--diagramm-nur-bild`
schaltet den Text ab (und spart bei Scans die Inferenz).

Bildkosten: ~215 kB je Seite bei 150 dpi, also **~17 MB für den ganzen Bestand**.
Die Bilder müssen im Vault liegen, nicht in der Cloud — sie sind auf
Diagrammseiten der eigentliche Inhalt.

### Kasten-Absätze

Der Fehler aus Nachtrag 2 ist behoben: `Übergabesurrogat nach § 931 BGB durch`
hing an `**als von Anfang an nichtig anzusehen**`, weil der Kasten weder
Schlagwort noch Gliederungsmarker mitbringt. Jetzt erzwingt jeder Kastenwechsel
einen Absatz.

Bei Rasterscans wird die Zuordnung **nur über die Höhe** gemacht: die
Modellkoordinaten sind kachelrelativ in x, aber der senkrechte Kachelschnitt
lässt die volle Blatthöhe stehen — y ist direkt vergleichbar, x nicht. Bei
waagerechter Kachelung (dichte einspaltige Seiten) verschiebt sich y, dort
bleibt die Zuordnung darum aus.

Gegenprobe `BGB AT Fall 3.pdf`: 33,3 s/Seite unverändert, 0 falsche Fußnoten,
8 Fußnotendefinitionen, 3 Literaturangaben, 0 verschmolzene Normzitate.

### Was ich mir dabei selbst widerlegt habe

Dreimal habe ich eine Seite von Hand als „Prosa" oder „Kästen" gelabelt und der
Detektor hat widersprochen — dreimal hatte der Detektor recht:
`teil-5` S. 6 (Dreiecksdiagramm über Fließtext), `teil-2` S. 1 (Pfeilbaum unter
Aufzählung), `teil-2` S. 2 (rahmenloser Baum). Alle drei sind **Mischseiten**.
Das ist auch das Argument für Bild + Text statt Bild allein: auf einer
Mischseite ist die Hälfte des Blattes ganz normal lesbarer Text.

### Offen

- **Diagramme in Rasterscans**: die Bestandsmessung findet dort keine. Ob das
  stimmt oder am fehlenden zweiten Merkmal liegt, ist offen — Pfeile im Bild zu
  erkennen bräuchte Linienverfolgung, nicht nur Projektionen.
- Die OCR-Ausbeute schwankt zwischen Läufen leicht (S. 3: 3.496 / 3.556 / 3.830
  Zeichen bei `temperature=0`). Ursache nicht untersucht; vermutlich
  Reduktionsreihenfolge in den MLX-Kernen.

## Nachtrag 2026-07-30 (5): Vier Fehler am Seitenübergang

In Obsidian sichtbar geworden, nicht in den Kennzahlen. Alle vier durch die
Änderungen aus Nachtrag 4 verursacht oder freigelegt.

### 1. Ein Rechteck pro Textzeile — mein Fehler

PDFs hinterlegen eine schattierte Passage häufig **zeilenweise**: der Kasten
„Zwei Fallen im Fall 12" besteht aus vier gleich breiten Streifen von je ~17 pt.
`min_h=14` ließ jeden davon als Kasten durchgehen, und weil ein Kastenwechsel
einen Absatz erzwingt, zerfiel der Kasten in **eine Zeile pro Absatz**.

Zwei Gegenmaßnahmen:

- `_verschmelzen()` fasst senkrecht anschließende Rechtecke gleicher Breite
  zusammen.
- Für die **Absatztrennung** zählen nur Kästen mit ≥ 2 Textzeilen. Für die
  **Diagrammentscheidung** zählen einzeilige weiter mit — dort tragen ihre
  Breiten das Kriterium.

Absätze `fall12`: S. 1 17 → 11, S. 2 34 → 20.

### 2. Spaltentrennung erfand eine zweite Spalte

`fall12` S. 2 ist einspaltig, hat aber x-Startpositionen 119/128/142 **und**
380/496/655 — drei zentrierte Zeilen. Die Lücke von 238 überschritt die Schwelle
von 25 % der Textbreite, die Seite galt als Zweispalter, und die Umsortierung zog
Zeilenreste an den Anfang:

```
Verurteilung anerkannt.[^3]     ← Rest eines Absatzes von weiter unten
Rechtshängigkeit.
**ZR 67/22**                    ← Rest einer Überschrift
Schlagworten fassen:
```

**Eine Lücke allein beweist keine Spalte.** Jetzt muss auch jede Seite des Stegs
mindestens 25 % der Zeilen halten. Echte Zweispalter liegen bei ~50 % und sind
nicht betroffen — `BGB AT Fall 3.pdf` erkennt weiter alle vier Seiten
zweispaltig (@50–53 %).

### 3. Laufende Kopfzeile — jetzt ohne Wortliste

`Schuldrecht AT – Fall 12 | Begleitskript` stand in keiner Hemmer-Stichwortliste
und klebte am Ende an einer Fußnotendefinition. Statt die Liste zu verlängern:
`laufende_zeilen()` sammelt Texte, die auf **mehreren Seiten in der Kopf- oder
Fußzone gleich lauten**. Eine laufende Kopfzeile beweist sich durch Wiederholung,
nicht durch Vokabular.

Wirkung über die eine Datei hinaus: bei `teil-5` fliegen jetzt auch
`SchR AT Teil 5: Schadensrecht, §§ 249 ff. BGB` und die Fußzeile
`RA Clobes/RA Dr. Issa/Ass. jur. Motel` heraus.

Beim Bauen selbst reingefallen: beim Sammeln normiere ich Leerraum, beim
Vergleichen zunächst nicht — die Kopfzeile enthält `Fall 12  |  Begleitskript`
mit **doppelten** Leerzeichen und traf darum nie.

### 4. Seitenmarke war sichtbar

`<!-- S. 2 -->` zeigt Obsidian in der Live-Vorschau an. Jetzt `%% S. 2 %%` —
Obsidians eigene Kommentarsyntax, in Live-Vorschau und Leseansicht unsichtbar.

### Regression geprüft

| | |
|---|---|
| Diagrammseiten `teil-5` | unverändert 5 (S. 2, 4, 5, 6, 7) |
| gestapelte Kästen `BGB-AT_10` | unverändert 6 / 4 / 3, kein Diagramm |
| `BGB AT Fall 3.pdf` | 33,1 s/Seite, 0 falsche Fußnoten, 8 Definitionen, 3 Literaturangaben, 0 verschmolzene Normzitate, `aa)/bb)/cc)` erhalten, Kasten weiter eigener Absatz |

### Lehre

Die Kennzahlen sahen bei allen vier Fehlern gut aus — Zeichenausbeute, falsche
Fußnoten, Normzitate, alles im Rahmen. Sichtbar wurden sie erst **im Zielprogramm
an der Seitengrenze**. Absatzzahlen wären das Warnsignal gewesen: S. 2 sprang von
25 auf 34, und ich habe das als Wirkung des Kasten-Features gelesen statt als
Symptom.

## Nachtrag 2026-07-30 (6): Gescannte Diagramme — gescheitert, mit Notausgang

`Strafrecht AT VI - Fahrlaessigkeit.pdf` S. 5 ist ein Baumdiagramm
(Unterlassungsdelikte → Echte / Unechte) und wird **nicht** erkannt. Vier Signale
geprüft, alle vier versagen auf diesem Material:

| Signal | Ergebnis auf S. 5 |
|---|---|
| Streuung der Kastenbreiten | alle drei Kästen ~30 % breit → keine Streuung |
| Schräglinien (Pfeile) | nur vektoriell verfügbar; das hier ist ein Scan |
| Rahmenprüfung im Bild | siehe unten — **keine Schwelle trennt** |
| Pfeilzeichen in der OCR-Ausgabe | 0 auf S. 5, dafür 4 auf S. 8 (kein Diagramm) |

### Warum die Rahmenprüfung nicht geht

Erst war die Schwelle falsch: mit `mindest=0.12` muss eine Längslinie 12 % der
Seitenhöhe überspannen, ein Diagrammkasten ist aber nur **2,5 %** hoch. Seine
senkrechten Kanten waren unauffindbar, also entstand gar kein Rechteck.

Nach Absenken auf 2 % entstanden Phantomkästen — der Code paart bloß benachbarte
Linienpositionen, und auf dichten Seiten enthält fast jedes Rechteck Text. Deren
gestreute Breiten machten reihenweise Prosaseiten zu Diagrammen (`BGB-AT_10`
S. 3–5, `Verwaltungsrecht Fall 7` S. 3–5, `BGB AT Fall 3` S. 3–4).

Also alle vier Kanten im Bild nachgeprüft. Gemessene beste Kantendeckung:

| Seite | Band ±25 px |
|---|---|
| Fahrlässigkeit S. 5 — 3 echte Kästen | **0,17** |
| BGB-AT_10 S. 3 — 6 echte Kästen | 0,98 |
| BGB AT Fall 3 S. 4 — dichte Prosa, 0 Kästen | 0,95 |

Prosa und echte Kästen liegen beide bei 0,95 — **nicht trennbar**. Und die Kästen
der Zielseite bleiben bei 0,17, weil dieser Scan sehr hell ist (Median-Helligkeit
253) und die Linien blassgrau bei ~200 liegen, oberhalb der Schwelle von 170.
Dazu die Schräglage: eine Kante wandert über Dutzende Pixelzeilen, ein schmales
Band erfasst sie nie, ein breites nimmt Prosa mit.

Drei Ursachen zugleich — schwankende Linienhelligkeit, Schräglage, linienartige
Strukturen in Prosa. Deshalb **zurückgenommen** auf den geprüften Stand, statt eine
vierte Heuristik zu versuchen.

### Was der Fehler konkret kostet

Die zwei nebeneinanderstehenden Kästen verschmelzen zu einer Zeile:

```
**Echte Unterlassungsdelikte Unechte Unterlassungsdelikte z.B.: § 323 c §13**
```

Der Text ist vollständig, die **Zuordnung zerstört**: dass `§ 323 c` zu *Echte* und
`§ 13` zu *Unechte* gehört, steht nicht mehr drin. Das ist der ganze Inhalt der
Grafik.

### Notausgang

`--diagramm-seiten 5` (auch `5,7-9`) erklärt Seiten von Hand zum Diagramm. Für
~80 automatisch erkannte Seiten plus einzelne Nachträge ist das der verlässliche
Weg; der Nutzer sieht die Seite, der Detektor nicht.

### Nebenbefund: Bildgröße über dpi zu steuern war falsch

Dieselben 150 dpi ergaben bei einem Vektorskript 200 kB und bei dieser Seite
**6,6 MB** — Seitenrechtecke schwanken stark. Jetzt begrenzt `--bild-max-kante`
(Standard 1800 px) die Pixelkante: 1,13 MB für den Scan, 200–280 kB für
Vektorseiten.

## Nachtrag 2026-07-30 (7): Drittes Diagramm-Merkmal — Kästen nebeneinander

`schuldrecht-at-zusatzuebersichten.pdf` S. 7 („Übersicht zur Verspätung der
Leistung": drei Spalten mit Kästen) und S. 8 („Übersicht zum Schadensersatzrecht":
Norm links, Bedeutung rechts) wurden **nicht** erkannt. Beide sind
**gleich breite Kästen im Raster** — S. 7 hat drei Spalten à 0,20 Breite, also
Streuung null. Merkmal 1 kann sie prinzipiell nicht sehen, Merkmal 2 (Schräglinien)
greift nur bei Pfeilbäumen.

Drittes Merkmal, **nur für vektorielle Seiten**: zwei Kästen auf gleicher Höhe
ohne x-Überschneidung. Auf Rasterscans bleibt es draußen — dort müssten die
Kästen erst aus Linien rekonstruiert werden, und das liefert (Nachtrag 6) zu
viele Fehlalarme.

Damit sind es drei Merkmale, mit ODER verknüpft:

| | fasst | gilt für |
|---|---|---|
| Streuung der Kastenbreiten | gerahmte Diagramme mit ungleichen Kästen | alle |
| Schräglinien | rahmenlose Pfeilbäume | vektoriell |
| Kästen nebeneinander | Raster gleich breiter Kästen | vektoriell |

### Der Fehlalarm, der dabei entstand

80 → 107 Seiten, aber unter den 27 neuen waren **Doppelseiten-Layouts**: zwei
logische Seiten nebeneinander auf einem Querblatt (`2131_Zusatzmaterial`,
`2135_Zusatzmaterial`). Deren zwei Seitenrahmen liegen nebeneinander — und das
ist dichte Prosa, die als Bild ein klarer Rückschritt wäre.

Unterscheidungsmerkmal aus der Messung:

| | Breite × Höhe |
|---|---|
| Seitenrahmen einer Doppelseite | 0,50 × **0,99** |
| echte Diagrammkästen (Maximum über alle geprüften) | ≤ 0,47 hoch |

Der bestehende Flächenfilter (75 %) lässt 0,50 × 0,99 = 0,495 durch. Ergänzt um:
Höhe ≥ 0,85 der Seite oder Breite ≥ 0,92 → Umrandung, kein Inhaltskasten.

**Bestand: 96 Diagrammseiten (3,3 %) in 26 Dateien.** 14 Kontrollfälle
(Diagramm wie Prosa) alle korrekt.

### Lehre, dritte Runde

Zum dritten Mal war meine Annahme über das Material falsch — erst „Diagrammkästen
haben ungleiche Breiten", dann „vektorielle Seiten sind einspaltig". Beide Male
hat erst eine Seite aus dem echten Bestand es widerlegt, nicht ein Testfall. Die
Merkmale selbst waren jeweils richtig, die **Reichweite** war zu großzügig
angenommen.

---

## Nachtrag 2026-07-30 (8): Vier Fehler im Textlayer-Pfad

Vier Screenshots aus Obsidian, alle aus `schuldrecht-at-zusatzuebersichten`.
Gemeinsame Ursache bei dreien, und es ist keine Heuristik, sondern eine
Fehlannahme über den Textlayer: **eine gesetzte Zeile ist nicht eine `line`.**

Word setzt Gliederungsmarker an einen Tabulator. PyMuPDF bricht dort und liefert
zwei `line`-Einträge auf derselben Grundlinie:

```
x0= 70.9 x1= 82.3 y=145.5 | '1. '
x0=106.9 x1=246.3 y=145.7 | 'Die Abtretung als Verfügung '
```

### Was daraus wurde

| Screenshot | Ausgabe | Ursache |
|---|---|---|
| 4 (§§ 398ff) | „1." als eigener Absatz, Überschrift als nächster | Marker und Titel getrennt |
| 2 (§ 275 II) | „II." allein, dann Überschrift | dito |
| 1 (Annahmeverzug) | Punkte 1–4 zu **einem** Absatz verschmolzen | s.u. |
| 2 (§ 275 II) | Tofu-Kästchen statt „⇨" | Wingdings-PUA |
| 3 (Fahrlässigkeit) | „keine überzogenen **o** Anforderungen" | Courier-Bullet |

Der Listen-Fall ist der lehrreiche: `AUFZAEHLUNG` verlangte `\s` **nach** dem
Marker. Das getrennte Fragment ist aber `"2."` und wird beim Einlesen getrimmt —
kein Folgezeichen, kein Treffer, kein Absatzumbruch. Der Marker war da, die
Regel hat ihn nur nicht gesehen. `(?=\s|$)` statt `\s`.

### Die Reparatur

`fragmente_verschmelzen()` zieht Marker und Folgetext derselben Grundlinie
zusammen — **nur alleinstehende Marker**, nicht beliebige Fragmente. Eine
allgemeine Regel „gleiche Höhe → eine Zeile" würde auf zweispaltigen Seiten
links und rechts verkleben, und `spalten_trennen()` käme nie mehr zum Zug. Ein
Prosaabsatz kann nie ein alleinstehender Marker sein; damit ist die Regel von
der Spaltenfrage unabhängig.

Drei Dinge, die die Messung erzwungen hat:

1. **Reihenbildung nach Überlappung, nicht nach Oberkante.** Der Courier-Punkt
   sitzt 0,8 pt tiefer als der Times-Text daneben. Nach `y` sortiert steht er
   *hinter* seiner eigenen Zeile — und wanderte beim Verschmelzen an den Anfang
   der nächsten. Genau das zeigt Screenshot 3.
2. **Buchstabenmarker nur mit Klammer.** `[a-z]{1,3}[.)]` fängt im Blocksatz
   auch `aus.`, `bzw.`, `vgl.`, `gem.` — 1683 → 1450 Merges, und die 233
   entfallenen waren durchweg Blocksatz-Fragmente.
3. **Gedrehter Satz bleibt roh.** Auf `Klausur_2131_Zusatzmaterial` S. 6 läuft
   der Text senkrecht; alle Zeilen teilen sich ein `y`, die Reihenbildung warf
   die Seite in *eine* Reihe und klebte „II." an „I.". Zeilen mit
   `dir != (1,0)` gehen unverschmolzen durch. Zusätzlich Lücke ≤ 60 pt absolut.

### Sonderzeichen

441 Zeichen im Bestand zeigen in den Private-Use-Bereich (U+F000 + Fontcode) —
Obsidian rendert dort ein leeres Kästchen. Geschlossene Menge, Tabelle genügt:

| | | |
|---|---|---|
| Wingdings 0xF0 / 0xE0 | 270 / 14 | ⇨ |
| Wingdings 0xD8 | 111 | ➢ |
| Wingdings 0xFC | 30 | ✔ |
| Symbol 0xB7 | 9 | • |

Dazu der Courier-`o` der zweiten Word-Aufzählungsebene → `-`. Als Buchstabe
gelesen landete er mitten im Fließtext.

**Kontrolle:** 1450 Merges auf 1426 vektoriellen Seiten, 31 auffällig — alle
geprüft und korrekt (`a) Angebot`, `c) h.M.`, `29. April`).

---

## Nachtrag 2026-07-30 (9): `WuV_Verwaltungsrecht 2026` — gedrehter Doppelbogen

Vier Seiten, vollständig vektoriell, 0,0 s. Die erste Ausgabe war unbrauchbar:
Marker ohne Text, Fragen ohne Antworten. Drei Ursachen, alle im Layout.

### 1. `/Rotate 270`

Die Seite hat `rotation = 270`. `page.rect` zeigt die **gedrehte** Ansicht
(842 × 595), `get_text()` und `get_drawings()` liefern aber **ungedrehte**
Koordinaten (595 × 842) mit `dir = (0,1)` — der Text läuft senkrecht.

Damit bricht jede Annahme dieser Pipeline auf einmal: Zeilenreihenfolge,
Spaltensteg, Absatzabstand, Kastenerkennung. Und `page.rect` (gedreht) und die
Textboxen (ungedreht) stehen in **verschiedenen Systemen** — die Normierung auf
0–1000 war dadurch schon falsch, bevor irgendeine Heuristik lief.

`page.remove_rotation()` einmal beim Öffnen zieht alles in ein System. Betrifft
nur das Objekt im Speicher; `raw/` bleibt unberührt. Trifft auch die
Doppelseiten in `2131_`/`2135_Zusatzmaterial`, wo bisher `dir=(0,1)` galt.

### 2. Die Seitenzahl im Bund

Der Bogen ist eine Doppelseite: zwei logische Seiten nebeneinander. Der Steg
zwischen ihnen ist breit und eindeutig — trotzdem fand die Spaltenlogik ihn
nicht. Grund: die **Seitenzahl der linken Hälfte** steht mittig im Blatt und
erzeugt im x-Start-Histogramm einen eigenen Eintrag genau dort, wo der Steg
liegt. Die größte Lücke lag danach nicht mehr am Steg.

Kopf- und Fußzeilen zählen jetzt bei der Stegsuche nicht mit — sie gehören zu
keiner Spalte und fliegen ohnehin raus.

Dabei fiel auf: eine **blanke Seitenzahl war bisher gar keine Boilerplate**.
Die Hemmer-Skripte setzen sie auf 93 % Seitenhöhe, oberhalb der Fußzone (95 %),
in der die Textsignale greifen. Sie stand als eigener Absatz in jeder Ausgabe —
im Zusatzübersichten-`.md` zwölfmal. Eigene Zone (≤ 8 % / ≥ 92 %) nur für
reine Ziffernzeilen: **1069 Seitenzahlen im Bestand**, davon 222 vierstellig
und auch die korrekt (`2026` aus der Fußzeile, `2132` aus dem Kopf).

### 3. Vier Spalten, ein Schnitt

`spalten_trennen()` schnitt genau einmal. Der Bogen hat aber **vier** Spalten:
zwei logische Seiten à Frage- und Antwortspalte. Jetzt rekursiv, Tiefe 2.
Korpusweit ändert das die Reihenfolge auf **6 Seiten** — alles echte
Mehrspalter.

### Was bleibt

Die Ausgabe ist vollständig und in korrekter Lesereihenfolge, aber
**Frage und Antwort stehen getrennt**: erst alle Fragen einer logischen Seite,
dann alle Antworten. Das ist die richtige Spaltenreihenfolge und für Prosa das
Richtige — für einen Frage-Antwort-Bogen liest man aber quer, nicht runter.

Sauber wäre eine Tabelle `Frage | Antwort`. Dafür fehlt ein Rasterkriterium,
das den Bogen von zweispaltiger Prosa trennt, und die naheliegenden tragen
nicht:

| Kriterium | gemessen |
|---|---|
| waagerechte Leerstreifen über beide Spalten | WuV S. 1: 4 Streifen ≥ 8 pt bei 20 Zeilen — die Antwortblöcke überlappen die nächste Frage |
| Antwortzeile auf Höhe des Fragemarkers | WuV ~100 %, aber in Prosa-Zweispaltern zufällig ~35 % |

Offen und bewusst nicht geraten.

### Nachzug: Merkmal 3 war zu weit gefasst

S. 4 des Bogens wurde als Diagramm erkannt und durch ein Bild ersetzt — falsch.
Ursache: die hinterlegten **Kopfstreifen** der Abschnitte stehen paarweise
nebeneinander (Frage-/Antwortspalte) und erfüllten damit Merkmal 3. Sie tragen
aber je *eine* Zeile Überschrift, keinen Aufbau.

Merkmal 3 zählt jetzt nur noch Kästen mit **mindestens zwei Textzeilen** —
dieselbe Schwelle, die für die Absatztrennung ohnehin schon galt. Die
Übersichtsseiten, für die das Merkmal gebaut wurde, tragen mehrzeilige Kästen
und bleiben erkannt. 11 Kontrollfälle korrekt.

**Prüfung der Ausgabe:** 22 509 von 22 525 Zeichen des Textlayers stehen im
`.md` (99,9 %). Die Differenz sind die 8 laufenden Fußzeilen.

### Nebenbefund: zwei Läufe gleichzeitig zerstören sich die Kacheln

`BGB AT Fall 12` brach mit `FileNotFoundError: _seite003_L.png` ab, während in
einem zweiten Terminal ein anderer Lauf fertig wurde. Ursache: die
Zwischenbilder gingen für **alle** Läufe nach `.ocr-bench/out-C/`, und das
Aufräumen am Ende greift per Glob `_seite*.png` auf das ganze Verzeichnis zu —
der schnellere Lauf löscht dem langsameren die Kacheln unter den Händen weg.

Jetzt bekommt jeder Lauf `out-C/_tmp-<pdfname>/` und räumt nur dort auf.

### Rest, bewusst offen

Auf reinen Scan-Dokumenten (`Strafrecht AT VI`, 13 von 13 Seiten OCR) bleiben
**4 Seitenzahlen** stehen. Dort kommen die Koordinaten aus dem Grounding des
Modells, nicht aus dem Satz. Die Zonenschwelle darauf abzustimmen, ohne die
tatsächlichen y-Werte gemessen zu haben, hieße raten — und die Kosten eines
Fehlers sind hier asymmetrisch: eine überstehende Seitenzahl ist Kosmetik,
verschluckter Inhalt ist ohne das Original unauffindbar.

---

## Nachtrag 2026-07-30 (10): Frage-Antwort-Raster als Tabelle

Der WuV-Bogen wird jetzt als Markdown-Tabelle gerendert. Die offene Frage aus
Nachtrag 9 war nicht *ob*, sondern **woran** man ein Raster von zweispaltiger
Prosa unterscheidet — beide sind zwei volle Spalten mit demselben Zeilenraster.

### Das Merkmal

Nicht Leerstreifen (zu wenige, gemessen in Nachtrag 9), nicht die reine
y-Koinzidenz von Zeilen (in Prosa identisch, weil beide Spalten denselben
Durchschuss haben), sondern:

> **Fängt jeder Absatz der rechten Spalte auf derselben Höhe an wie ein
> Zeilenanfang der linken?**

„Zeilenanfang" heißt Gliederungsmarker oder fette Zeile — dieselbe Prüfung, die
`zusammenfuegen()` für Absätze benutzt. Die Absätze der rechten Spalte kommen
aus Leerraum-Gruppierung.

| | passende Absätze |
|---|---|
| WuV S. 1–4, beide Hälften | 13/13 · 10/11 · 7/7 · 12/12 · 10/10 · 4/5 · 13/14 · 3/3 |
| `2138-loesung` S. 10 | 0/5 |
| `bereicherungsrecht-zusatzfall` S. 12 | 0/3 |
| `2135_Lösung` S. 5 | 1/8 |

≥ 80 % gegen ≤ 13 %. Schwelle: mindestens 3 Treffer **und** ≥ 75 %.

Wichtig ist der Zeitpunkt: geprüft wird erst, wenn **keine** der beiden Spalten
sich weiter teilt. Vorher wäre „links" beim Doppelbogen noch eine ganze logische
Seite, und der Test liefe auf dem falschen Paar.

### Warum die Blockbildung links nicht taugt

Erster Versuch: Blöcke aus Leerraum auf **beiden** Seiten. Ergebnis 6/13 — zu
schwach. Grund: aufeinanderfolgende Fragen haben keinen Leerraum zwischen sich,
ein „Block" verschluckte bis zu vier Fragen. Links müssen die **Marker** die
Zeilen schneiden, nicht der Leerraum. Rechts umgekehrt: dort gibt es keine
Marker, nur Absätze.

### Ausgabe

- Zeilen = Markerzeilen der linken Spalte; die rechte Spalte wird über
  y-Intervalle zugeordnet
- Abschnittsüberschriften ohne Antwort („Fall 1", „Prozessuales") schließen die
  Tabelle, stehen als Absatz und eröffnen die nächste — Markdown kann keine
  Zelle über beide Spalten ziehen
- Trennstriche werden in der Zelle aufgelöst („Behördenei- genschaft" →
  „Behördeneigenschaft"); die Absatzlogik läuft dort nicht mehr
- Spaltentitel `Frage | Antwort` nur, wenn ≥ 50 % der linken Zellen mit „?"
  enden. `schuldrecht-at-zusatzuebersichten` S. 8 trägt dasselbe Raster mit
  Begriff/Erläuterung — dort bleibt die Kopfzeile leer

### Kontrolle

Korpusweit greift das Raster auf **9 von 1426** vektoriellen Seiten: die 8
Hälften des WuV-Bogens und die eine Übersichtsseite. Die WuV-Ausgabe enthält
**33 Tabellen mit 136 Zeilen**, und keine Zeile des Textlayers über 40 Zeichen
fehlt.

---

## Nachtrag 2026-07-30 (11): Gliederungsebenen im Gutachten

Rückmeldung an `BGB AT Fall 12` (Scan, Hemmer-Lösung): Überschriften kleben am
Fließtext (`3. Anspruch aus § 816 I S. 2 BGB Grundsätzlich ergibt sich …`), und
die Ebenen `a)` / `aa)` treten nicht hervor. Beides trifft die Mehrzahl der
Scans, weil das fast alles Gutachten sind.

### Was der Satz hergibt

Hemmer setzt die Gliederung im **hängenden Einzug**: der Marker steht links
außerhalb, der Text des Punktes ist eingerückt (gemessen `BGB AT Fall 12` S. 4:
Marker x = 20, Fließtext x = 98). Der Fließtext läuft im **Blocksatz**.

Damit ist die kurze Zeile der Beweis für das Ende einer Einheit — und das
einzige Signal, das die Überschrift von ihrem eigenen Fließtext trennt, denn
Abstand steht zwischen beiden nicht.

### Warum die kurze Zeile allein nicht reicht

Über alle Blocksatzseiten des Bestandes, Fehlschnitt-Proxy „Folgezeile beginnt
klein":

| Füllgrad der Zeile | Folgezeile klein |
|---|---|
| 0,00–0,95 (jede Stufe) | 10–17 % |
| 1,00 (volle Zeile) | 53 % |

Der Sprung ist eindeutig, 10–17 % Fehlschnitte sind für eine allgemeine
Absatzregel aber zu viel. Die Regel gilt deshalb **nur für Absätze, die mit
einem Gliederungsmarker beginnen und noch als Überschrift durchgehen** (≤ 90
Zeichen). Dort ist der Schaden begrenzt und das Signal eindeutig.

Zusätzlich: eine klein beginnende Folgezeile hebt den Schnitt auf.

### Rechter Rand aus der Nachbarschaft

Der Satzspiegel wird aus ±15 Zeilen bestimmt, nicht aus der Seite. Spalten und
OCR-Kacheln haben je eigene Ränder — auf `BGB AT Fall 12` S. 3 stehen 876 und
957 nebeneinander. Ein gemeinsamer Rand erklärt die schmalere Spalte
vollständig zu Kurzzeilen.

### Ohne Koordinaten

Das Modell liefert das Grounding nicht immer: 1 von 13 Kacheln kam ohne
`<|LOC|>` zurück (S. 5, rechte Spalte). Ersatzmaß ist die Zeichenzahl. Gemessen
auf genau dieser Spalte: Absatzenden ≤ 0,93 der Medianlänge, Fließtext ≥ 0,98 —
Schwelle 0,95. Nur als Notbehelf: das Maß steuert allein die Gliederungsregel,
nicht die Absatzlogik insgesamt.

### Ausgabe

| Fall | Ausgabe |
|---|---|
| kurzer Markerabsatz ohne Satzschlusszeichen | echte Überschrift `##`…`######` |
| dito mit Punkt, aber fett gesetzt | ebenfalls Überschrift |
| Marker + Fließtext, Buchstabe/Klammer | `**a)** …` |
| Marker + Fließtext, Zahl | unverändert (Markdown rendert `1.` schon als Liste) |

Grade nach der üblichen Ordnung A. → `##`, I. → `###`, 1. → `####`, a) →
`#####`, aa)/(1) → `######`. `#` bleibt frei.

Der Punkt am Ende trennt Überschrift von Prosa: `cc) Diese Ansicht ist mit der
h.M. abzulehnen.` bleibt Absatz, `3. Anspruch aus § 816 I S. 2 BGB` wird
Überschrift. Fettschrift sticht den Punkt aus, weil Hemmer auch Überschriften
mit Punkt fett setzt.

### Drei Fallen, die die Messung gezeigt hat

1. **`ff.` ist keine Ebene, sondern „folgende".** 15 solcher Stellen im Bestand
   gegen 8 echte `f)`/`ff)`. Die Punktform von `f`/`ff` entfällt, die
   Klammerform bleibt. Ebenso `h. L.`, `d. h.`, `a. A.`: steht hinter dem
   Marker gleich die nächste Abkürzung, war es keine Gliederung.
2. **Fett heißt nicht Überschrift.** `5. Wertersatz, § 818 II BGB (Geld ist
   nicht` / `**mehr identifizierbar vorhanden)**` — die fette Zeile ist die
   Fortsetzung. Drei Vetos: volle Vorzeile im Blocksatz, Fortsetzungseinzug,
   Komma oder Trennstrich am Ende.
3. **Rückwärtssprung in y.** Beim Spalten- und Kachelwechsel springt der Text
   an den Kopf zurück. Ein negativer Abstand ist nie größer als `normal * 1.6`,
   der Wechsel blieb also unbemerkt — der Fußnotenblock der einen Spalte
   verschmolz mit dem Fließtext der nächsten.

### Fußnotennummer am Seitenfuß

Der Satz rückt die Nummer der Definition nach links aus; als eigene Zeile ist
sie danach eine bloße Zahl, die die Boilerplate-Regel als Seitenzahl wegwirft.
Der Definition fehlte anschließend die Nummer, und die Verweise blieben als
nackte Ziffer am Wort kleben (`abzulehnen.9`). Nummer und Text werden jetzt
verbunden, wenn die Zahl links unten steht und der Text auf derselben Höhe
beginnt. Die hochgestellten Verweiszeichen im Fließtext sind nicht betroffen:
die stehen am **rechten** Rand (gemessen x = 977 gegen x = 8…53).

Dazu: gleiche Fußnotennummer zweimal auf einer Seite hängt jetzt an, statt zu
überschreiben. Auf Doppelseiten stehen zwei Fußnotenblöcke nebeneinander — das
Überschreiben löschte den ersten Text ersatzlos.

### Kontrolle

1426 vektorielle Seiten, alt gegen neu:

- **3641 Überschriften** (H2: 254, H3: 1038, H4: 1288, H5: 705, H6: 356) und
  872 fett ausgezeichnete Marker
- Absätze 20041 → 19618 (−2,1 %); 432 neu getrennt, der Rest netto verschmolzen
- Zeichenfolge identisch bis auf **176 Seiten**, davon 172 mit **mehr** Text
  (die geretteten Fußnotennummern). Vier Seiten verlieren 1 Zeichen: zwei
  Klausurlösungen, deren Fußnotenblöcke schon vorher spaltenweise ineinander
  liefen
- Stichprobe von 30 Überschriften geprüft; die drei abgeschnittenen darin sind
  **Altbestand** — dieselben Absätze brachen vorher an derselben Stelle, sie
  fielen als Fließtext nur nicht auf

`BGB AT Fall 12` hat danach 25 Überschriften, 11 fett ausgezeichnete Marker und
7 Fußnotendefinitionen mit 10 Verweisen im Text. Die Ziffern 14–17
bleiben nackt: auf S. 6 und S. 7 steht im Original kein Fußnotenblock (im
Textlayer der PDF ebenfalls nicht) — ohne Definition wird aus einer Ziffer
kein `[^n]`.

### Werkzeug

`--zeilen-dump <datei.json>` schreibt die Zeilen samt Boxen je Seite. Ohne das
kostet jede Änderung an der Absatzlogik einen vollen OCR-Lauf (236 s für
`BGB AT Fall 12`).

---

## Nachtrag 2026-07-30 (12): falscher Längsschnitt auf Scanseiten

Rückmeldung an `Strafrecht AT VI` S. 3: der Text ist zerhackt, Zeilenhälften
stehen als eigene Absätze („Neben den aus dem Vorsatzdelikten bekannten hier-
wie beim unechten Unterlassungsdelikte Verhaltens zu denken."). Die Seite ist
einspaltig und wurde trotzdem senkrecht in der Mitte zerschnitten.

### Der Fehler in der Entscheidung

`layout_erkennen` fragte den Kantenanteil an einer **angenommenen** Stegposition
von 0,5 ab, wenn das Tintenprofil gar keinen Steg fand. Der Kantenanteil darf
eine Stegposition aber nur bestätigen, nie erfinden: einspaltige Seiten mit
breitem rechtem Rand erreichen dort 0,31–0,77, weil ihre Zeilen genau in dem
Band enden, das der Test abfragt. 125 Seiten des Bestandes wurden so an einer
Stelle zerschnitten, die im Bild nichts markiert.

### Warum das Profil den echten Steg verfehlte

Drei Ursachen, alle im Profil sichtbar:

1. **Referenz war der Median.** Bei `Verwaltungsprozessrecht` S. 54 steht der
   Satz in zwei Dritteln der Blattbreite; der Median beschreibt dann den leeren
   Rand, der Satzspiegel ragt als 6-faches heraus, und jedes Rauschen im Rand
   fällt unter die Schwelle. Jetzt das 60-%-Quantil.
2. **Kein Flankentest.** Ein Steg ist ein Tal *mit Tinte auf beiden Seiten*.
   Ohne diese Bedingung zählt der rechte Rand als Steg — ein Tal, das nie
   wiederkommt.
3. **Ein Pixel Rauschen zerteilte das Tal.** Auf `strafrecht-fall-02` S. 19
   (echter Zweispalter) lagen zwei Hälften 0,467–0,501 und 0,504–0,529
   nebeneinander, und jede sah die andere als schlechte Flanke. Nahe Täler
   werden jetzt verschmolzen (≤ 1 % der Blattbreite).

Dazu eine Mindestbreite von 1,5 %: auf schmutzigen Scans sinkt das Profil
vereinzelt für ein einziges Pixel ab — `Strafrecht AT VI` S. 3 hat drei solcher
Dips bei 0,60, 0,65 und 0,70.

### Schwellen aus 14 per Auge belegten Seiten

Rasterlauf über Quantil × Talschwelle × Flanke × Mindestbreite, geprüft gegen
7 sichere Zweispalter und 7 sichere Einspalter:

| | |
|---|---|
| Quantil | 60 |
| Tal | < 0,35 × Referenz |
| Flanke | ≥ 0,35 × Referenz auf **beiden** Seiten |
| Mindestbreite | 1,5 % der Blattbreite |
| Lückenschluss | 1 % der Blattbreite |

13 von 14 richtig. Der eine Fehler ist `Verwaltungsrecht AT Fall 8` S. 10 —
echter Zweispalter mit flachem Steg (Tal 0,40, linke Flanke 0,72), der jetzt
als einspaltig durchgeht.

### Warum die Schieflage gewollt ist

Die Kosten sind asymmetrisch. Ein **falscher** Schnitt zerlegt jede Zeile der
Seite in zwei Hälften — unlesbar, und ohne das Original nicht zu rekonstruieren.
Ein **versäumter** Schnitt schickt die Seite ganz durch das Modell, das
Spaltenlayout selbst versteht; die Reihenfolge kann leiden, der Text bleibt.

### Kontrolle

1496 Scanseiten: **132 wechseln von zweispaltig auf einspaltig**, keine in die
Gegenrichtung (vorher 1137 zweispaltig, jetzt 1005). Stichprobe von 6 zufällig
gezogenen Wechseln im Bild geprüft: 5 eindeutig einspaltig, 1 gemischtes Layout
(Textspalte oben, Anzeige unten) — dort ist der ganze Durchlauf ebenfalls
richtig.

---

## Nachtrag 2026-07-31 (13): Fehlerbericht abgearbeitet

Sieben gemeldete Befunde, davon zwei bereits durch Nachtrag 11/12 erledigt
(Lesereihenfolge `Strafrecht AT VI` S. 3; Trennstriche in den WuV-Zellen).
Die übrigen fünf, jeweils mit der Ursache:

### Kopf- und Fusszeile in Tabellenzellen (WuV)

`frage_antwort_raster` baute die Zellen aus den Rohzeilen — der Boilerplate-Test
läuft aber in `zusammenfuegen`, und das sieht die fertige Tabelle nur noch als
einen Block. `RA Dr. Michael Hein, M.A., LL.M. - 04/2026` stand damit mitten in
den Antworten. Jetzt wird vor der Rasterprüfung gefiltert: **0 statt 5** solcher
Zellen, die 33 Tabellen bleiben unverändert.

### Städteliste des Briefkopfs (BGB AT Fall 3)

Der Briefkopf trägt die Standorte fünfzeilig am rechten Blattrand. Die oberen
drei Zeilen liegen in der Kopfzone und flogen raus, die unteren beiden nicht
(gemessen y = 79 und 91 gegen `kopf` = 70). Einzeln greift auch die
Städte-Regel nicht, die zwei Ortsnamen in einer Zeile verlangt — nach dem
Kachelschnitt steht dort nur noch `Mainz - Man`.

Neue, streng verankerte Regel: Ortsname am **Zeilenanfang**, dahinter höchstens
ein angeschnittenes Wort. Trifft `Mainz - Man`, `Passau - Reg`, `Hamburg - H`;
trifft **nicht** `OLG München - Urteil vom …` oder `München - 5 U 123/20`.
Im gesamten vektoriellen Bestand **null** Treffer — die Regel wirkt nur dort,
wo das Problem sitzt.

### Fussnoten-Mapping

Drei unabhängige Ursachen:

1. **Feste x-Grenze.** `fussnotennummern_anbinden` verlangte x < 500. Auf
   zweispaltigen Seiten beginnt der rechte Fussnotenblock aber bei 523 — 151
   Nummern blieben unverbunden. Jetzt zählt der hängende Einzug: die Nummer
   beginnt links von ihrem Text. Das trennt sie weiterhin von den
   hochgestellten Verweiszeichen (x = 977 vor Text bei x = 98).
2. **Definition beginnt mit Klammer.** `4 (= Wiederbeschaffungswert abzüglich
   Restwert)` erfüllte das Anfangsmuster nicht und blieb im Text der
   vorangehenden Fussnote stecken; der Verweis fand keine Definition.
3. **Ziffer am Fettmarker.** `**Wiederbeschaffungsaufwand****4**` — die
   Zusammenführung benachbarter Fettläufe verlangte Leerraum dazwischen.

Ergebnis auf `BGB AT Fall 12`: 8 Definitionen, Verweise `[^1]`–`[^9]`, `[^12]`,
`[^13]`. Nackt bleiben **2 Ziffern** (11, 15) — zu ihnen steht im Original kein
Definitionstext.

### Seitenzahlen auf Scanseiten — die nachgeholte Messung

Gemessen an `Strafrecht AT VI`: die Seitenzahl liegt bei **y = 911…938**, die
Regel griff ab 920. Vier von zwölf blieben deshalb stehen. Schwelle jetzt 905.

Der Preis war zu prüfen, denn zwischen 905 und 920 stehen auch
Fussnotennummern. Nach der Reparatur von `fussnotennummern_anbinden` bleiben
dort im ganzen Bestand **54 nackte Zahlen — ausnahmslos Seitenzahlen**
(fortlaufend eine je Seite, konstante x-Position). In der Ausgabe von
`Strafrecht AT VI`: **0 statt 4**.

### Markdown nach Zeilenumbruch

- **Trennstrich vor fetter Fortsetzung.** `Berei-` / `**cherungsrecht, Rn.
  395)**` — der Trennstrich-Test verlangte einen Kleinbuchstaben als erstes
  Zeichen und sah stattdessen `*`. Die Fetterkennung arbeitet zeilenweise und
  setzt die Auszeichnung mitten ins Wort.
- **Ungerade Zahl von `**`.** Neuer Ausgleich am Absatzende — sonst färbt
  Obsidian den Rest des Absatzes, und der Fehler ist erst im Rendern sichtbar.
  Im Bestand jetzt **null** unbalancierte Absätze.
- **Überschrift über zwei Zeilen.** `**IV. Exkurs: … – V**` / `**ZR 67/22**`
  wurde getrennt, weil die 90-Zeichen-Grenze für Überschriften griff (die
  vollständige hat 94). Die Grenze auf 110 zu heben brächte 133 weitere
  Überschriften, darunter Inhaltsverzeichniszeilen mit Füllpunkten — verworfen.
  Stattdessen die Ausnahme „Absatz besteht ausschliesslich aus fetten Läufen":
  **57 lange Überschriften** kommen dazu, in der Stichprobe 10 von 12 echt.
- **Pfeile als LaTeX.** Ein Muster je Befehl statt einer Liste je Schreibweise;
  `$\Leftrightarrow$` war der letzte Rest im Bestand.

### Kontrolle

1426 vektorielle Seiten gegen den Stand vor dieser Sitzung: 254 Seiten mit
abweichender Zeichenfolge, davon **251 mit mehr Text**. Drei verlieren 1–2
Zeichen, alle in denselben spaltenweise verschränkten Fussnotenblöcken zweier
Klausurlösungen, die schon vorher ineinanderliefen.

### Offen

`Strafrecht AT VI` S. 8 (Garanten-Diagramm) wird **nicht** als Diagrammseite
erkannt — es ist kein Bild eingebettet, und der linearisierte Text verliert die
räumliche Zuordnung. Behelf bis auf Weiteres: `--diagramm-seiten 8`.

---

## Nachtrag 2026-08-05 (14): Benchmark des OCR-Pfades

`bench_ocr.py`. Vektorielle Seiten tragen ihren Text exakt im PDF — dieselbe
Seite läuft zweimal durch dieselbe Pipeline: einmal über den Textlayer
(= Wahrheit), einmal mit `--nur-ocr` erzwungen durchs Modell. Gemessen wird
damit der **ganze** OCR-Pfad einschließlich Kachelung, Spaltenerkennung und
Zusammenbau; ein Vergleich gegen rohen PDF-Text hätte genau das ausgeblendet.

40 Seiten aus 40 verschiedenen Dateien, 1500–6000 Zeichen, keine Diagramme,
in einem Sammel-PDF (beide Läufe sehen damit dieselben laufenden Kopfzeilen).
17 401 Wörter, 225 Normzitate, 21 min auf dem M1 Air.

### Drei Maße, weil sie verschiedene Entscheidungen tragen

| | |
|---|---|
| Wortgenauigkeit | Multimengen-Vergleich, reihenfolgeunabhängig — der Anteil, den ein Reparaturlauf angehen könnte |
| Reihenfolge | Sequenzähnlichkeit derselben Wörter — fällt sie ab, ist es Layout, und dagegen hilft kein Sprachmodell |
| Zitattreue | Anteil der Normzitate, die unverändert wiederkommen — Messlatte für jeden späteren Eingriff |

Das Zitatmuster verlangt beim Gesetzesnamen **zwei Großbuchstaben** (BGB, StGB,
SPolG). Ohne das zog es das nächste beliebige Wort mit hinein (`§ 12 Eser`), und
jede Abweichung dort zählte fälschlich als verlorenes Zitat.

### Ergebnis

| | alle 40 | ohne die 6 defekten |
|---|---|---|
| Wortgenauigkeit | 93,3 % | **98,5 %** |
| Zitattreue | 87,6 % | **93,3 %** |
| Reihenfolge (Median) | 97,4 % | 97,8 % |

Median-Seite: 99,1 % Wortgenauigkeit, 100 % Zitattreue. 35 von 40 Seiten liegen
über 95 %.

### Der eigentliche Befund: entgleiste Generierung

Die Aggregatzahlen werden von **6 Seiten (15 %)** getragen, deren Ausgabelänge
grob falsch ist — und zwar nicht durch Lesefehler, sondern durch **Schleifen und
Abbrüche des Modells**:

| Seite | Länge zur Wahrheit | häufigstes 5-Gramm |
|---|---|---|
| `2131_Lösung` S. 4 | 347 % | `V. V. V. V. V.` — **1920×** |
| `Unirep_FK_SR_LH_18_02` S. 14 | 658 % | `Haft, BT1, S. 58; Jescheck/Weigend,` — 106× |
| `Klausur_2140_Lösung` S. 6 | 406 % | `Vgl. die Nachweise in BGHZ` — 64× |
| `UNIREP_KK_ZR_LH_07_11` S. 8 | 352 % | aufsteigender Zähler `(1982) (1983) …` |
| `Klausur_2136` S. 2 | 24 % | Abbruch |
| `2143_SV` S. 2 | 74 % | Abbruch |

Auf den 34 übrigen Seiten verliert der Pfad **12 von 179 Zitaten**, davon 8 auf
einer einzigen Seite mit gestörter Lesereihenfolge (`Klausur_2137` S. 7,
Reihenfolge 47,5 %). Ohne die bleiben **4 verlorene Zitate**.

### Was daraus für den LLM-Reparaturlauf folgt

Er zielt am Problem vorbei.

- Auf den 85 % funktionierenden Seiten steht der Pfad bei **99 % Wortgenauigkeit**.
  Was ein Sprachmodell dort noch holt, ist Politur — gegen das Risiko, ein
  korrektes Normzitat zu „verbessern".
- Auf den 15 % defekten Seiten **kann** es nicht helfen: der Inhalt ist nicht
  da (Abbruch) oder durch eine Schleife ersetzt. Es müsste erfinden.

Der Hebel liegt lokal und ist billig: **Entgleisungen erkennen und die Seite neu
rechnen.** Zwei Signale, beide ohne Modell:

1. Ausgabelänge gegen die Zeichenzahl des vorhandenen Textlayers — die Pipeline
   kennt sie bereits und druckt sie je Seite.
2. Wiederholte n-Gramme in der Ausgabe — bei allen vier Schleifen eindeutig
   (1920×, 106×, 64×).

Erst danach lohnt die Frage nach einem Reparaturlauf, und dann gegen diese
Basiswerte gemessen.

---

## Nachtrag 2026-08-05 (15): Entgleisungen abgefangen, Spaltensteg repariert

Umsetzung der drei Punkte aus Nachtrag 14. Alle Zahlen unten sind gemessen,
nicht geschätzt; die Testskripte liegen als `regress_steg.py`,
`regress_randlabel.py` und `bench_defekt.py` daneben.

### 1. Entgleiste Generierung: erkennen, neu rechnen, kürzen

Drei Bausteine in `pdf2md.py`, alle ohne zweites Modell:

**`entgleist(text, erwartet, geeicht)`** — zwei Signale. `schleifenlaenge()`
zählt das häufigste Wort-5-Gramm, **ziffernblind** (`\d+` → `#`). Ohne die
Ziffernblindheit entgeht die häufigste Bauform überhaupt: `ZR_LH_07_11` S. 8
lieferte `(1982) (1983) (1984) …` über 2000 Zeichen, wörtlich gezählt ist dort
jedes 5-Gramm einmalig — der Zähler ist eine Schleife, die sich als Fortschritt
tarnt. Ziffernblind kommt dasselbe 5-Gramm 275-mal. Gegenprobe über alle 40
Seiten: **keine zweite Seite kommt über 6**, die entgleisten auf 64, 106, 275
und 1920. Die Schwelle 8 liegt mitten in einer sehr breiten Lücke. Das zweite
Signal ist die Ausgabelänge gegen eine Erwartung.

Die Erwartung stammt aus der **Tintenmenge der Kachel**, nicht aus einer
Aufteilung der Seitenerwartung — Kacheln tragen unterschiedlich viel Text. Der
Umrechnungsfaktor kommt aus dem Textlayer derselben Seite, wenn es einen gibt
(dann ist er exakt), sonst aus dem Korpusmittel von 18,8 Zeichen je 1000
Tintenpixel bei 150 dpi. Weil diese beiden Fälle sehr verschieden genau sind,
gibt es zwei Korridore: `(0,80 – 2,2)` geeicht, `(0,45 – 2,6)` grob. Der grobe
muss die eigene Streuung des Korpusmittels (0,56–1,58) enthalten, sonst schlägt
er auf jeder zweiten Scanseite an.

Am Bestand geprüft, Seitenebene, gegen die 40 Benchmarkseiten:
**6 von 6 entgleisten Seiten erkannt, 0 Fehlalarme auf den 34 gesunden.**

**`kachel_zeilen()`** — bei Verdacht wird die Kachel waagerecht halbiert und
neu gerechnet, die Teilkoordinaten werden zurück ins Elternbild gerechnet.
Welche Fassung gewinnt, entscheidet `_guete()`: Schleife ist ein harter Malus,
sonst zählt der Abstand zur Erwartung. Damit kann der Neuversuch nichts
verschlimmern — liefert er weniger, wird er verworfen. Genau das ist bei
`Klausur_2136` S. 2 passiert (Neuversuch 26649 Z., verworfen).

**`schleife_kuerzen()`** — bleibt eine Schleife nach dem Neuversuch stehen, ist
die Seite nicht zu retten, aber sie muss lesbar bleiben. Wiederholungen werden
auf zwei Vorkommen gekürzt, innerhalb der Zeile und über Zeilen hinweg.
Absichtlich zwei und nicht eins: die Stelle soll im Text sichtbar bleiben.
Stilles Glätten wäre derselbe Fehler wie stilles Löschen.

Der Zähler braucht auch hier einen eigenen, ziffernblinden Durchgang — und der
läuft mit einer **viel** höheren Schwelle (`ZAEHLER_AB = 20` statt 3), weil
ziffernblind auch `§§ 823, 826, 831, 840` wie eine Wiederholung aussieht. Eine
Normenkette von vier Paragrafen soll keine werden; ein Zähler läuft in die
Hunderte, eine Normenkette nicht über ein Dutzend. Stehen bleiben dabei die
ersten zwei **echten** Vorkommen (`(1982) (1983)`), nicht zwei Kopien des
ersten — die erfundene Spur wäre schlechter als gar keine.

Beide Wege haben Gegenproben in `test_schleife.py`: Fließtext, Fußnotenblock
und Normenkette müssen unverändert durchlaufen.

### 2. Token-Budget aus der Tintenmenge

Beim Messen fiel auf, dass die entgleisten Seiten auch die teuersten sind: eine
Schleife hört von selbst nicht auf, sie läuft bis zum festen Deckel von 8192
Token — gemessen ~8 min für **eine** Kachel auf dem M1. Eine volle A4-Seite
Gutachten braucht aber nur rund 1700 Token.

`_tokenbudget()` leitet den Deckel jetzt aus derselben Tintenschätzung ab
(Zeichen ÷ 2,2 × 1,8 Reserve, gedeckelt auf 1024–8192). Ist er doch zu knapp,
sieht `entgleist` einen Abbruch und die Kachel wird feiner geschnitten neu
gerechnet — der Fehler heilt sich also selbst, während ein zu hohes Budget nur
Zeit verbrennt.

**Ein Nebeneffekt, der Arbeit gemacht hat:** das Budget schneidet eine Schleife
mitten im Lauf ab, und damit verschwindet auch das Längensignal. `ZR_LH_07_11`
S. 8 lief vorher bis 8249 Zeichen (341 % der Erwartung → sicher erkannt), mit
Budget endet derselbe Zähler bei 2033 Zeichen — 84 % der Erwartung, also mitten
im Normalbereich. Das Budget spart Zeit und macht den Längendetektor an genau
der Stelle blind, an der er vorher zog. Deshalb ist die ziffernblinde
5-Gramm-Zählung nicht Kür, sondern die Bedingung dafür, dass das Budget
überhaupt gefahrlos gesetzt werden darf.

### 3. Die sechs kaputten Seiten, vorher/nachher

`bench_defekt.py`, Wahrheit aus dem Textlayer wie im großen Benchmark:

| Seite | Wort | Zitate | Zeichen |
|---|---|---|---|
| `2131_Lösung` S. 4 | 62,1 % → **88,3 %** | 43 % → 64 % | 11362 → 13151 |
| `2143_SV` S. 2 (Abbruch) | 72,2 % → **98,9 %** | 67 % → 67 % | 2159 → 3065 |
| `ZR_LH_07_11` S. 8 (Zähler) | 0,9 % → **90,3 %** | 0 % → **100 %** | 8249 → 2276 |
| `FK_SR` S. 14 | 99,0 % → 100 % | 100 % → 100 % | 12608 → **2151** |
| `Klausur_2136` S. 2 | 23,2 % → 23,2 % | — | unverändert |
| `2140_Lösung` S. 6 | 92,3 % → 92,4 % | 100 % → 100 % | 14395 → **4438** |

**63,1 % → 85,0 % Wortgenauigkeit, 65,2 % → 87,0 % Zitattreue.** Fünf von sechs
repariert. `Klausur_2136` S. 2 bleibt offen — dort liefert auch der Neuversuch
keinen brauchbaren Lauf.

### 4. Spaltensteg: jede Lücke prüfen, nicht nur die größte

Der Befund aus Nachtrag 14, dass `Klausur_2137` S. 7 die Lesereihenfolge
verliert (47,5 %, acht der zwölf verlorenen Zitate auf dieser einen Seite),
hatte eine Ursache im **Textlayer-Pfad**, nicht im OCR. `_steg()` nahm die
größte Lücke im x-Start-Histogramm als Kandidaten. Gemessen auf dieser Seite:

| Position | Lücke | Anteil | Kreuzer |
|---|---|---|---|
| 505 (echter Steg) | 103 | 0,49 | **1** |
| 817 (gewählt) | 122 | 0,01 | **49** |

Ein halbes Dutzend eingerückter Einzelzeilen zwischen den Spalten zerlegt den
echten Steg in kleine Sprünge, während am rechten Rand eine breitere Lücke ohne
jede Bedeutung stehenbleibt. `_steg()` bewertet jetzt **alle** Lücken und
rangiert nach wenigen Kreuzern zuerst, Breite danach — der Kreuzer ist das
stärkere Signal, denn eine breite Lücke, durch die Zeilen laufen, ist kein Steg.

Regression über alle 1426 vektoriellen Seiten (`regress_steg.py`):

| | |
|---|---|
| unverändert | 1361 |
| nur umsortiert | 11 |
| **Spalten korrekt, Trennstriche aufgelöst** | **50** |
| Zeichenverlust | 4 Seiten (−2, −2, −1, −1) |

Die vier Verluste sind einzelne Ziffern in Fußnotenblöcken, die vorher ohnehin
verschränkt waren; auf `2131_Lösung` S. 7 gewinnt die neue Fassung dafür die
Fußnoten 30–32 zurück, die vorher komplett im Fließtext verschwanden.

> **Zur Messmethode:** der Wortvergleich täuscht hier. Sind die Spalten richtig
> getrennt, werden die Trennstriche aufgelöst — „Allge" + „meininteresses"
> verschwinden zugunsten von „Allgemeininteresses". Wortweise sieht das nach
> zwei verlorenen und einem gewonnenen Wort aus, obwohl kein Buchstabe fehlt.
> Gemessen wird deshalb auf **Zeichenebene**, und dort muss auch die
> Markdown-Auszeichnung heraus: ein geänderter Überschriftgrad sah im ersten
> Lauf nach „6 Zeichen verloren" aus, es waren `#`.

### 5. Randmarken vor ihren Block

Hemmer setzt „Beispiel:", „Anmerkung:" & Co. in den linken Rand, senkrecht
**mittig** zu dem Block, den sie beschriften. Nach y sortiert landet die Marke
damit mitten im Satz — im Bestand: „stieß dabei aus Unachtsamkeit einen
**Beispiel:** Blumentopf herunter".

`randlabel_vorziehen()` erkennt sie am hängenden Einzug gegen die **lokale**
Nachbarschaft (Spalten haben je eigene Einzüge) und läuft rückwärts bis zum
Blockanfang; an einer Absatzlücke, einem Gliederungsmarker oder einer weiteren
Randzeile ist Schluss.

Regression: 2 Seiten geändert, **0 Zeichenverlust**. Beide Änderungen sind
Verbesserungen — `**Beachte:**` stand vorher hinter dem Absatz, den es
beschriftet.

> **Zum Testrahmen:** der erste Anlauf baute alle 1426 Seiten zweimal komplett
> neu auf und brauchte 25 min — für eine Regel, die im ganzen Bestand
> 39 Zeilen anfasst. Der Aufwand steckte in PyMuPDFs Tabellensuche
> (~0,5 s je Seite und Aufbau), die mit der Frage nichts zu tun hat. Mit
> Vorfilter direkt auf `get_text("text")`: **3:25 min.**

#### Der zweite Fall derselben Marke: sie ist keine Überschrift

Die Gegenprobe auf einer echten Scanseite hat einen zweiten Weg gezeigt, auf dem
dieselbe Marke Schaden anrichtet. Auf `Strafrecht AT VI` S. 3 steht „Beispiel:"
**nicht** im Rand (gemessen: x0 = 166 wie der Rumpf), sondern als Vorspann
derselben Zeile — das Modell gibt es trotzdem als eigene Zeile aus.
`randlabel_vorziehen` schweigt dazu zu Recht. Dafür griff die
Überschriften-Heuristik: kurz, vollständig fett, also Überschrift, also beginnt
danach zwingend ein neuer Absatz. Ergebnis war ein Absatz `**Beispiel:**` und
ein zweiter, der mitten im Satz anfängt — der Satz war zerrissen, nur anders als
vorher.

`ist_ueberschrift()` nimmt die reine Randmarke jetzt aus. Eine echte Überschrift
(`**A. Grundsätzliches zum Unterlassen**`) trennt unverändert.

Regression über alle 1426 vektoriellen Seiten (`regress_randmarke.py`):

| | |
|---|---|
| unverändert | 1403 |
| Absätze zusammengezogen | **23** |
| Zeichen verloren | **0** |

Alle 23 sind Verbesserungen desselben Musters — `**Ergebnis:**` / `**Anmerkung:**`
/ `**Hinweis:**` läuft jetzt in seinen Satz ein statt darüber zu stehen. Auf die
40 Benchmarkseiten wirkt sich die Regel nicht aus: dort kommt keine
alleinstehende Marke vor (in keiner der beiden Darstellungen geprüft), die
Messwerte unten sind davon also unberührt.

### 6. Kachelüberlappung — und der Fehler, den sie zuerst verursacht hat

Kacheln überlappen um `OVERLAP`, damit der Schnitt keine Zeile zerreißt; der
Preis ist ein doppelt erkanntes Überlappungsband. Bei zwei Kacheln ist das eine
Zeile, bei vier sind es drei — die Sache wird also genau dann wichtig, wenn eine
entgleiste Seite feiner gekachelt neu gerechnet wird.

Die erste Fassung verglich **zeilenweise** und war falsch. `parse_zeilen` fasst
zu Absätzen zusammen; der erste Absatz der unteren Kachel *beginnt* zwar mit dem
Überlappungsband, *trägt* aber den ganzen Rest der Seite mit sich. Er galt als
Dublette und flog vollständig heraus. Auf `UNIREP_KK_ZR_LH_30_01` S. 9 hat das
ein Drittel der Seite entfernt — **98,6 % → 58,0 % Wortgenauigkeit**, ohne jede
Meldung. Zwei weitere gesunde Seiten verloren 6 Punkte auf demselben Weg.

Aufgefallen ist es nur, weil der 40-Seiten-Lauf die gesunden Seiten getrennt
ausweist. Auf die Gesamtzahl geschlagen wäre es unsichtbar geblieben: die
reparierten Seiten haben mehr gewonnen als diese drei verloren.

`ueberlappung_kuerzen()` schneidet jetzt **wortweise**: gesucht wird das längste
Wortstück, das zugleich Ende des Vorhandenen und Anfang des Neuen ist, entfernt
wird nur dieses Stück. Findet sich keins — die beiden Kacheln lesen dieselbe
Zeile selten wortgleich —, bleibt alles stehen. Das ist die bewusste Richtung
des Fehlers: **eine sichtbare Dopplung ist besser als ein unsichtbarer
Verlust.** Ein Mindestmaß von 6 Wörtern und 30 Zeichen hält kurze
Gliederungsmarker heraus, „aa)" steht auf einer Seite zu Recht mehrfach.

Gegenprobe einzeln nachgemessen, nachdem die Nahtlogik korrigiert war:

| Seite | vorher | erste Fassung | korrigiert |
|---|---|---|---|
| `ZR_LH_30_01` S. 9 | 98,6 % | 58,0 % | **98,6 %** |
| `klausur-erbe` S. 5 | 97,7 % | 91,6 % | **97,3 %** |
| `OER_LH_05_12` S. 9 | 98,8 % | 92,8 % | **98,8 %** |

Dazu `test_naht.py` mit dem Fall, der die Seite gekostet hat, und der
Gegenprobe, dass eine im Band abweichend gelesene Zeile lieber doppelt stehen
bleibt als halb zu verschwinden.

### 7. Gesamtergebnis: derselbe Benchmark, vorher und nachher

Dieselben 40 Seiten, dieselbe Wahrheit aus dem Textlayer, einmal mit dem Stand
von Nachtrag 14 und einmal mit dem hier beschriebenen. Gewichtet nach Wörtern
bzw. Zitaten — eine 30-Wort-Seite soll nicht so schwer wiegen wie eine mit 800.

| | Wortgenauigkeit | Zitattreue | Reihenfolge |
|---|---|---|---|
| **alle 40 Seiten** | 93,3 % → **98,5 %** | 87,6 % → **92,4 %** | 84,7 % → **93,7 %** |
| davon vorher entgleist (6) | 64,8 % → **96,5 %** | 65,2 % → **87,0 %** | 32,7 % → **78,5 %** |
| davon vorher gesund (34) | 98,5 % → **98,8 %** | 93,3 % → **93,9 %** | 94,2 % → **96,5 %** |

Nicht wiedergegebene Normzitate: **32 → 17**. Keine einzige Seite ist
zurückgegangen. Die sechs entgleisten im Einzelnen:

| Seite | Wort | Zitate |
|---|---|---|
| `ZR_LH_07_11` S. 8 (Zähler) | 0,9 % → **100 %** | 0 % → **100 %** |
| `Klausur_2136` S. 2 | 23,2 % → **99,3 %** | — |
| `2143_SV` S. 2 (Abbruch) | 72,2 % → **98,9 %** | 67 % → 67 % |
| `2131_Lösung` S. 4 | 62,1 % → **88,3 %** | 43 % → 64 % |
| `FK_SR` S. 14 | 99,0 % → **100 %** | 100 % → 100 % |
| `2140_Lösung` S. 6 | 92,3 % → 92,4 % | 100 % → 100 % |

Dazu die Lesereihenfolge auf `Klausur_2137` S. 7 (Punkt 4): 88,3 % → **96,1 %**.

> **Die getrennte Ausweisung ist nicht Kosmetik.** Der erste Lauf mit dem neuen
> Stand kam auf 95,0 % gesamt und sah damit nach einem Erfolg aus. Getrennt
> gerechnet zeigte dieselbe Messung, dass die gesunden Seiten von 98,5 % auf
> 96,9 % **gefallen** waren — die Naht-Regression aus Punkt 6. Über alles
> gemittelt war der Verlust unsichtbar, weil die reparierten Seiten mehr
> gewonnen hatten. Eine Kennzahl, die Reparatur und Regression zu einer Zahl
> verrechnet, verbirgt genau den Fehler, den man sucht.

Die sechs Seiten sind damit nicht mehr die schwächsten. Was jetzt oben steht,
ist gewöhnliche OCR-Ungenauigkeit: `2131_Lösung` S. 4 mit 88,3 % (und einer
Reihenfolge von 49,7 % — eine mehrspaltige Seite, die noch nicht sauber
getrennt wird), danach beginnt das Feld bei 95,7 %.

### Was 8 GB RAM bedeuten

Der erste Versuch, den vollen Benchmark neben einer Bestandsregression laufen zu
lassen, endete mit SIGKILL für beide Prozesse. Das 4-bit-Modell belegt ~4 GB,
PyMuPDF über 1426 Seiten den Rest. **OCR-Läufe auf dieser Maschine laufen
allein.**

### Was offen bleibt

**Diagrammseiten ohne Bild-Fallback** — Punkt 3 aus Nachtrag 14 ist *nicht*
umgesetzt. `ist_diagramm()` ist unverändert. Das war eine Entscheidung, keine
Auslassung: die Erkennung ist auf handgeprüften Seiten kalibriert, und ein
Eingriff ohne eigene Messreihe hätte nur Wahrscheinlichkeiten verschoben —
gewonnene Diagrammseiten gegen verlorene Textseiten, ohne zu wissen, wie das
Verhältnis steht. Der Ausweg bleibt `--diagramm-seiten 8` für
`Strafrecht AT VI` S. 8.

**`Klausur_2136` S. 2 im Einzellauf** — im vollen Lauf jetzt bei 99,3 %, im
Sechs-Seiten-Lauf von `bench_defekt.py` blieb dieselbe Seite bei 23,2 %, weil
`_guete` den Neuversuch (26649 Z. Schleife) verwarf. Der Unterschied liegt am
Codestand zwischen beiden Läufen; die Zahlen in Abschnitt 3 stammen aus dem
älteren und werden von der Tabelle oben abgelöst.

## Nachtrag 2026-08-17 (16): Klärung der 140 Scanseiten mit unter 50 Zeichen

Im frühen Nebenbefund (Abschnitt oben) standen **140 Scanseiten mit unter 50 Zeichen**
im Textlayer als ungeklärte Zahl im Raum — mit dem Verdacht, dass Stufe 1 Seiten
übergangen haben könnte oder Inhalt verloren ging.

Die systematische Untersuchung aller 140 Seiten (aus dem damaligen `pages.json`-Stand
von 2.922 Seiten Gesamtbestand, 1.496 Scanseiten) zeigt: **alle 140 Seiten hatten exakt
0 Zeichen**, und keine einzige davon ist ein unerkannter Verlust im aktiven Studienbestand.

### Aufschlüsselung der 140 Seiten

| Kategorie / Pfad | Seiten | Dateien | Ursache & Status |
|---|---|---|---|
| `raw/assets/_archive/` | **68** | 5 | Unbearbeitete Original-Scans im Archivordner (`schuldrecht-at-fall-13-14-original-scan.pdf` (9 S.), `schuldrecht-at-fall-19-20-original-scan.pdf` (15 S.), `schuldrecht-at-fall-22-original-scan.pdf` (9 S.), `schuldrecht-at-fall-6-7-original-scan.pdf` (12 S.), `strafrecht-at-v-irrtuemer-ii-original-scan.pdf` (23 S.)). Alle 5 Dateien liegen im aktiven Vault vollständig mit OCR-Textlayer (28.000–58.000 Zeichen pro Datei) vor. |
| `raw/assets/repair-stage/` | **47** | 3 | Rohe Eingangs-PDFs im Reparatur-Zwischenordner (`fall-19-20.pdf` (15 S.), `fall-22.pdf` (9 S.), `strafrecht-at-v.pdf` (23 S.)). Die fertig gerechneten `-ocr.pdf`-Fassungen liegen direkt daneben mit vollständigem Textlayer. |
| `raw/StR/Strafrecht-BT/` | **24** | 1 | `strafrecht-bt-diebstahl-skript-1.pdf`: War zum Zeitpunkt der Ersterfassung ein Rohscan ohne Textlayer. Wurde am 2026-08-09 per Mistral-OCR (Stufe 2) vollständig nach Markdown (`strafrecht-bt-diebstahl-skript-1.md`, 56 KB Text, 1.028 Zeilen) überführt und das PDF nach `Pdfs_Alt/` archiviert. |
| `raw/ZR/Schuldrecht-AT/` | **1** | 1 | `Skizze-2134.pdf`: Eine 1-seitige handschriftliche Prüfungsskizze/Diagramm. Tesseract lieferte 0 Zeichen (reine Handschrift); Apple OCR (Vision) erkennt darauf 38 Textzeilen. |
| **Summe** | **140** | **10** | **Vollständig aufgeklärt** |

### Befund für den aktiven Bestand

Im aktiven Studienbestand (außerhalb von `raw/assets/` Archiv und Reparatur-Staging) gibt es **keine unvollständig oder fehlerhaft ausgelassenen Textseiten**.

Die einzigen verbleibenden Seiten mit unter 50 Zeichen im aktiven Bestand sind:
1. **Titel-, Trenn- und Schlussfolien** in Vorlesungs- und Klausurfolien (z. B. `VERTIEFUNGSTEIL 🤓`, `KLVK Zivilrecht RA Dr. Issa`, `Schlaft gut und schönes Wochenende!`, `IHR HABT ES GESCHAFFT`).
2. Die handschriftliche Prüfungsskizze `Skizze-2134.pdf`.

Damit ist vor dem Stapellauf (#21) gesichert: Die Zahl von 140 Seiten war ein Erfassungsartefakt unbereinigter Rohdateien in `_archive` und `repair-stage`, kein Pipeline-Ausfall.

---

## Nachtrag 2026-08-17 (17): Benchmark-Neumessung gegen den gemergten `main` (`ddf69e9`)

Neumessung aller 40 Seiten gegen den Textlayer (`bench/bench_ocr.py`, PaddleOCR-VL-1.5-4bit) nach Abschluss der Refactorings (Modulaufteilung Issue #8, Preflight-Check #24, geordneter Abbruch #25, Marker-Herkunft #10 und Übersetzung nach Englisch #41). Gemessen gegen Commit `ddf69e9`.

### 1. Gesamtergebnis: Vergleich gegen Stand `038689b`

40 Seiten aus 40 Dateien, 17 234 Wörter, 224 Normzitate:

| | 038689b (pdf2md/entgleisungsschutz) | **ddf69e9 (main)** |
|---|---|---|
| **Wortgenauigkeit gesamt** | 98,5 % | **98,2 %** |
| **Zitattreue gesamt** | 92,4 % | **92,0 %** |
| **Reihenfolge (Median)** | 93,7 % | **98,1 %** |

Nicht wiedergegebene Normzitate: **18 in 12 Formen** (Stand 038689b: 17 in 12 Formen).

### 2. Abweichungen im Detail

Der minimale Unterschied in Wortgenauigkeit (−0,3 %) und Zitattreue (−0,4 %) geht im Wesentlichen auf eine annotierte Scanseite zurück:

- **`Klausur_2143_korrigiert.pdf` S. 9**: 99,4 % → **83,3 %** Wortgenauigkeit, Reihenfolge 74,0 % → **31,6 %**, Zitattreue 100 % → **60,0 %** (3 von 5 Zitaten). Die Seite enthält handschriftliche Korrekturanmerkungen des Korrektors am Rand und im Fließtext. Das Vision-Modell transkribiert diese handschriftlichen Randbemerkungen („*Sie müssen an dieser Stelle strukturierter als auch dogmatischer vorgehen...*"), die im maschinengeschriebenen PDF-Textlayer nicht vorkommen. Zudem fehlen im PDF-Textlayer die Wortzwischenräume („*DieIdentitätsfeststellungmüssteverhältnismäßigsein*").
- **`UNIREP_KK_SR_SV_12_12_2025.pdf` S. 3**: 98,6 % → **95,6 %** (keine Normzitate auf der Seite).
- **`klausur-erbe-auf-umwegen.pdf` S. 5**: 97,3 % → **97,0 %** Wortgenauigkeit, Zitattreue **85,7 % → 100,0 %** (alle Zitate wiedergefunden).
- **37 der 40 Seiten** sind identisch oder liegen innerhalb von ±0,2 % des Vorstands.

### 3. Schwächste zehn Seiten auf `ddf69e9`

| Datei | S. | Wort | Reihe | Zitat |
|---|---|---|---|---|
| `Klausur_2143_korrigiert.pdf` | 9 | 83,3 % | 31,6 % | 60 % |
| `2131_Lösung_OeffentlichesRecht_Hessen.pdf` | 4 | 88,2 % | 49,8 % | 64 % |
| `UNIREP_KK_SR_SV_12_12_2025.pdf` | 3 | 95,6 % | 90,7 % | — |
| `2147_SV_OeffentlichesRecht_Hessen.pdf` | 2 | 95,7 % | 97,5 % | 100 % |
| `Klausur_2137_Strafrecht_Loesung.pdf` | 7 | 96,1 % | 96,8 % | 61 % |
| `grundrechtstraegerschaft-juristische-personen.pdf` | 4 | 96,2 % | 95,4 % | 100 % |
| `klausur-erbe-auf-umwegen.pdf` | 5 | 97,0 % | 76,9 % | 100 % |
| `UNIREP_KK_OER_SV_05_12_2025.pdf` | 3 | 97,7 % | 95,5 % | — |
| `Klausur_2146_Zivilrecht.pdf` | 2 | 98,1 % | 98,2 % | 100 % |
| `Klausur_2130_Zivilrecht_Loesung.pdf` | 8 | 98,5 % | 98,2 % | 100 % |

Normzitate, die der OCR-Pfad auf `ddf69e9` nicht wiedergibt: 18 in 12 Formen (`§ 244 I Nr. 3 StGB` 3×, `Art. 3` 2×, `§ 123 I StGB` 2×, `§ 243 I S. 2 Nr. 1 StGB` 2×, `§ 11 II SPolG` 2×, `Art. 20` 1×, `§ 13 I` 1×, `§ 39 I` 1×, `§ 48 HVwVfG` 1×, `§ 48 I HVwVfG` 1×, `§ 370 AO` 1×, `Art. 21 I` 1×).

## Nachtrag 2026-09-15 (18): PP-OCRv5 über ONNX Runtime (RapidOCR) — Laufzeit-Spike für #62

Schritt 1 aus `docs/paddle-textlayer.md`: Kann PaddleOCR auf dem Zielrechner (Apple M1, 8 GB, macOS 26.2) als Stage-1-Engine laufen? Gemessen gegen Repo-Stand `e18691c`.

### Warum nicht PaddlePaddle

PaddleOCR 3.x mit `paddlepaddle` auf der CPU hat die PP-OCRv5-Modelle geladen (`PP-OCRv5_server_det`, `latin_PP-OCRv5_mobile_rec`, dazu `UVDoc` und zwei `PP-LCNet`-Orientierungsmodelle) und ist danach mit **SIGSEGV in `libpaddle.so`** abgestürzt (Absturzbericht: Paddles Thread-Pool, `ThreadPoolTempl::WorkerLoop`; Python 3.12.4). Schon Pfad A des ersten Benchmarks war mit PaddlePaddle auf der CPU unbrauchbar (9.226 s pro Seite, durchgehend im Swap). Das Abbruchkriterium „no usable package“ gilt für `paddlepaddle` damit als erfüllt.

Der Spike läuft deshalb auf **denselben PP-OCRv5-Modellen im ONNX-Format über ONNX Runtime** (RapidOCR). `paddlepaddle` und `paddleocr` werden nicht installiert.

### Aufbau

```bash
python3.12 -m venv <tmp>/venv
<tmp>/venv/bin/pip install "rapidocr==3.9.2" "onnxruntime==1.26.0"
```

31 s Installation, 303 MB venv. RapidOCR zieht `opencv-python 5.0.0.93`, `numpy 2.5.3`, `shapely 2.1.2`, `pyclipper 1.4.0`, `omegaconf 2.3.1` u. a. nach. ONNX Runtime bietet `CoreMLExecutionProvider` und `CPUExecutionProvider`; gemessen wurde nur die CPU.

Modelle (von RapidOCR beim ersten Start aus seinem ModelScope-Repo `RapidAI/RapidOCR`, Tag `v3.9.2`, geladen; Download und Initialisierung zusammen 14,1 s):

| Datei | Größe | SHA-256 |
|---|---:|---|
| `ch_PP-OCRv5_det_mobile.onnx` | 4,8 MB | `4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae` |
| `ch_PP-OCRv5_det_server.onnx` | 88,1 MB | `0f8846b1d4bba223a2a2f9d9b44022fbc22cc019051a602b41a7fda9667e4cad` |
| `latin_PP-OCRv5_rec_mobile.onnx` | 7,9 MB | `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a` |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 0,6 MB | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

Das Detektionsmodell heißt bei RapidOCR `ch_…`, ist aber das eine mehrsprachige PP-OCRv5-Detektionsmodell. Deutsch erkennt das lateinische Erkennungsmodell; einen eigenen `de`-Schalter gibt es nicht. Cache-Pfad: `Global.model_root_dir` (im Spike ein eigenes `models/`).

Messskript: `bench/spike_rapidocr.py` (ein Prozess je Konfiguration, damit Peak-RSS eindeutig zuordenbar ist).

### Testseiten

Alle als PNG in A4-Breite 2480 px (300 dpi):

| Seite | Quelle | Was sie prüft |
|---|---|---|
| 01-zweispalter-handschrift | Benchmark-Set | Zweispalter, Kleindruck, Fußnoten, Marginalie, leichte Schräglage |
| 02-zweispalter-dicht | Benchmark-Set | Zweispalter dicht, Gliederung, Zitatblöcke |
| 07-zweispalter-vpr-s11 | `raw/OeR/Verwaltungsrecht-AT/Verwaltungsprozessrecht.pdf` S. 11 | Zweispalter-Scan, Handschrift am Rand, §§ |
| 08-zweispalter-avr-s16 | `raw/OeR/Verwaltungsrecht-AT/Allgemeines-Verwaltungsrecht-Skript.pdf` S. 16 | Zweispalter-Scan, schief, Fundstellen |
| 04-einspaltig-sauber | Benchmark-Set | einspaltig, Blocksatz, Urteilszitate |
| 09-einspaltig-gedreht-4grad | Seite 04 um 4° gedreht | Schräglage |

Referenzen sind **keine Ground Truth**: für 01/02/04 die Tesseract-Baseline des Benchmark-Sets, für 07/08 der vorhandene Textlayer der PDF. Sie taugen nur als Plausibilitätsprüfung (Zeichenzahl, Umlaute, ß, §). Die Genauigkeit misst Schritt 5.

### Messwerte

Je Konfiguration ein Prozess: sechs Seiten nacheinander (die erste inklusive ONNX-Aufwärmen), danach Seite 02 fünfmal. „volle Auflösung“ heißt `Global.max_side_len 4000`, also keine Verkleinerung; RapidOCRs Standard 2000 verkleinert A4 auf etwa 170 dpi. Ohne Textzeilen-Orientierungsklassifikator (`use_cls` aus; Drehung macht OCRmyPDF). Nur CPU.

| Konfiguration | Kaltstart bis bereit | 1. Seite | warm Median | warm Max | Zweispalter Median | Peak-RSS | Swap-outs | 5× gleich |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `v5-mobile-2000-t1` | 0,57 s | 12,1 s | 11,9 s | 15,2 s | 12,0 s | 1230 MB | 0 MB | ja |
| `v5-mobile-full-t1` | 0,85 s | 15,1 s | 14,4 s | 16,2 s | 14,7 s | 1918 MB | 0 MB | ja |
| `v5-mobile-full-t4` | 1,00 s | 7,9 s | 8,5 s | 10,5 s | 8,8 s | 2458 MB | 0 MB | ja |
| `v5-server-2000-t1` | 0,87 s | 32,3 s | 30,8 s | 33,4 s | 31,0 s | 1664 MB | 425 MB | ja |
| `v5-server-2000-t4` | 0,66 s | 14,7 s | 15,7 s | 17,5 s | 15,9 s | 2246 MB | 0 MB | ja |
| `v6-small-full-t1` | 0,80 s | 18,9 s | 18,5 s | 21,1 s | 18,5 s | 1692 MB | 0 MB | ja |

Weitere Einzelmessung: PP-OCRv5 **Server**-Detektion in voller Auflösung, 1 Thread, Seite 04: **69,9 s**, 2.198 MB, und die Seite zerfällt in 134 Bruchstücke statt 41 Zeilen.

Der Rechner hatte vor jedem Lauf schon 2,6–3,5 GB Swap belegt (Obsidian und andere Programme offen). „Swap-outs“ ist der Zuwachs während des Laufs.

### Erkennung: Plausibilität gegen die Referenzen

Zeichen / ä-ö-ü / ß / § je Seite, in Klammern die Sekunden:

| Seite | Referenz Zeichen / ä-ö-ü / ß / § | `v5-mobile-2000-t1` | `v5-mobile-full-t1` | `v5-mobile-full-t4` | `v5-server-2000-t1` | `v5-server-2000-t4` | `v6-small-full-t1` |
|---|---|---|---|---|---|---|---|
| 01-zweispalter-handschrift | 8591 / 86 / 6 / 27 | 8432 / 86 / 6 / 27 (13,8 s) | 8427 / 86 / 6 / 27 (16,2 s) | 8427 / 86 / 6 / 27 (9,0 s) | 8424 / 86 / 6 / 27 (33,4 s) | 8424 / 86 / 6 / 27 (15,9 s) | 8417 / 86 / 6 / 27 (21,1 s) |
| 02-zweispalter-dicht | 7041 / 121 / 12 / 24 | 6934 / 123 / 12 / 25 (12,1 s) | 6940 / 123 / 12 / 25 (15,1 s) | 6940 / 123 / 12 / 25 (7,9 s) | 6936 / 123 / 13 / 25 (32,3 s) | 6936 / 123 / 13 / 25 (14,7 s) | 6916 / 123 / 13 / 25 (18,9 s) |
| 04-einspaltig-sauber | 2709 / 38 / 5 / 1 | 2605 / 38 / 5 / 1 (5,9 s) | 2604 / 38 / 5 / 1 (7,2 s) | 2604 / 38 / 5 / 1 (4,3 s) | 2602 / 38 / 5 / 1 (24,0 s) | 2602 / 38 / 5 / 1 (11,3 s) | 2605 / 38 / 5 / 1 (9,7 s) |
| 07-zweispalter-vpr-s11 | 5687 / 76 / 4 / 0 | 5908 / 78 / 4 / 45 (10,6 s) | 5906 / 78 / 4 / 45 (13,3 s) | 5906 / 78 / 4 / 45 (7,0 s) | 5908 / 78 / 4 / 45 (31,1 s) | 5908 / 78 / 4 / 45 (17,5 s) | 5886 / 78 / 4 / 45 (16,7 s) |
| 08-zweispalter-avr-s16 | 7324 / 100 / 21 / 0 | 7147 / 98 / 22 / 9 (15,2 s) | 7149 / 98 / 22 / 9 (15,0 s) | 7149 / 98 / 22 / 9 (8,5 s) | 7140 / 98 / 22 / 9 (32,4 s) | 7140 / 98 / 22 / 9 (15,9 s) | 7120 / 98 / 22 / 9 (18,6 s) |
| 09-einspaltig-gedreht-4grad | 2709 / 38 / 5 / 1 | 2603 / 38 / 5 / 1 (6,0 s) | 2606 / 38 / 5 / 1 (7,8 s) | 2606 / 38 / 5 / 1 (4,5 s) | 2604 / 38 / 5 / 1 (24,7 s) | 2604 / 38 / 5 / 1 (11,0 s) | 2603 / 38 / 5 / 1 (10,4 s) |

- Alle PP-OCRv5-Varianten treffen Umlaute, ß und § praktisch wie die Referenz. Auf 07 und 08 findet RapidOCR 45 bzw. 9 Paragraphenzeichen, die im alten Textlayer ganz fehlen.
- Die um 4° gedrehte Seite 09 liefert dasselbe wie die gerade Seite 04.
- Server- und Mobile-Detektion unterscheiden sich im Text kaum; Server ist mit 1 Thread etwa zweieinhalbmal langsamer und liegt auch mit 4 Threads über 15 s. PP-OCRv6 `small` (in RapidOCR mitgeliefert) bringt nichts Messbares und ist langsamer.
- Die Boxen sind Vierecke je Zeile und folgen auf dem schiefen Scan 08 sichtbar den gekippten Zeilen (Overlay geprüft). Wortboxen gibt es mit `Global.return_word_box` ohne messbaren Zeitaufschlag (Seite 04: 7,4 s statt 7,2 s).
- RapidOCR liefert die Zeilen nach y sortiert; bei Zweispaltern wechseln sich linke und rechte Spalte ab. Die Lesereihenfolge bleibt Aufgabe von `order_lines()` (#69).

### Wiederholbarkeit, Offline, Speicher

- **Deterministisch:** In jeder Konfiguration ergeben die fünf Läufe von Seite 02 denselben Hash für Text und für Text plus Boxen. Mobile mit 1 und mit 4 Threads liefert sogar byte-gleiche Ergebnisse auf allen sechs Seiten.
- **Offline:** Ein warmer Lauf mit auf einen toten Port gesetztem `HTTP(S)_PROXY` läuft durch. RapidOCR lädt nur, wenn eine Modelldatei fehlt oder ihr SHA-256 nicht zu seiner Modellliste passt.
- **Kaltstart:** Import plus Initialisierung unter 1 s. Die erste Seite ist höchstens eine halbe Sekunde langsamer als ein warmer Lauf derselben Seite.
- **Speicher:** Peak-RSS 1,2–2,5 GB, also unter der Hälfte von 8 GB. Nur die Server-Detektion hat im Lauf Swap nach sich gezogen.

### Geteilte Umgebung

Klon der gepinnten OCRmyPDF-Umgebung (`pip freeze` von `ocrmypdf==17.8.0` + `ocrmypdf-appleocr==0.3.4` in ein frisches venv), danach `rapidocr==3.9.2` und `onnxruntime==1.26.0` dazu:

- `pip check` vorher und nachher: „No broken requirements found.“
- Keine vorhandene Version wurde geändert; RapidOCR fügt nur 17 Pakete hinzu.
- `import ocrmypdf, ocrmypdf_appleocr, rapidocr, onnxruntime` gemeinsam: ok.
- Smoke-Test Seite 04 mit `-l deu --output-type pdf --rasterizer pypdfium`: Tesseract 5 s, 2725 Zeichen, 38 Umlaute; Apple (`--plugin ocrmypdf_appleocr`) 3 s, 2699 Zeichen, 38 Umlaute.

### Nebenprobe: PaddleOCR-VL „Spotting:“ über MLX

Frage: Kann das Modell, das Stage 2 schon nutzt, Zeilen samt Position liefern und damit Paddle-Qualität ohne CPU-Laufzeit bringen? `mlx-vlm 0.6.10`, Prompt `Spotting:`, `max_pixels` auf 1.605.632 angehoben (Standard in `mlx_vlm`: 1.003.520), `max_tokens` 8192. Keine Gate-Messung.

| Lauf | Seite | Zeit | Ergebnis |
|---|---|---:|---|
| `PaddleOCR-VL-1.6-4bit`, Spotting | 04 | 42 s | Boxen sitzen genau auf den Zeilen (Overlay geprüft), aber 3 statt 38 Umlaute, ß → `B`, § → `$` |
| `PaddleOCR-VL-1.6-4bit`, `OCR:` | 04 | 129 s | Wiederholungsschleife („-Vorstrafen“), Token-Budget erschöpft |
| `PaddleOCR-VL-1.5-4bit`, Spotting | 04 | 36 s | 38 Zeilen, 37 von 38 Umlauten, 5 ß, 1 §, Boxen passend |
| `PaddleOCR-VL-1.5-4bit`, Spotting | 02 (dicht) | 123 s | Token-Budget nach 90 Zeilen erschöpft, erst bei etwa 62 % der Seitenhöhe |

Das 4-Bit-Modell 1.6 ist in `mlx_vlm` für deutschen Text nicht brauchbar. 1.5 liefert auf einer lockeren Seite Text und Boxen in guter Qualität, braucht auf einer dichten Seite aber mehr als 8192 Tokens und hochgerechnet über 200 s. Kacheln gibt es für Spotting nicht. Als Stage-1-Engine scheidet Spotting damit aus; RapidOCR ist auf derselben dichten Seite etwa 15-mal schneller.

### Entscheidung

**Go** für PP-OCRv5 über RapidOCR mit dieser Konfiguration:

- Detektion `ch_PP-OCRv5_det_mobile`, Erkennung `latin_PP-OCRv5_rec_mobile`;
- volle Auflösung (`Global.max_side_len` nicht unter der Seitenlänge in Pixeln);
- 4 ONNX-Runtime-Threads (intra- und inter-op), OCRmyPDF mit einem Job.

| Abbruchkriterium | Befund |
|---|---|
| kein nutzbares Paket für Python 3.12 arm64 | `rapidocr 3.9.2` und `onnxruntime 1.26.0` installieren sauber |
| warm über 15 s pro Seite | höchstens 10,5 s, Median 8,5 s |
| mehr als die Hälfte des RAM oder Swap-Druck | Peak-RSS 2.458 MB von 8 GB, keine Swap-outs |
| wesentlich nichtdeterministisch | fünf identische Hashes; 1 und 4 Threads byte-gleich |
| Konflikt in der geteilten Umgebung | `pip check` sauber, Apple und Tesseract laufen |

Verworfen: 1 Thread (dichte Zweispalter bis 16,2 s), Server-Detektion (auch mit 4 Threads bis 17,5 s; in voller Auflösung 69,9 s und zerstückelte Zeilen), PP-OCRv6 `small` (langsamer, kein Gewinn).

### Durchgang durch OCRmyPDF (Prototyp)

Zur Ansicht lief ein Wegwerf-Plugin (nicht #68) mit dieser Konfiguration durch OCRmyPDF 17.8.0 in der geklonten Umgebung: fünf Zweispalter-Scanseiten aus `Verwaltungsprozessrecht.pdf` (S. 10–14, als 300-dpi-JPEG), `-l deu -j 1 --output-type pdf --rasterizer pypdfium`. Gesamt 49 s, davon Erkennung 6,7–9,8 s je Seite. Suche und Markieren funktionieren; „§ 55a VwGO“ steht korrekt im Textlayer, Tesseract macht auf derselben Seite `$ 55a` daraus.

Befunde für #68 und #71:

- **Wortboxen verwenden.** Werden Wörter nur proportional in der Zeilenbox verteilt, klebt `pdftotext -raw` auf schrägen Zeilen ganze Zeilen zusammen. Mit den Wortboxen aus `Global.return_word_box` wird es deutlich besser, aber einzelne Wörter auf schrägen Zeilen klebt `pdftotext -raw` weiterhin zusammen („Geschiehtdies dennoch“), während PyMuPDF denselben Textlayer sauber mit Leerzeichen liefert. Die Wortgenauigkeit in Schritt 5 darf deshalb nicht allein an `pdftotext -raw` hängen.
- **Auflösung selbst begrenzen.** Mit `--redo-ocr` hat OCRmyPDF die Originalseiten (2.906 × 4.109 pt, Bild mit 72 dpi) auf 16.144 × 22.828 px gerastert, 368 MP; RapidOCR hat das auf 4.000 px verkleinert. Das Plugin muss die Bildgröße explizit prüfen und begrenzen.
- **Reihenfolge.** RapidOCR sortiert Zeilen nach y; die Spalten wechseln sich ab. Das bleibt #69.

## Nachtrag 2026-09-15 (19): Lesereihenfolge — Wahrheitsset und Split-Entscheidung für #69

**Ergebnis: keep split mode.** `--engine paddle --split-columns` bleibt der unterstützte Befehl.

Ungeteiltes PaddleOCR mit `order_lines()` hat im Median eine höhere Präzedenzgenauigkeit als beide Split-Baselines (99,7 % gegen 96,5 %). Es verletzt aber zwei Gate-Kriterien:
- 18 Vollbreite-Zeilen stehen falsch.
- Auf einer Seite sind die Fußnoten beider Spalten verschränkt.

Plan: `docs/paddle-textlayer.md`, Schritt 3.

### Aufbau

**Wahrheitsset:** `bench/reading_order_truth.json` mit 16 Vault-Seiten.
- **Zweispalter (10):** Repetitoriums-Skripte und -Fälle, Verwaltungsrecht, ein 150-dpi-Scan, ein schief fotografierter Scan.
- **Einspalter (6):** Handschrift am Rand, Schräglage, Kopf- und Fußzeilen.

**Seiten:** `prepare` rastert mit `pdftoppm -gray -scale-to 3508` und baut mit `img2pdf` 300-dpi-PDFs.

**Wahrheit:**
- Die Wahrheitszeilen sind die PP-OCRv5-Zeilen des Seitenbilds (`recognize`).
- Die Rollenregionen (Kopf, Überschrift, Text, Fußnote, Randnotiz, Fuß) sind auf Rasterbildern gezeichnet, bevor eine Ordnungsausgabe dieser Seiten angesehen wurde.
- Jede Zeile gehört zur kleinsten Region, die ihren Mittelpunkt enthält, und wird darin zeilenweise gelesen.
- Die Overlays (`overlay`) sind von Hand geprüft. Keine erkannte Zeile liegt außerhalb aller Regionen.

**Konvention:**
- Kopf zuerst.
- Je Abschnitt die linke Spalte (Text, Fußnoten, Randnotizen), dann die rechte.
- Vollbreite-Überschriften an ihrem Platz, Fußzeile zuletzt.
- Handschrift im Textbereich wird in ihrer Zeile gelesen.

**Metrik (`score`):**
- **Zuordnung:** Jede Wahrheitszeile ab 10 normalisierten Zeichen (NFKC, klein, nur Buchstaben, Ziffern und §) wird per 4-Gramm-Abstimmung in `pdftotext -raw` gesucht. Längere Zeilen belegen ihre Textstelle zuerst.
- **Wert:** paarweise Präzedenzgenauigkeit über die gefundenen Zeilen.
- **Getrennt ausgewiesen:**
  - nicht gefundene und doppelte Zeilen;
  - Vollbreite-Zeilen, die mit einer Spaltenzeile vertauscht sind;
  - Abschnitte mit verschränkten Textspalten;
  - Seitenzahl 1 und B5 (`column_tools.py verify-pages --min-chars 50`);
  - Ausgabegröße.
- **Größe:** In den Stage-1-Scripts gibt es keine Größenschwelle. Die Größe wird deshalb nur berichtet.

### Workflows und Befehle

| Workflow | Lauf |
|---|---|
| `split-apple`, `split-tesseract` | `bin/pdf-auto.sh --engine apple` bzw. `tesseract` mit `--split-columns`, Qualitätsgate an |
| `unsplit-apple`, `unsplit-tesseract` | `bin/pdf-auto.sh --engine apple` bzw. `tesseract` mit `--no-quality-gate` |
| `split-paddle` | `column_tools.py split --auto`, dann `ocrmypdf --plugin ocrmypdf_paddle -l deu --skip-text --optimize 3 --jobs 1 --max-image-mpixels 400`, dann `column_tools.py merge` |
| `unsplit-paddle` | derselbe OCRmyPDF-Aufruf ohne Split, mit `--rotate-pages --deskew` |
| `rapidocr-order`, `order_lines` | kein PDF: die Wahrheitszeilen in RapidOCRs Reihenfolge bzw. von `order_lines()` geordnet; misst die Ordnung ohne Erkennungs- und PDF-Einfluss |

```text
python bench/reading_order.py prepare
python bench/reading_order.py recognize
python bench/reading_order.py overlay
python bench/reading_order.py run --optimize 3
python bench/reading_order.py --python <OCRmyPDF-Python> score
```

**Umgebung:**
- **Stand und Variablen:** Repo-Stand `8b957ab` plus diese Änderung, `VAULT_ROOT` auf den Vault, `OCRMYPDF_PADDLE_MODEL_DIR` auf die Modelle aus Nachtrag 18.
- **Shell-Workflows:** laufen in `~/.venvs/ocrmypdf`; `pdf-auto.sh` meldet `--optimize 3` und `--jobs 1`.
- **Paddle-Workflows und `recognize`:** laufen in der geklonten OCRmyPDF-Umgebung mit RapidOCR, das Netz ist über einen toten Proxy-Port gesperrt.
- **Versionen:**

| Paket bzw. Werkzeug | Version |
|---|---|
| ocrmypdf | 17.8.0 |
| ocrmypdf-appleocr | 0.3.4 |
| Tesseract | 5.5.2 |
| Ghostscript | 10.07.1 |
| Poppler `pdftotext` | 26.08.0 |
| pikepdf | 10.9.1 |
| img2pdf | 0.6.3 |
| rapidocr | 3.9.2 |
| onnxruntime | 1.26.0 |

- **Fallbacks und Paddle-Laufzeit:** Kein Lauf zeigt einen Engine-Fallback. Die Paddle-Laufzeit beträgt 382 s (Split) bzw. 315 s (ungeteilt) für 16 Seiten; das ist nur ein Richtwert, gemessen wird in Schritt 5.

### Messwerte

| Workflow | Seiten | Median | Mittel | Min | Median (gemeinsame Zeilen) | verschränkte Seiten | Vollbreite falsch / doppelt / fehlt (von 77) | nicht gefunden (von 1348) | doppelt | Seitenprüfung fehlgeschlagen | Größe |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `split-apple` | 16 | 96,0 % | 96,9 % | 91,4 % | 96,4 % | 1 | 53 / 0 / 8 | 28 | 9 | 0 | 10,6 MB |
| `split-tesseract` | 16 | 96,5 % | 97,0 % | 92,5 % | 96,7 % | 0 | 52 / 0 / 12 | 50 | 7 | 0 | 10,6 MB |
| `unsplit-apple` | 16 | 99,5 % | 98,2 % | 93,0 % | 100,0 % | 5 | 17 / 0 / 5 | 21 | 9 | 0 | 8,9 MB |
| `unsplit-tesseract` | 16 | 99,8 % | 93,0 % | 74,6 % | 99,8 % | 6 | 8 / 0 / 7 | 79 | 7 | 0 | 9,0 MB |
| `split-paddle` | 16 | 96,1 % | 96,9 % | 91,1 % | 96,7 % | 0 | 55 / 0 / 6 | 12 | 7 | 0 | 10,6 MB |
| `unsplit-paddle` | 16 | 99,7 % | 99,0 % | 94,9 % | 99,7 % | 1 | 18 / 0 / 0 | 5 | 7 | 0 | 10,2 MB |
| `rapidocr-order` | 16 | 82,6 % | 86,9 % | 75,5 % | 80,5 % | 10 | 0 / 0 / 0 | 0 | 5 | – | – |
| `order_lines` | 16 | 99,7 % | 99,4 % | 97,3 % | 99,7 % | 1 | 10 / 0 / 0 | 0 | 8 | – | – |

Je Seite (`*` = Textspalten verschränkt):

| Seite | Layout | `split-apple` | `split-tesseract` | `unsplit-apple` | `unsplit-tesseract` | `split-paddle` | `unsplit-paddle` | `rapidocr-order` | `order_lines` |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| t01 | Zweispalter, Kopf, Fußnoten, Fuß | 95,8 % | 95,9 % | 94,2 %* | 92,8 % | 95,8 % | 95,9 % | 79,2 %* | 99,3 % |
| t02 | Zweispalter, Handschrift an beiden Rändern, Fußnoten | 96,2 % | 96,2 % | 100,0 % | 100,0 % | 96,2 % | 99,4 % | 77,9 %* | 99,4 % |
| t03 | Zweispalter, hinterlegte Überschrift, Randnotiz, Fußnoten | 99,2 % | 99,2 % | 100,0 % | 99,7 % | 99,2 % | 99,2 % | 75,5 %* | 99,2 % |
| t04 | Zweispalter, hinterlegte Überschriften, Fußnoten | 95,2 % | 95,2 % | 93,3 %* | 99,9 % | 95,2 % | 99,2 % | 82,7 %* | 99,2 % |
| t05 | Zweispalter, schief fotografiert, Kästen, Fußnoten | 95,8 %* | 96,8 % | 96,0 %* | 95,8 %* | 96,1 % | 99,2 % | 78,8 %* | 99,2 % |
| t06 | Zweispalter, Kästen | 94,9 % | 94,9 % | 99,2 % | 85,0 %* | 94,9 % | 94,9 % | 78,3 %* | 97,5 % |
| t07 | Kopf, Vollbreite-Titel über zwei Spalten, Übersichtskästen | 91,4 % | 92,5 % | 97,8 %* | 74,6 %* | 91,1 % | 100,0 % | 82,4 %* | 100,0 % |
| t08 | Zweispalter, Kästen, Fußnoten | 94,3 % | 94,3 % | 99,0 % | 77,5 %* | 94,3 % | 99,0 % | 75,9 %* | 99,0 % |
| t09 | Zweispalter, Kästen, Fußnoten | 94,6 % | 94,2 % | 93,0 %* | 77,9 %* | 94,5 % | 97,2 %* | 76,7 %* | 97,3 %* |
| t10 | Zweispalter aus 150-dpi-Scan, Kopf, Vollbreite-Titel | 93,6 % | 93,2 % | 98,9 % | 85,0 %* | 93,6 % | 100,0 % | 83,4 %* | 100,0 % |
| t11 | einspaltig, Kopf, Titel, Fuß | 100,0 % | 100,0 % | 100,0 % | 99,9 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % |
| t12 | einspaltig, Handschrift neben Überschriften, Seitenzahl | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % |
| t13 | einspaltig, Handschrift über dem Titel und am linken Rand | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % |
| t14 | einspaltig, Seitenzahl | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % |
| t15 | einspaltig, schief fotografiert | 99,9 % | 100,0 % | 99,9 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % | 100,0 % |
| t16 | einspaltig, Kopf, Fuß, Ordnerringe im Bild | 99,9 % | 100,0 % | 99,9 % | 100,0 % | 100,0 % | 100,0 % | 99,9 % | 100,0 % |

### Befunde

**Gate für `unsplit-paddle`:**
- **Median:** 99,7 % gegen die beste Split-Baseline 96,5 % (`split-tesseract`). Das Kriterium ist erfüllt.
- **Seitenprüfungen:** Seitenzahl und B5 bestehen auf allen 16 Seiten.
- **Vollbreite-Zeilen:** Das Kriterium ist verfehlt, 18 Zeilen stehen in einer Spalte.
  - **Seitentitel:** 8-mal die rechte Seitentitel-Zeile des Kopfes („…, Seite N“), auf t01–t06, t08 und t09.
  - **Ortsliste:** auf t01 und t06 zusätzlich die fünfzeilige Ortsliste desselben Kopfes.
  - **Ursache:** Auf diesen Seiten steht die letzte Kopfzeile weniger als eine Zeilenhöhe über dem Text. `order_lines()` schneidet deshalb kein Kopfband ab, und der rechte Teil des Kopfes wird als Anfang der rechten Spalte gelesen.
  - **Real gegen Wahrheitsgeometrie:** Auf der Geometrie der Wahrheitszeilen (`order_lines`) sind es 10 statt 18 Zeilen. Im echten Lauf erkennt PaddleOCR die mit `--rotate-pages --deskew` aufbereitete Seite neu. Dabei wird auf t01 und t06 zusätzlich die ganze Ortsliste der rechten Spalte zugeschlagen.
- **Keine Verschränkung:** Das Kriterium ist verfehlt, t09 verschränkt die Fußnoten. Dort beginnen die Fußnoten beider Spalten auf gleicher Höhe, die Lücke darüber läuft über die ganze Seite. Die Fußnoten landen im Fußband und werden zeilenweise über beide Spalten gelesen.

**Split-Baselines:**
- Sie verletzen das Vollbreite-Kriterium stärker (52–55 Zeilen). Der Split schneidet den Kopf; seine rechte Hälfte folgt erst nach der ganzen linken Spalte.
- Deshalb liegen die Split-Workflows auf den Zweispaltern meist nur bei 91–96 %.

**Native ungeteilte OCR:** Sie ist keine Alternative. Apple verschränkt die Spalten auf 5 Seiten, Tesseract auf 6 (Minimum 74,6 %).

**Ohne `order_lines()`:** Der Textlayer wäre unbrauchbar. RapidOCRs Reihenfolge (`rapidocr-order`) verschränkt jede der 10 Zweispalterseiten (Median 82,6 %).

**Erkennung und Größe:**
- **Nicht gefunden:** `unsplit-paddle` findet bis auf 5 von 1348 Zeilen alles (je 1–2 Zeilen auf t07, t12, t13 und t15). `split-tesseract` verfehlt 50 Zeilen.
- **Größe:** 16 Seiten ergeben 8,9–10,6 MB. Die Paddle-Läufe gehen nicht durch `pdf-auto.sh`, weil Paddle dort erst mit Schritt 4 eine Engine wird. Die Größen sind daher nur grob vergleichbar.

### Einschränkungen

- **Segmentierung:** Die Wahrheitszeilen stammen aus PaddleOCRs Zeilensegmentierung. Eine Zeile, die eine andere Engine anders schneidet, wird trotzdem gefunden, solange mindestens die Hälfte ihrer 4-Gramme übereinstimmt. Paddle-Workflows haben hier höchstens einen leichten Heimvorteil.
- **Fehlende Seitenart:** Eine echte Seite mit Vollbreite-Überschrift *zwischen* zwei Spaltenbereichen ist nicht im Set. Dieser Fall ist nur synthetisch getestet.
- **Messfehler in der ersten Auswertung, behoben:**
  - **Fehler:** Kurze Zeilen, etwa eine Fundstelle in der Fußnote, trafen dieselbe Textstelle in einer längeren Zeile der anderen Spalte. Das ergab falsche Verschränkungen und Doppelte, bei `split-apple` z. B. 5 statt 1 verschränkte Seiten und 59 statt 9 doppelte Zeilen.
  - **Korrektur:** Längere Zeilen belegen ihre Textstelle jetzt zuerst, mit Test in `bench/test_reading_order.py`. Alle Workflows wurden neu bewertet; `order_lines()` blieb dabei unverändert.
- **Keine Nachjustierung:** `order_lines()` wurde an diesen 16 Seiten nicht nachjustiert. Eine Korrektur für den dichten Kopf und für seitenbreite Fußnotenlücken muss auf Seiten außerhalb dieses Sets gemessen werden. Sonst zeigt ein bestandenes Gate nur Anpassung an das Set.

## Nachtrag 2026-09-16 (20): Lesereihenfolge — dichter Kopf, Fußnoten beider Spalten und neue Prüfseiten für #87

**Ergebnis: keep split mode.** Auf 13 neuen Prüfseiten steht keine Vollbreite-Zeile mehr falsch, aber drei Gate-Kriterien bleiben verletzt: 3 Kopfzeilen werden nicht gefunden, und auf 3 Seiten gelten die Spalten als verschränkt. `--engine paddle --split-columns` bleibt der unterstützte Befehl; #71 vergleicht ungeteiltes PaddleOCR nicht.

Auf den alten 16 Seiten besteht das Gate jetzt. Diese Seiten haben die beiden Fehler aber erst gezeigt, ihr Ergebnis belegt nur, dass die Korrektur dort wirkt. Entschieden wird auf den neuen Seiten.

Plan: `docs/paddle-textlayer.md`, Schritt 3. Vorgänger: Nachtrag 19.

### Korrektur (`aad8774`)

**Befund auf den alten Seiten:** Die erkannten Zeilenboxen der dichten Scans überlappen sich senkrecht (Boxhöhe 50–75 px bei rund 38 px Zeilenabstand). `_leading()` ergibt deshalb 0, und zwischen Kopf und Text liegen nur 1–27 px. Keine Lückenschwelle trennt dort Kopf und Text.

**Signal:** Auf allen Repetitoriums-Seiten endet der Kopf mit der Zeile „Rubrik | …, Seite N“. Deren rechter Teil ist rechtsbündig und beginnt 3,6–9,4 Zeilenhöhen rechts von dem Rand, an dem die vollen Zeilen der rechten Spalte beginnen. In Textzeilen beginnt die rechte Zeile höchstens 1,6 Zeilenhöhen neben diesem Rand (hängende Gliederungsziffern, Einzüge).

**`order_lines()` auf Zweispalterseiten (`_column_bands()`):**
- **Spaltenpaar:** eine linke und eine rechte Zeile nebeneinander; die rechte beginnt höchstens `ALIGN` = 2,5 Zeilenhöhen rechts vom Rand der rechten Spalte.
- **Kopf:** reicht bis zur untersten Lücke zwischen Zeilenkernen (halbe Zeilenhöhe um die Zeilenmitte) oberhalb des ersten Spaltenpaars, weiterhin in den oberen 22 % der Seite und mit höchstens einem Viertel der Zeilen.
- **Fuß:** Zeilen des Fußbands gehen von oben her zeilenweise an die Spalten zurück, solange eine Zeile nur Zeilen einer Spalte oder ein Spaltenpaar enthält und mindestens eine davon ein Spaltenpaar enthält. Die erste Zeile, die den Steg kreuzt oder ohne Spaltenpaar beide Seiten belegt, beginnt den Fuß.
- **Tests:** synthetische Tests in `ocrmypdf_paddle/test/test_paddle_ordering.py` für den dichten Kopf, für Fußnoten auf gleicher Höhe in beiden Spalten und für eine zweiteilige Fußzeile mit rechtsbündigem Teil. Die ersten beiden schlagen mit dem Stand von `main` fehl. Alle 123 Paddle- und Bench-Tests bestehen.

Die Korrektur wurde committet, bevor die neuen Seiten ausgesucht wurden, und danach nicht mehr geändert.

### Neue Prüfseiten (`dfcbdfc`)

**Set:** `bench/reading_order_holdout.json`, 13 Seiten aus 9 Dokumenten, die im alten Set nicht vorkommen.
- **Zweispalter (12):** alle mit Repetitoriums-Kopf. Auf n04, n06 und n07 beginnen die Fußnoten beider Spalten auf gleicher Höhe, auf n05 und n10 fast gleich hoch.
- **Einspalter (1):** n13 als Kontrolle.
- **Fehlende Seitenart:** Eine echte Vollbreite-Überschrift *zwischen* zwei Spaltenbereichen fand sich in den durchgesehenen Dokumenten nicht; n11 hat nur einen Falltitel über den Spalten.

**Ablauf:**
1. Seiten auf Übersichtsbildern ausgewählt, ohne Ordnungsausgabe.
2. `prepare` und `recognize`, dann Regionen auf den Rasterbildern gezeichnet.
3. Overlays von Hand geprüft; keine erkannte Zeile liegt außerhalb aller Regionen. Zeilen nahe einer Regionsgrenze wurden zusätzlich als Text geprüft; danach liegt die Kopfgrenze auf n04 höher, weil die erste Zeile der rechten Spalte auf dem schiefen Scan in den Kopfbereich ragte.
4. Wahrheit committet, erst danach `score`.

**Befehle:** wie in Nachtrag 19, mit eigener Wahrheit und eigenem Laufverzeichnis:

```text
python bench/reading_order.py --truth bench/reading_order_holdout.json --run-dir <neu> prepare
python bench/reading_order.py --truth bench/reading_order_holdout.json --run-dir <neu> recognize
python bench/reading_order.py --truth bench/reading_order_holdout.json --run-dir <neu> overlay
python bench/reading_order.py --truth bench/reading_order_holdout.json --run-dir <neu> run --optimize 3
python bench/reading_order.py --truth bench/reading_order_holdout.json --run-dir <neu> --python <OCRmyPDF-Python> score
```

**Umgebung:** unverändert gegenüber Nachtrag 19 (dieselben Umgebungen, Modelle und Versionen). Kein Lauf zeigt einen Engine-Fallback.

### Messwerte auf den neuen Seiten

| Workflow | Seiten | Median | Mittel | Min | Median (gemeinsame Zeilen) | verschränkte Seiten | Vollbreite falsch / doppelt / fehlt (von 86) | nicht gefunden (von 1360) | doppelt | Seitenprüfung fehlgeschlagen | Größe |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `split-apple` | 13 | 94,8 % | 95,1 % | 89,9 % | 94,7 % | 2 | 61 / 0 / 8 | 30 | 24 | 0 | 9,2 MB |
| `split-tesseract` | 13 | 94,8 % | 95,0 % | 87,7 % | 94,6 % | 2 | 61 / 0 / 14 | 26 | 21 | 0 | 9,2 MB |
| `unsplit-apple` | 13 | 97,0 % | 96,0 % | 83,1 % | 96,9 % | 5 | 32 / 0 / 7 | 20 | 32 | 0 | 7,6 MB |
| `unsplit-tesseract` | 13 | 91,4 % | 90,5 % | 77,1 % | 91,4 % | 8 | 14 / 0 / 10 | 58 | 22 | 0 | 7,8 MB |
| `split-paddle` | 13 | 94,9 % | 94,8 % | 85,8 % | 94,7 % | 2 | 64 / 0 / 7 | 11 | 29 | 0 | 9,3 MB |
| `unsplit-paddle` | 13 | 100,0 % | 99,1 % | 91,9 % | 100,0 % | 3 | 0 / 0 / 3 | 8 | 33 | 0 | 8,6 MB |
| `rapidocr-order` | 13 | 77,9 % | 80,1 % | 76,1 % | 77,3 % | 12 | 0 / 0 / 0 | 0 | 20 | – | – |
| `order_lines` | 13 | 100,0 % | 96,3 % | 76,0 % | 100,0 % | 3 | 0 / 0 / 0 | 0 | 25 | – | – |

Je Seite (`*` = Textspalten verschränkt):

| Seite | Layout | `split-apple` | `split-tesseract` | `unsplit-apple` | `unsplit-tesseract` | `split-paddle` | `unsplit-paddle` | `rapidocr-order` | `order_lines` |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| n01 | Zweispalter, dichter Kopf, Zitatkästen, Fußnoten, Fuß | 99,3 % | 99,2 % | 99,2 %* | 97,5 % | 99,3 % | 100,0 % | 77,0 %* | 100,0 % |
| n02 | Zweispalter, dichter Kopf, Kästen, Fuß | 94,2 % | 94,1 % | 94,0 % | 82,7 %* | 94,2 % | 100,0 % | 77,9 %* | 100,0 % |
| n03 | Zweispalter, dichter Kopf, Kästen, Fußnote rechts, Ordnerring | 94,9 % | 94,8 % | 94,9 % | 85,1 %* | 94,9 % | 100,0 % | 79,3 %* | 100,0 % |
| n04 | Zweispalter, dichter Kopf, Fußnoten gleich hoch, schiefer Hochkontrast-Scan | 94,8 %* | 94,7 %* | 94,8 %* | 90,0 %* | 94,8 %* | 98,5 %* | 77,5 %* | 78,0 %* |
| n05 | Zweispalter, dichter Kopf, Kasten, Fußnoten fast gleich hoch | 95,0 % | 95,0 % | 99,2 % | 78,4 %* | 95,0 % | 100,0 % | 79,2 %* | 100,0 % |
| n06 | Zweispalter, dichter Kopf, Fußnoten gleich hoch, dunkler Scanrand | 94,8 % | 94,8 % | 94,8 % | 91,4 % | 94,8 % | 100,0 % | 76,3 %* | 100,0 % |
| n07 | Zweispalter, Kopf, Kasten, Fußnoten gleich hoch, Ordnerring | 94,8 % | 94,8 % | 97,0 %* | 100,0 % | 94,9 % | 97,7 %* | 78,4 %* | 97,7 %* |
| n08 | Zweispalter, angeschnittener Kopf, Kästen, lange Fußnoten auf verschiedener Höhe | 96,7 % | 98,3 % | 93,3 %* | 97,6 %* | 96,7 % | 100,0 % | 78,0 %* | 100,0 % |
| n09 | zwei Spalten mit Fragekästen, Handschrift in einer Fragezeile, durchscheinender Text am linken Rand | 89,9 %* | 87,7 %* | 83,1 %* | 77,1 %* | 85,8 %* | 91,9 %* | 76,1 %* | 76,0 %* |
| n10 | Zweispalter, dichter Kopf, Kästen, Fußnoten fast gleich hoch, Ordnerring | 95,3 % | 95,0 % | 99,2 % | 78,3 %* | 95,2 % | 100,0 % | 77,3 %* | 100,0 % |
| n11 | Kopf, Vollbreite-Falltitel über zwei Gliederungsspalten | 93,5 % | 93,3 % | 100,0 % | 98,9 %* | 93,5 % | 100,0 % | 77,4 %* | 100,0 % |
| n12 | dichter Kopf, zwei ungleich lange Kastenspalten, Fußnote rechts | 93,8 % | 93,9 % | 98,9 % | 100,0 % | 93,9 % | 100,0 % | 86,7 %* | 100,0 % |
| n13 | einspaltig, Kopf, Fuß, Ordnerring | 100,0 % | 99,9 % | 100,0 % | 99,9 % | 100,0 % | 100,0 % | 99,9 % | 100,0 % |

### Befunde auf den neuen Seiten

**Gate für `unsplit-paddle`:**
- **Median:** 100,0 % gegen die beste Split-Baseline 94,8 % (`split-apple`, `split-tesseract`). Das Kriterium ist erfüllt.
- **Seitenprüfungen:** Seitenzahl und B5 bestehen auf allen 13 Seiten.
- **Vollbreite-Zeilen falsch oder doppelt:** keine. Die Kopfkorrektur trägt auf den neuen Seiten. Mit `order_lines()` von `main` stünden auf denselben Zeilen des echten Laufs 18 Vollbreite-Zeilen falsch und 4 Seiten wären verschränkt.
- **Vollbreite-Zeilen nicht gefunden:** Das Kriterium ist verfehlt, 3 Kopfzeilen fehlen. Es sind Erkennungs-, keine Ordnungsfehler: Der echte Lauf erkennt die mit `--rotate-pages --deskew` aufbereitete Seite neu und liest die angeschnittene erste Kopfzeile auf n08, ein Ortslisten-Bruchstück auf n11 und „Juristisches Repetitorium“ auf n10 anders als das Seitenbild der Wahrheit.
- **Keine Verschränkung:** Das Kriterium ist verfehlt, n04, n07 und n09 gelten als verschränkt.
  - **n07, echter Fehler:** Die Fußnoten beider Spalten beginnen auf gleicher Höhe, ihre Zeilen sind aber um eine halbe Zeile gegeneinander versetzt. `_row_groups()` legt deshalb nie eine linke und eine rechte Fußnote in dieselbe Zeile. Die Fußregel sucht das Spaltenpaar innerhalb einer Zeile, findet keins und gibt keine Fußnote an die Spalten zurück; die Fußnoten werden über beide Spalten zeilenweise gelesen. Auf n10 (Fußnoten fast gleich hoch) greift die Regel; mit `main` war n10 verschränkt.
  - **n04 und n09 im echten Lauf, Messartefakt:** Die Reihenfolge der Debug-Ausgabe ist regionsweise richtig (Kopf, linke Spalte, linke Fußnoten, rechte Spalte, rechte Fußnoten, Fuß). n04 ist in Einzelwörter zerlegt („Fixgeschäft“ steht mehrfach als eigene Zeile), n09 wiederholt Fundstellen („Hemmer/Wüst, Basics Zivilrecht, Band 1“). `score` vergibt gleiche Schlüssel der Reihe nach und erzeugt so scheinbare Spaltenwechsel.
  - **n04 und n09 in `order_lines` auf der Wahrheitsgeometrie:** 78 % bzw. 76 %. Auf dem nicht entzerrten Seitenbild findet `_gutter()` keinen Steg, die Seite wird zeilenweise gelesen. `main` verhält sich dort gleich. Der echte Workflow entzerrt vorher und liest beide Seiten spaltenweise.

**Split-Baselines:** 61–64 Vollbreite-Zeilen falsch, Median 94,8–94,9 %; derselbe Kopfschnitt wie in Nachtrag 19.

**Native ungeteilte OCR:** keine Alternative. Apple verschränkt 5 Seiten, Tesseract 8.

### Alte 16 Seiten mit der Korrektur

`unsplit-paddle` neu gelaufen; die übrigen Workflows sind die Läufe aus Nachtrag 19.

| Workflow | Median | Mittel | Min | verschränkte Seiten | Vollbreite falsch / doppelt / fehlt (von 77) | nicht gefunden (von 1348) | Seitenprüfung fehlgeschlagen |
|---|---:|---:|---:|---:|---|---:|---:|
| `unsplit-paddle` (Nachtrag 19) | 99,7 % | 99,0 % | 94,9 % | 1 | 18 / 0 / 0 | 5 | 0 |
| `unsplit-paddle` (Korrektur) | 100,0 % | 100,0 % | 99,9 % | 0 | 0 / 0 / 0 | 5 | 0 |
| `order_lines` (Nachtrag 19) | 99,7 % | 99,4 % | 97,3 % | 1 | 10 / 0 / 0 | 0 | – |
| `order_lines` (Korrektur) | 100,0 % | 100,0 % | 99,9 % | 0 | 0 / 0 / 0 | 0 | – |

Das Gate meldet hier „unsplit is supported“. Weil die Korrektur an diesen Seiten entstand, belegt das keine Verallgemeinerung.

### Einschränkungen und nächster Schritt

- **Keine Nachjustierung auf den neuen Seiten:** Die Ursache auf n07 ist bekannt (Spaltenpaar im ganzen oberen Fußband statt je Zeile suchen), aber nicht behoben. Eine Korrektur entstünde an n07 und bräuchte wieder eigene, ungesehene Seiten.
- **Metrik:** Kurze, im Text wiederholte Zeilen (Einzelwörter, gleiche Fundstellen) können Verschränkungen vortäuschen. Das Gate zählt sie trotzdem; die Gate-Kriterien bleiben unverändert.
- **Fehlende Seitenart:** Eine Vollbreite-Überschrift zwischen zwei Spaltenbereichen ist weiterhin nur synthetisch getestet.
- **Laufzeit:** `unsplit-paddle` brauchte 146 s für die 13 neuen und 182 s für die 16 alten Seiten, `split-paddle` 140 s für die neuen; nur ein Richtwert, gemessen wird in Schritt 5.

## Nachtrag 2026-09-21 (21): Struktur-Referenzkorpus für die Zusammenbau-Schicht (Issue #20)

`bench/structure_bench.py`, Wahrheit in `bench/structure_truth.json`. 20
handgeprüfte Vektorseiten quer durch alle Layouts: Einspalter, Zweispalter,
Fußnotenblöcke (ein- und zweispaltig), tiefe Gliederung (bis Ebene 6),
Randmarken in beiden Bauformen (ausgerückt und inline), Tabellen (6×3,
DSL-Vergleich, QA-Raster), Diagrammseiten (linearisiert, mit Bild-Callout
in der echten Ausgabe) und Trennstrich-Auflösung. Gemessen wird Struktur,
nicht Wörter: Überschriften (Anzahl und Ebene), Absatzgrenzen, korrekt
zugeordnete Fußnoten, Reihenfolge auf Absatzebene — als exakte
Fingerabdrücke (SHA-1 über normalisiertem Text), nicht als Text: kein
urheberrechtlich geschütztes Material im Repo, dieselbe Regel wie
`BENCHMARK-SET.md` und `reading_order_truth.json`.

Stand: **20 von 20 Seiten bestehen.** Der Lauf braucht kein Modell
(Vektorseiten, Textlayer-Pfad) und wiederholt sich über den Seiten-Cache
aus #11; fünf aufeinanderfolgende Läufe liefern byte-identische Kandidaten.

### Drei Fehler, gefunden durch das Handprüfen

Jeder wurde erst repariert, dann geblesst — die Referenzen enthalten keinen
einzigen bekannten Fehler:

1. **Wortverschmelzung an Span-Grenzen** (`conversion.textlayer_lines`):
   `clean_text` entfernte nachstehende Leerzeichen jedes Spans, bevor die
   Naht gelesen wurde — aus `Gläubigerrechte stärken` wurde
   `Gläubigerrechtestärken`, aus `**Reformziel:** Die` wurde
   `**Reformziel:**Die`. Jetzt stammt der Randabstand aus dem Roh-Span.
2. **Klausur-Boilerplate** (`assembly.is_boilerplate`): `Klausurenkurs/…`,
   `Lösung - Klausur Nr. N, Seite M` und `Klausur Nr. N - Lösung, Seite M`
   standen als eigene Absätze in jeder Ausgabe. Zwei verankerte Muster.
3. **Phantom-Fußnoten im Splitter** (`assembly.split_footnote_defs`): die
   Trennung nach jedem Punkt/Leerzeichen zerlegte Zitate — aus
   `Kopp/Ramsauer, § 35 VwVfG, Rn. 18` wurden die Definitionen 8 (gekürzt)
   und 35 (Phantom). Jetzt trennt nur Satzzeichen plus Leerzeichen, und
   ein Zitatwort davor (inklusive römischer Ziffern) schützt die Zahl.
   Regression über alle 1404 Vektorseiten (`bench/regress_footnote.py`):
   **1026 unverändert, 378 neu gruppiert, 294 Phantom-Doppelpunkte weg,
   0 Zeichen verloren.**

### Bewusst nicht enthalten

- **Scanseiten** (Kachelschnitt): Die OCR-Ausbeute schwankt zwischen Läufen
  leicht — exakte Fingerabdrücke würden flakken. Die Naht-Entdopplung ist
  über `pdf2md/test/test_seam.py` abgedeckt.
- **Drei Zweispalter ohne erkennbaren Steg** (2131_Lösung S. 1/4, 2135_Lösung
  S. 1): `_column_gap` findet dort keinen Spalt, die Spalten verschränken
  sich zeilenweise. Als Referenz unbrauchbar — das ist offener Fehler #12,
  kein Korpusfall. Ersetzt durch Seiten mit gefundenem Steg.
- **Bekannte Restfehler mit Vermerk** statt Austausch: linearisierte
  Diagrammseiten (Box-Reihenfolge), ein in Fußnote 33 eingehängter
  Fließtext-Rest, zwei am Fließtext klebende Langüberschriften. Die
  `note`-Felder im Truth-File benennen sie; jede künftige Reparatur bricht
  gezielt diese Fingerabdrücke.
