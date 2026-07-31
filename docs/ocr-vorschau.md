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
python .ocr-bench/pdf2md.py "raw/ZR/…/datei.pdf" --out _ocr-vorschau
```

Messwerte und Fehlerklassen: `.ocr-bench/ERGEBNIS.md`.
