# Roadmap: vom Script-Bündel zum Obsidian-Plugin

Dieses Dokument hält fest, was ein Plugin sein soll, was der aktuelle Code dafür
schon hergibt und wo die echten Hürden liegen. Es ist ein Arbeitsstand, kein
Versprechen.

## Was das Plugin können soll

Ein Nutzer legt eine gescannte PDF in den Vault, klickt im Kontextmenü auf
„OCR → Markdown" und bekommt eine lesbare, durchsuchbare `.md` daneben — mit
Rücksprung-Link auf das Original. Kein Terminal, kein venv, kein Flag-Raten.

Realistischer Funktionsumfang v1:

- Kontextmenü-Eintrag auf PDF-Dateien im File-Explorer
- Fortschrittsanzeige (Seite n von m) — bei 15–60 s/Seite ist das Pflicht
- Ergebnis als `.md` in einem konfigurierbaren Zielordner
- Einstellungen: Engine, DPI, Zielordner, Pfad zur lokalen Installation
- Abbruch-Button, der den Kindprozess wirklich killt

## Die zentrale Architekturfrage

Obsidian-Plugins sind TypeScript in Electron. Diese Pipeline ist Bash + Python +
MLX + Ghostscript + Tesseract. **Das lässt sich nicht bundeln.** Drei Wege:

### A · Thin Client über lokale Installation (empfohlen für v1)

Das Plugin ruft die installierten CLIs per `child_process.spawn` auf und parst
deren stdout für den Fortschritt. Die Pipeline bleibt genau der Code in diesem
Repo.

- **Dafür:** Sofort machbar. Kein Reimplementieren. Alle Bugfixes an der
  Pipeline kommen dem Plugin automatisch zugute.
- **Dagegen:** Nur Desktop (`child_process` gibt es auf Mobile nicht). Der
  Nutzer muss vorher `install.sh` und `pdf2md/setup.sh` laufen lassen. Der
  Obsidian-Community-Store nimmt Plugins, die auf externe Binaries angewiesen
  sind, nur mit klarer Kennzeichnung — für ein privates Plugin egal.
- **Nötige Arbeit an diesem Repo:** die Scripts müssen maschinenlesbaren
  Fortschritt ausgeben (`--json`-Flag oder eine Zeile `PROGRESS 7/20` auf
  stderr). Aktuell ist der Output auf Menschen ausgelegt (Emoji, deutsche
  Sätze). Das ist die konkreteste offene Aufgabe.

### B · Sidecar-Daemon

Ein kleiner lokaler HTTP-Server (Python, aus `pdf2md/`), den das Plugin startet
und per `fetch` bedient.

- **Dafür:** Modell bleibt zwischen Aufträgen geladen — die 1,6 s Ladezeit
  fallen nur einmal an. Sauberer Fortschritt über SSE. Vorstufe zu „läuft auf
  dem Mac, bedient wird vom iPad".
- **Dagegen:** Prozess-Lebenszyklus, Port-Konflikte, Zombie-Prozesse beim
  Obsidian-Absturz. Deutlich mehr Code für wenig Mehrwert in v1.

### C · Reimplementierung in TypeScript/WASM

- **Dagegen:** PaddleOCR-VL über MLX gibt es nicht in WASM, und Tesseract.js ist
  spürbar schlechter als die native Variante. Die gemessenen Ergebnisse in
  `bench/ERGEBNIS.md` wären hinfällig. Kein Weg.

**Entscheidung:** A für v1, B als Option, sobald Stapelverarbeitung über viele
Dateien der Normalfall wird.

## Was schon plugin-tauglich ist

- `pdf2md.py` schreibt bereits Frontmatter mit `seiten-textlayer` /
  `seiten-ocr` / `seiten-diagramm` und einem `Quelle:`-Link — genau das
  Metadatenmodell, das ein Plugin in der Obsidian-UI anzeigen würde
  (siehe [ocr-vorschau.md](ocr-vorschau.md)).
- `--out <ordner>` existiert schon, das Ziel ist also konfigurierbar.
- Die Trennung „Vorschau-Ordner ≠ Wiki" ist bereits gedacht und dokumentiert.
- Diagrammseiten kommen als Bild + eingeklappter Callout — Obsidian-Syntax, kein
  Nachbau nötig.

## Was fehlt

