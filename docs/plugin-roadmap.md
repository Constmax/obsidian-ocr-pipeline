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

## Reihenfolge

1. **Scripts plugin-fähig machen** — Fortschritt, Exit-Codes, `--check`.
2. **Zusammenbau-Schicht härten** — an einem Korpus von 20 Seiten mit
   handgeprüfter Referenz, nicht nach Gefühl. Das Harness dafür steht in `bench/`.
3. **Plugin-Skelett** — TypeScript, esbuild, Kontextmenü, `spawn`,
   Fortschrittsmodal. `~/Developer/ask-my-notes` ist die vorhandene Vorlage.
4. **Settings-Tab** mit Preflight-Anzeige.
5. Erst dann über Sidecar (Weg B) und Mobile nachdenken.

## Nicht-Ziele

- Kein Cloud-OCR. Die Quellen sind Kursmaterial; sie verlassen die Maschine nicht.
- Kein automatisches Überschreiben von Wiki-Seiten. Das Plugin erzeugt
  Vorschau-Dateien; die Übernahme ins Wiki bleibt ein bewusster Schritt.
- Kein Anspruch auf fehlerfreie Ausgabe. Der Rücksprung-Link aufs Original ist
  Teil der Architektur, nicht ein Zugeständnis.
