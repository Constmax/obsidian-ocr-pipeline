# PP-DocLayoutV3 als Vorverarbeitung — Messung

**Stand: gemessen (2026-08-14).** Die Zahlen unten stammen aus dem ersten Lauf
auf dem M1 im Vault. Ergebnisse gehören als Nachtrag auch nach `ERGEBNIS.md` —
dort stehen nur Zahlen, die jemand gesehen hat.

## Die Frage

Die Spaltenentscheidung ist die riskanteste Einzelentscheidung der Pipeline.
Ein **falscher** Längsschnitt zerlegt jede Zeile der Seite in zwei Hälften und
ist ohne das Original nicht zu rekonstruieren; ein **versäumter** kostet nur
Reihenfolge (Nachtrag 12). Sie fällt heute über ein Tintenprofil
(`layout.py:304`, `layout_erkennen()`) und trifft 13 von 14 handgeprüften
Seiten.

Warum nie ein Layoutmodell geprüft wurde, ist historisch und kein Argument:
Pfad A ist 2026-07 als **PaddlePaddle-Framework** ausgeschieden — 9.226 s/Seite,
5.679 MB Peak-RSS, durchgehend im Swap auf 8 GB. Der Layoutdetektor flog dabei
ungeprüft mit heraus. Inzwischen gibt es die Gewichte als safetensors, und
`transformers` fährt sie ohne PaddlePaddle.

Dazu kommt: PP-DocLayoutV3 ist die **offizielle erste Stufe von PaddleOCR-VL
1.5/1.6**. Wir fahren das VLM also ohne die Vorstufe, mit der zusammen es
ausgewertet wurde.

## Was geprüft ist

Gegen den Quelltext von `transformers` 5.15.0 (`models/pp_doclayout_v3/`)
nachgesehen, nicht aus der Erinnerung:

| | |
|---|---|
| Native Unterstützung | ja, ab `transformers` 5.15.0 — **kein PaddlePaddle** |
| Checkpoint | `PaddlePaddle/PP-DocLayoutV3_safetensors` |
| API | `AutoImageProcessor` + `AutoModelForObjectDetection`, dann `post_process_object_detection(outputs, target_sizes=…)` |
| Rückgabe | `scores`, `labels`, `boxes` (xyxy absolut), `polygon_points`, `order_seq` |
| Leseordnung | wird **mitgeliefert** und die Treffer sind danach sortiert |
| Eingabegröße | hart auf **800 × 800** (`size` im ImageProcessor), `mean=[0,0,0]`, `std=[1,1,1]` |
| Zusatzabhängigkeit | `torch`, `torchvision` (hatter Import im ImageProcessor) **und** `cv2` (für die Polygonpunkte) |
| Klassen (im Beispiel belegt) | `text`, `paragraph_title`, `footnote`, `number`, `footer` — vollständige Menge steht in `model.config.id2label` |

Die feste Eingabegröße ist dieselbe Sorte Deckel wie die 1.003.520 Pixel des
VLM, die 150 dpi zur Voreinstellung gemacht haben: **oberhalb ~110 dpi bringt
höheres Rendern dem Detektor nichts.** Deshalb ist 110 dpi die Voreinstellung
des Messstands — nicht die 150 der Pipeline.

Die Leseordnung im selben Forward-Pass ist der zweite bemerkenswerte Punkt:
genau das rekonstruiert `spalten_trennen()` heute aus Geometrie, rekursiv bis
Tiefe 2, mit einem Sonderweg für den WuV-Doppelbogen.

## Was aus fremder Quelle stammt

Nicht selbst nachgemessen, aus der Modellkarte und dem Paper — vor jeder
Bauentscheidung gegen den eigenen Lauf prüfen:

- ~33 M Parameter, ONNX-Datei ~124 MiB
- CPU-Inferenz ~760 ms/Seite (≈1,3 FPS), GPU ~132 FPS
- RT-DETR-Familie, Mehrpunkt-Boxen für **nicht-planare** Seiten (schräg, gewölbt)

Wenn die 760 ms auf dem M1 ungefähr stimmen, sind das gegen ~33 s/Seite Inferenz
rund **2 % Aufschlag** — und der Detektor läuft nur auf dem Rasterpfad. Die
48,8 % vektoriellen Seiten haben den Satz exakt; dort wäre ein Detektor ein
Rückschritt.

## Wo es die offenen Punkte träfe

Die Fehlerklassentabelle in `ERGEBNIS.md` nennt die Layout-Vorstufe selbst als
geplanten Fix: *„Vollbreiten-Elemente | Fußzeile am Kachelschnitt abgeschnitten
| ja, über Layout-Vorstufe"*. Dazu:

| offener Punkt | Beleg | passende Klasse |
|---|---|---|
| gescannte Diagramme nicht erkannt | Nachtrag 6 — vier Signale gemessen, alle vier versagt, bewusst zurückgenommen | `image` / `chart` (erster `--dump`: Baumdiagramm kam als `image` 0,92 heraus — es gibt kein `figure`) |
| Tabellen in Rasterscans | Nachtrag 3 — gar nicht erkannt | `table` |
| Kästen in Rasterscans | Nachtrag 6 — Rahmenprüfung nicht trennbar (Prosa 0,95 gegen Kästen 0,95) | `text`-Regionen |
| Seitenzahlen auf Scanseiten | Nachtrag 9 — 4 bleiben stehen | `number` / `footer` |
| Schräglage | Nachtrag 12 — Kante wandert über Dutzende Pixelzeilen | Mehrpunkt-Boxen |