| Aufgabe | Warum | Aufwand |
|---|---|---|
| Maschinenlesbarer Fortschritt aus den Scripts | ohne das keine Fortschrittsanzeige | klein |
| Sauberer Exit-Code je Fehlerklasse | Plugin muss „fehlende Abhängigkeit" von „OCR schlug fehl" unterscheiden | klein |
| Preflight-Check als eigenes Kommando | Plugin-Settings will „Installation ok?" anzeigen können | klein |
| Abbruchbarkeit (SIGTERM sauber behandeln, Temp aufräumen) | 30-Minuten-Läufe müssen abbrechbar sein | mittel |
| Zusammenbau-Schicht stabilisieren | jüngste Komponente, Trennstriche/Leseordnung/Sperrschrift | groß |
| Fußzeilen-Erkennung bei Kachelschnitt | Vollbreiten-Elemente werden am Schnitt abgeschnitten | mittel |

Die ersten drei Punkte sind zusammen ein Nachmittag und machen aus dem Repo eine
plugin-fähige Schnittstelle. Der große Brocken ist die Zusammenbau-Schicht — die
entscheidet über die wahrgenommene Qualität, nicht die OCR-Engine.

## Offene Fehler

| | Was | Gewicht |
|---|---|---|
| ~~1~~ | ~~**Entgleiste Seiten** — 15 % laufen in Schleifen oder brechen ab~~ | **erledigt**, 6 von 6 abgefangen |
| ~~2~~ | ~~**Lesereihenfolge** `Klausur_2137` S. 7 (47,5 %)~~ | **erledigt**, Seite jetzt 96,1 % |
| 3 | **Diagrammseite ohne Bild** — `Strafrecht AT VI` S. 8, kein Rückfall auf das Seitenbild | mittel, Behelf `--diagramm-seiten 8` |
| 4 | **Ein Zweispalter zu wenig** — `Verwaltungsrecht AT Fall 8` S. 10, flacher Steg | bewusst gewählt (1 von 14) |
| 5 | **Verschränkte Fußnotenblöcke** in 2131/2135/2143 | klein, dort auch der Restverlust von 1–2 Zeichen |
| 6 | **Fußnotentext über den Seitenumbruch** wird abgeschnitten | klein |
| ~~7~~ | ~~**`**Beispiel:**` mitten im Satz**~~ | **erledigt**, beide Bauformen |
| 8 | **Wortfehler** — jetzt beziffert: 1,2 % über alle 40 Seiten | gering |
| 9 | **Mehrspaltige Lesereihenfolge** — `2131_Lösung` S. 4 liegt bei 49,7 % | neu, jetzt der größte Einzelposten |

Zu Punkt 7: die Randmarke hatte **zwei** Bauformen, und nur eine war bekannt.
Ausgerückt in den linken Rand (Hemmer-Skripte) → `randlabel_vorziehen()` holt
sie an den Blockanfang. Als Vorspann derselben Zeile → sie galt als Überschrift
und riss den Satz ab; `ist_ueberschrift()` nimmt sie jetzt aus. Der
`**A.**`-Teil desselben Punktes war kein Fehler: die Marker tragen ihren Titel
hinter sich, das ist korrektes Markdown.

Dazu ungeprüft: **~140 Scanseiten mit unter 50 Zeichen im alten Textlayer**.
Unklar, ob dort Inhalt fehlt oder die Seiten leer sind.

## Noch nicht gebaut

**Der LLM-Reparaturlauf** liegt auf Eis. Bei 98,5 % Wortgenauigkeit über alle
Seiten steht der Ertrag nicht mehr gegen das Risiko, ein korrektes Normzitat zu
„verbessern". Falls er doch kommt: der Benchmark misst ihn jetzt, und die
Messlatte heißt **92,4 % Zitattreue** — er darf sie nicht senken.

**Der lokale Wörterbuchabgleich** (hunspell + juristische Begriffsliste) — aus
demselben Grund entwertet, aber als Prüfhilfe weiter brauchbar.

**Die Migration.** 701 `[[raw/…pdf]]`-Wikilinks zeigen noch auf die PDFs. Wartet
auf die Entscheidung, wohin die Originale wandern — ohne sie sind die `.md` bei
OCR-Seiten nicht belastbar.

**Kleinkram:** `pages.json` gehört in `.gitignore` — in diesem Repo erledigt
(`bench/pages.json`), im Vault noch offen.

## Vorgezogen: die Abgleich-Ansicht (v0.1)

Dieser Abschnitt hält eine bewusste Abweichung fest, die nicht
stillschweigend überholt werden soll. Details zur Ansicht selbst:
[review-ansicht.md](review-ansicht.md).

Der Reihenfolge unten liegt das Plugin-Skelett als **Schritt 5** zugrunde.
Gebaut wurde aber zuerst etwas anderes, mit anderem Umfang:

