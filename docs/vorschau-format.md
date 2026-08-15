# Vorschau-Format — Normative Spezifikation

_Dies ist die authoritative Beschreibung des `.md`-Formats, das `pdf2md.py`
erzeugt und das Plugin `vorschau-parser.ts` liest. Ab sofort beide
Implementierungen verweisen darauf statt auf Zeilennummern der jeweils
anderen Datei._

## 1. Frontmatter

Das YAML-Frontmatter (abschliessend mit `---`) enthält Metadaten zur PDF und
zur Erzeugungslauf. Jedes Feld ist ein flaches Schlüssel-Wert-Paar.

| Feld | Bedeutung | Format / erlaubte Werte |
|---|---|---|
| `titel` | Kurzer Titel (oft PDF-Name) | String |
| `quelle-pdf` | Pfad zur rohen PDF-Datei | String, immer JSON-gequotet via `json.dumps` |
| `seiten` | Gesamtzahl der Seiten | positive ganze Zahl (als String) |
| `seiten-textlayer` | Anzahl Seiten mit verlustlosem Textlayer | ganze Zahl |
| `seiten-ocr` | Anzahl Seiten, die durch das Modell gelesen wurden | ganze Zahl |
| `seiten-diagramm` | Anzahl Seiten, die als Diagramm erkannt wurden | ganze Zahl |
| `seiten-entgleist` | Anzahl entgleister Kacheln (optional) | ganze Zahl |
| `woerter-verdaechtig` | Anzahl Wörter im Wörterbuch-Abgleich (optional) | ganze Zahl |
| `woerter-korrigiert` | Anzahl Wörter, die ersetzt wurden (optional) | ganze Zahl |
| `ocr-modell` | Modellbezeichner, das für OCR-Seiten genutzt wurde | String (z.B. `mlx-community/PaddleOCR-VL-1.5-4bit`) |
| `abgebrochen` | Abbruchvermerk einer Teildatei (optional, nur bei geordnetem Abbruch, Issue #25) | `seite n von m` — `n` = letzte fertige Seite, `m` = geplante Gesamtzahl |
| `ocr-datum` | Kalenderdatum des Laufs (ISO) | `YYYY-MM-DD` |
| `ocr-zeitpunkt` | Feingranularer Zeitpunkt ISO (mit Uhrzeit) | `YYYY-MM-DDTHH:MM:SS` |
| `vorschau-format` | Versionsfeld der Format-Spezifikation (neu) | `1` |

### Regeln

1. `quelle-pdf` wird immer via `json.dumps` quotiert (auch ohne Leerzeichen). Der Parser liest es als flachen Skalar — ein YAML-Parser ist bewusst nicht nötig.
2. Alle Felder sind optional; Fehlende werden vom Parser als `undefined` / `null` behandelt.
3. Das Feld `vorschau-format: 1` kennzeichnet Dateien, die nach dieser Spezifikation erzeugt wurden. Der Parser toleriert unbekannte Felder (siehe Abschnitt 7).
4. `abgebrochen` kennzeichnet eine **Teildatei** aus einem geordneten Abbruch
   (SIGINT/SIGTERM, Exit-Code 6): die Datei ist unvollständig, aber bewusst
   geschrieben statt verworfen. Die Zählfelder (`seiten`, `seiten-ocr`, …)
   beziehen sich dann nur auf die tatsächlich geschriebenen Seiten; `m` im
   Vermerk ist die geplante Gesamtzahl des Laufs.

## 2. Seitenmarker

Jede Seite wird durch eine Kommentarzeile getrennt, die Obsidian im
Rendern unsichtbar lässt. Der Marker erlaubt ein optionales Zusatzfeld
(`herkunft` und `layout`), damit Dateien aus Läufen vor der Marker-Erweiterung
weiter funktionieren.

```
%% S. {nr} | {herkunft} | {layout} %%
```

| Teil | Bedeutung | Erlaubte Werte / Regeln |
|---|---|---|
| `nr` | Seitennummer im PDF, 1-basiert | Positive ganze Zahl |
| `herkunft` | Woher der Text stammt | `textlayer` \| `ocr` \| `diagramm` |
| `layout` | Nur bei OCR-Seiten, optional | z.B. `zweispaltig, senkrecht @48%`, `waagerecht`, `ganz` |

### Regeln für Erzeuger und Leser

1. `diagramm` sticht: Eine Seite, deren Text als Bild eingebettet wird, ist
   keine Textseite — egal ob der Textlayer sie hätte liefern können.
2. Die alte Form `%% S. n %%` ohne Zusatz bleibt gültig und wird von der
   Review-Ansicht weiterhin gelesen — dort fehlt dann nur das
   Herkunfts-Badge.
3. Leser raten **nie**: unbekannte oder kaputte Zusätze werden als Layout
   durchgereicht, nicht als Herkunft interpretiert.
4. Ein `%% S. n %%` innerhalb eines Codeblocks ist **keine** Seitengrenze.
5. Eine Formatänderung in `pdf2md.py` ohne aktualisierte Fixture macht CI rot
   (siehe CI-Schritt in `.github/workflows/ci.yml`).

## 3. `Quelle:`-Zeile

Direkt nach dem Frontmatter (erste Zeile ohne `---`):

```
Quelle: [[raw/ZR/skript.pdf]]
```

- Pfad ist der basename der PDF-Datei (relativ zum `raw/`‑Verzeichnis).
- Der Parser extrahiert diesen Link und nutzt ihn als Fallback für
  `quellePdf`, falls `quelle-pdf` im Frontmatter fehlt.

## 4. Diagramm-Callout

Seiten, die als Diagramm markiert sind (`herkunft: diagramm`), erhalten
zusätzlich ein eingeklapptes Callout mit dem Seitentext.

```
> [!note]- Text der Seite (Reihenfolge nicht verlässlich)
> Prüfungsaufbau Anfechtungsklage
> >
> > 1. Zulässigkeit
> > 2. Begründetheit
```

- Der Titel `Text der Seite (Reihenfolge nicht verlässlich)` ist fest.
- Der Callout-Text entsteht aus den Absätzen der Seite (via
  `als_callout(absaetze, "Text der Seite (Reihenfolge nicht verlässlich)")`).
- Auf diagramm-Seiten mit `--diagramm-nur-bild` entfällt der Callout.

## 5. Fußnoten pro Seite

- Fußnoten erscheinen als Obsidian-Syntax: `[^1]` im Text, `[^1]: ...` am
  Blockende.
- Sie werden pro Seite gesammelt (blockweises Rendering verhindert Kollision
  gleichlautender Fußnotennummern über Seiten hinweg).
- Das Format entsteht via `fussnoten_obsidian(absaetze)` in
  `zusammenbau.py`.

## 6. Codezaun-Sonderfall

Ein Marker `%% S. n %%` innerhalb eines Codeblocks (` ``` ` oder `~~~ `) wird
**nicht** als Seitengrenze gewertet. Der Inhalt des Codeblocks bleibt
unberührt.

```
```
%% S. 99 %%
noch immer Seite 2
```
```

## 7. Versionsfeld (vorschau-format)

- Feld `vorschau-format: 1` im Frontmatter kennzeichnet Konformität mit dieser
  Spezifikation.
- Der Parser liest es; bei fehlendem oder unbekanntem Wert wird **nicht**
  fehlerhaft abgebrochen, sondern das Feld fehlt einfach (`undefined`).
- Das Feld ist aktuell **reserviert**: es wird geschrieben, aber noch von
  keinem Konsumenten ausgewertet. Eine spätere Migrationsprüfung („Datei
  stammt aus einem älteren Format") kann daran ansetzen.
- Bestehende Dateien ohne dieses Feld bleiben gültig (es wird einfach als
  nicht vorhanden behandelt).