## Der Messstand

`bench/layoutmodell_test.py`. Läuft im Vault, weil die Seitenbilder
urheberrechtlich geschützte Scans sind und nicht im Repo liegen (wie
`regress_steg.py` und `bench_ocr.py` auch).

```bash
source ~/.venvs/mlxocr/bin/activate
pip install "transformers>=5.15" torch torchvision opencv-python-headless

python bench/layoutmodell_test.py --dump "raw/.../Strafrecht AT VI - Fahrlaessigkeit.pdf" 5
python bench/layoutmodell_test.py
python bench/layoutmodell_test.py --stichprobe 60
```

**Zuerst `--dump`.** Es gibt `id2label` und alle Regionen einer Seite roh aus.
Damit ist zu sehen, ob die API-Annahmen tragen und wie die Klassen wirklich
heißen; `SATZ_KLASSEN`/`RAND_KLASSEN` im Skript sind danach zu korrigieren.
Alles Modellabhängige steht in `modell_laden()` und `regionen()` — zwei
Funktionen, absichtlich. Beides ist beim ersten Lauf passiert: die API trug,
die Klassensets sind gegen die echte `id2label`-Menge nachgezogen (25 Einträge
mit Duplikaten: `footer`/`header`/`formula`/`text` je zweimal, dazu
`reference_content`/`vision_footnote`; angenommen waren u. a. `figure` und
`table_title`, die es nicht gibt).

Der Standardlauf vergleicht Modell und Heuristik gegen den Wahrheitssatz und
trennt dabei die beiden Fehlerrichtungen: *falsch geschnitten* (teuer) und
*versäumt* (billig). Eine Gesamttrefferquote allein würde die Entscheidung
verstecken — dieselbe Falle wie in Nachtrag 15, wo eine gemittelte Kennzahl
Reparatur und Regression gegeneinander aufrechnete.

`--stichprobe N` läuft ohne Wahrheit über zufällige Scanseiten und zählt nur die
**Widersprüche**. Das ist die Zahl, die über den Aufwand entscheidet: sind es
wenige, lohnt das Modell die Mühe nicht; sind es viele, müssen die Streitfälle
von Hand angesehen werden — und *dann* lohnt der Wahrheitssatz seine Erweiterung.

## Der Wahrheitssatz ist unvollständig

Die 14 handgeprüften Seiten aus Nachtrag 12 sind **nirgends als Liste
festgehalten**. Das ist selbst ein Befund: die Kalibrierung der wichtigsten
Schwellen der Pipeline ist nicht reproduzierbar.

`WAHRHEIT` im Skript ist aus `ERGEBNIS.md` und `BENCHMARK-SET.md`
rekonstruiert — 15 Seiten, jede mit ihrem Beleg. Vor dem ersten Lauf
durchsehen und ergänzen. Enthalten ist auch `Verwaltungsrecht AT Fall 8` S. 10,
der eine bekannte Fehlschlag der Heuristik (echter Zweispalter, flacher Steg:
Tal 0,40, linke Flanke 0,72) — die Seite, an der sich zuerst zeigt, ob das
Modell überhaupt etwas kann, was die Projektion nicht kann.

## Ergebnisse

Erster Lauf 2026-08-14, M1, Wahrheitssatz aus `WAHRHEIT` (15 Seiten):

| | Heuristik | PP-DocLayoutV3 |
|---|---|---|
| Wahrheitssatz richtig | **13/15** | **11/15** |
| davon falsch geschnitten | 1 | 2 |
| davon versäumt | 1 | 2 |
| Laufzeit je Seite | ~0 (Projektion) | Median **1,22 s** (0,90–1,71) |

Die Entscheidung ist damit gefallen: als **Spaltenentscheidung ist das Modell
kein Gewinn** — es verpasst `Verwaltungsrecht AT Fall 8` S. 10 genauso wie die
Heuristik (Tal 0,40, linke Flanke 0,72) und schneidet zusätzlich zwei Seiten
falsch (`BGB-AT_10` S. 3/4), die die Heuristik richtig hat.

Ende-zu-Ende-Kontrolle auf `BGB AT Fall 3.pdf` (5 Rasterseiten, je ein Lauf mit
und ohne vorgeschaltetes Modell, nur `layout_erkennen` getauscht): die Heuristik
trifft alle fünf Seiten (S. 2–5 zweispaltig @51–52 %), das Modell verpasst S. 2
(einspaltig) — die Seite wird verschränkt gelesen (Gliederung zerschnitten,
Absatzzeilen mitten im Satz gebrochen), Zeichenausbeute bleibt gleich. Auf den
übrigen Seiten ist die Ausgabe identisch bzw. der Steg verschiebt sich um
~2 %-Punkte.

Die **Klassifikationsfragen** sind damit unentschieden: der erste `--dump`
meldet das Baumdiagramm (Strafrecht AT VI S. 5) als `image` 0,92, die
Gegenprobe S. 8 aber ebenfalls ein `image` — das ist im Bild nachzusehen, bevor
daraus etwas gebaut wird. `--stichprobe` ist noch nicht gelaufen.

Danach entscheiden. Und die Entscheidung ist nicht „ersetzen oder nicht": der
Detektor kann die Spaltenfrage der Heuristik lassen und trotzdem die
Klassifikationsfragen beantworten, an denen sie nachweislich scheitert.