- **Anderer Umfang:** Die Abgleich-Ansicht **liest nur** — keine
  Konvertierung, kein `spawn`, kein Fortschrittsmodal. Sie zeigt die von
  Stufe 2 erzeugten `.md`-Dateien seitenweise neben dem Original-PDF und
  verschiebt sie per **Annehmen / Ablehnen** zwischen drei Ordnern. Von
  „Was das Plugin können soll" oben ist nichts davon im Bau.
- **Warum vorgezogen:** Die 15 % entgleisten Seiten (siehe „Offene Fehler",
  Nr. 1) erzwingen heute schon einen Menschendurchgang — Datei und PDF in
  zwei Fenstern, von Hand abgleichen. Genau dieser Durchgang hat keine
  Oberfläche, und er ist der kritische: Er ist das Werkzeug, mit dem sich die
  Entgleisungen überhaupt auffinden lassen.
- **Architektur:** Die Ansicht ist **Weg A ohne den spawn-Teil** — sie ruft
  nur Obsidians eigene PDF.js-Bibliothek (`loadPdfJs`), keinen Kindprozess.
  An der A/B/C-Entscheidung oben ändert sich nichts; B und C bleiben unberührt.
- **Die drei Schnittstellen-Aufgaben bleiben unberührt:** Maschinenlesbarer
  Fortschritt, Exit-Codes, Preflight — nichts davon ist durch die Ansicht
  erledigt oder überflüssig geworden. Sie sind weiterhin offen, wenn das
  eigentliche Plugin (Schritt 5) gebaut wird.

Einzige Berührung mit der Pipeline: `pdf2md.py` schreibt die Seitenherkunft
jetzt in den Marker (Vertrag: `docs/ocr-vorschau.md`, „Marker-Grammatik").
Nicht brechend — die alte Form `%% S. n %%` wird weiterhin gelesen.

## Gebaut danach: der Konvertierungs-Befehl (v0.2)

Der erste Weg-A-Baustein steht: der Befehl **„PDF konvertieren und im
OCR-Abgleich öffnen"**. Er wählt per Suggest-Modal eine PDF aus dem Vault,
startet `~/bin/pdf2md <pdf> --out <vorschau-Ordner>` per `child_process.spawn`
und öffnet den Abgleich mit dem Ergebnis. Rückmeldung bewusst nur per Notice.

Bewusst **nicht** enthalten (bleiben offen, siehe „Was fehlt"): Fortschritts-
anzeige, Abbruch-Button, maschinenlesbarer Fortschritt, Preflight, der
Kontextmenü-Eintrag auf PDF-Dateien und ein konfigurierbarer pdf2md-Pfad.

## Reihenfolge

1. ~~**Entgleisungserkennung**~~ — erledigt, 93,3 % → 98,5 %.
2. **Offener Fehler 9** — mehrspaltige Lesereihenfolge (`2131_Lösung` S. 4).
   Der größte verbliebene Einzelposten, und der Steg-Code ist frisch angefasst.
3. **Offener Fehler 3** — Diagramm-Rückfall aufs Seitenbild. Braucht zuerst eine
   Stichprobe handmarkierter Diagrammseiten; ohne die verschiebt jede Änderung
   an `ist_diagramm()` nur Wahrscheinlichkeiten.
4. **Scripts plugin-fähig machen** — Fortschritt, Exit-Codes, `--check`.
5. **Zusammenbau-Schicht härten** — an einem Korpus von 20 Seiten mit
   handgeprüfter Referenz, nicht nach Gefühl. Das Harness dafür steht in `bench/`.
6. **Plugin-Skelett** — TypeScript, esbuild, Kontextmenü, `spawn`,
   Fortschrittsmodal. `~/Developer/ask-my-notes` ist die vorhandene Vorlage.
   *Die Abgleich-Ansicht (v0.1) ist bereits gebaut — siehe den Abschnitt
   „Vorgezogen" oben; sie ersetzt diesen Schritt nicht, sie enthält nur
   keinen seiner Teile.*
7. **Settings-Tab** mit Preflight-Anzeige.
8. Erst dann über Sidecar (Weg B) und Mobile nachdenken.

## Nicht-Ziele

- Kein Cloud-OCR. Die Quellen sind Kursmaterial; sie verlassen die Maschine nicht.
- Kein automatisches Überschreiben von Wiki-Seiten. Das Plugin erzeugt
  Vorschau-Dateien; die Übernahme ins Wiki bleibt ein bewusster Schritt.
- Kein Anspruch auf fehlerfreie Ausgabe. Der Rücksprung-Link aufs Original ist
  Teil der Architektur, nicht ein Zugeständnis.
