# OCR-Vorschau — kein Wiki-Inhalt

Rohkonversionen von `raw/`-PDFs nach Markdown, erzeugt von
`.ocr-bench/pdf2md.py`. Zweck: **Begutachtung in Obsidian**, nichts weiter.

Dieser Ordner folgt **nicht** den Wiki-Konventionen aus `AGENTS.md`:

- kein Frontmatter-Pflichtschema (`titel`, `typ`, `rechtsgebiet`, …)
- keine `[[wikilinks]]` auf Wiki-Seiten
- wird von `lint_wiki.py` nicht erfasst und **soll** es nicht werden
- wird von `semantic_search.py` nicht indiziert (das liest nur `wiki/*.md`)

Wer daraus Wiki-Seiten machen will, nimmt `/jura-ingest` — nicht diese Dateien
verschieben.

## Marker-Grammatik (Vertrag)

`pdf2md.py` trennt Seiten durch eine Kommentarzeile, die Obsidian ohnehin
versteht:

```
%% S. {nr} | {herkunft} | {layout} %%
```

| Teil | Bedeutung | erlaubte Werte |
|---|---|---|
| `nr` | Seitennummer im PDF, 1-basiert | Zahl |
| `herkunft` | Woher der Text stammt | `textlayer` \| `ocr` \| `diagramm` |
| `layout` | nur bei OCR-Seiten, optional | z.B. `zweispaltig, senkrecht @48%`, `waagerecht`, `ganz` |

Regeln für alle, die diese Dateien erzeugen **oder lesen**:

1. `diagramm` sticht: eine Seite, deren Text als Bild eingebettet wird, ist
   keine Textseite — egal ob der Textlayer sie hätte liefern können.
2. Die alte Form `%% S. n %%` ohne Zusatz bleibt gültig und wird von der
   Review-Ansicht weiterhin gelesen — dort fehlt dann nur das Herkunfts-Badge.
3. Leser raten **nie**: unbekannte oder kaputte Zusätze werden als Layout
   durchgereicht, nicht als Herkunft interpretiert.
4. Ein `%% S. n %%` innerhalb eines Codeblocks ist **keine** Seitengrenze.
5. Andere Konsumenten als die Review-Ansicht gibt es nicht: weder
   `lint_wiki.py` noch `semantic_search.py` fassen diesen Ordner an.

## Das Drei-Ordner-Modell

Die Dateien wandern zwischen drei flachen Geschwisterordnern — die
Ordnerlage **ist** der Status:

```
_ocr-vorschau/            offen (noch zu begutachten)
_ocr-vorschau/_akzeptiert/  angenommen
_ocr-vorschau/_abgelehnt/   abgelehnt (es wird nichts gelöscht)
```

`review-status.json` im selben Ordner ist nur ein **Cache mit Anmerkungen**
(`notiz`, `geprüft-bis`, manuelle PDF-Zuordnung) und darf jederzeit gelöscht
werden. Details zu den Abgleichregeln: [review-ansicht.md](review-ansicht.md).

`_ocr-vorschau/assets/` bleibt wo es ist: Die drei Ordner teilen sich die
Diagrammbilder (`![[…png]]`), sie werden **nicht** mitverschoben.

## Was im Frontmatter steht

| Feld | Bedeutung |
|---|---|
| `seiten-textlayer` | verlustfrei aus dem PDF-Textlayer, exaktes Fett |
| `seiten-ocr` | durch PaddleOCR-VL gelesen — **Wortfehler möglich** |
| `seiten-diagramm` | als Seitenbild eingebettet, Text im eingeklappten Callout |

Bei `seiten-ocr > 0` ist der `Quelle:`-Link aufs Original die Absicherung:
Wortfehler des OCR sind nicht mechanisch korrigierbar.

## Neu erzeugen

```
source .venv-mlxocr/bin/activate
python pdf2md/pdf2md.py "raw/ZR/…/datei.pdf" --out _ocr-vorschau
```

Entgleist eine Kachel (Wiederholungsschleife, davonlaufender Zähler, Abbruch),
wird sie feiner geschnitten neu gerechnet. Jeder solche Fall steht als
`⚠`-Zeile im Protokoll des Laufs — auch dann, wenn die Reparatur *nicht*
gelungen ist. Wer nur die Erkennung will und keine Neuberechnung:

```
python pdf2md/pdf2md.py "raw/…/datei.pdf" --out _ocr-vorschau --neuversuche 0
```

Messwerte und Fehlerklassen: [../bench/ERGEBNIS.md](../bench/ERGEBNIS.md).
