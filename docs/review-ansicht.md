# Review-Ansicht (Stufe 3, v0.1)

Dreispaltige Obsidian-Ansicht zum Begutachten der OCR-Vorschau-Dateien aus
Stufe 2: Original-PDF und erzeugte Markdown-Datei seitenweise gekoppelt, mit
**Annehmen / Ablehnen** und Rückgängig. Das Plugin heißt `ocr-vorschau` und
liegt in `plugin/`.

Worum es geht: 15 % der Seiten entgleisen (Wiederholungsschleife oder Abbruch)
und drücken die Genauigkeit von 98,5 % auf 93,3 % — siehe `README.md`,
„Stand". Diese Ansicht ist das Werkzeug, mit dem sich genau diese Seiten beim
Durchgehen finden lassen, bevor etwas ins Wiki wandert.

## Die drei Spalten

| Spalte | Inhalt |
|---|---|
| **Vorschauen** | Dateiliste mit Statusfilter (Offen · Akzeptiert · Abgelehnt · Alle), Textfilter, Aktualisieren. Unter jeder Zeile `14 S. · 9 OCR · 2 Diagramm`, farbige Randmarkierung nach Status, gelber Punkt bei OCR-Seiten. Drei getrennte Leerzustände: Ordner fehlt (→ Einstellungen), Ordner leer (→ kopierbarer pdf2md-Befehl), Filter leer. |
| **Original-PDF** | Seiten der Original-PDF, lazy gerastert. Kopf mit Dateiname, `S. n / m`, Zoom −/+, „Im PDF-Viewer öffnen". |
| **Markdown** | Die erzeugte `.md`, seitenweise, mit Herkunfts-Badge (`Textlayer` / `OCR` / `Diagramm`) und Layout-Info. Umschalter **Gerendert \| Quelltext**. Oben die Entscheidungs-Knöpfe. |

Klicken auf einen Listeneintrag öffnet beide Seiten. Gescrollt wird gekoppelt:
blättert man das PDF, folgt das Markdown (und umgekehrt), bruchteilsweise statt
auf Seitenanfänge gerundet. Der Lesefortschritt (`geprüft-bis`) wird
mitgeschrieben und beim erneuten Öffnen wiederhergestellt.

## Öffnen

- Ribbon-Icon (Spalten-Icon) oder Befehlspalette: **„Abgleich-Ansicht öffnen"**
- Datei-Menü auf einer Vorschau-`.md` oder auf einer PDF mit passendem Stem:
  „Im OCR-Abgleich öffnen"
- Zweiter Befehl: **„Zum nächsten Vorschau-Eintrag springen"** (frei belegbar)

Die Ansicht übersteht `Cmd+R`: die zuletzt geöffnete Datei wird
wiederhergestellt.

## Tasten

Gilt nur, wenn die Ansicht den Fokus hat:

| Taste | Aktion |
|---|---|
| `j` / `k` | nächster / vorheriger Listeneintrag |
| `a` | **Annehmen** (in `_akzeptiert/`) |
| `x` | **Ablehnen** (in `_abgelehnt/`) |
| `t` | Gerendert ⇄ Quelltext |
| `Leertaste` | beide Spalten eine Seite weiter |
| `g` | Gehe zu Seite |
| `s` | Scroll-Synchronisation an/aus |
| `Esc` | zurück zur Liste |

## Knöpfe

- **Annehmen** (grün) / **Ablehnen** (rot): verschieben die Datei, schreiben
  das Manifest, zeigen eine **6-Sekunden-Notice mit Rückgängig** und springen
  automatisch zum nächsten passenden Eintrag.
- **In Obsidian öffnen**: die `.md` im normalen Editor.
- **⋯**: Notiz… · Alte Fassung ersetzen (nur bei `neu-erzeugt`) · Status
  zurücksetzen · Pfad kopieren.
- **PDF zuordnen…**: erscheint im Fehlerbanner, wenn kein Original gefunden
  wurde; öffnet eine Vorschlagsliste aller Vault-PDFs. Die Zuordnung landet
  im Manifest (`quelle-pdf-manuell`), nie im Frontmatter — die `.md` ist
  erzeugte Ausgabe.

## Das Ordner-/Manifest-Modell

Der Zustand sind die drei Ordner (`_ocr-vorschau/`, `_akzeptiert/`,
`_abgelehnt/`); `review-status.json` ist ein Cache mit Anmerkungen und darf
gelöscht werden. Es gilt: **das Dateisystem gewinnt, immer.** Niemals bewegt
das Plugin eine Datei, damit sie zum JSON passt — das würde einen bewussten
Handverschub still rückgängig machen.

Sechs Abgleichregeln (ausgelöst beim Öffnen, bei Einstellungsänderung und auf
entprellte Vault-Ereignisse):

1. **Exakter `parent.path`-Vergleich** beim Auflisten — kein `startsWith`:
   `_akzeptiert` liegt *innerhalb* von `_ocr-vorschau`, ein Präfixtest listete
   angenommene Dateien als offen.
2. **Ordnerlage ≠ Status → die Ordnerlage gewinnt.** `notiz` und
   `geprüft-bis` bleiben; einmalig wird „Status aus Ordnerlage übernommen"
   geloggt.
3. **Datei ohne Eintrag** → Eintrag anlegen; Metadaten aus dem
   Metadaten-Cache (Frontmatter).
4. **Eintrag ohne Datei** → liegt der gespeicherte Pfad woanders im Vault,
   wird der Eintrag `uebernommen` (Gedächtnis, wird nicht mehr gelistet);
   sonst fällt die Cache-Zeile weg. Es wird nie eine Datei gelöscht.
5. **Gleicher Basename in zwei Ordnern** → Regel 6.
6. **Neukonvertierung einer bereits entschiedenen Datei.** `pdf2md.py`
   schreibt immer nach `<out>/<stem>.md` und kennt die Unterordner nicht —
   ein erneuter Lauf erzeugt also eine zweite Datei gleichen Namens. Zwei
   Signale, jedes für sich ausreichend: dieselbe Datei liegt gleichzeitig
   offen *und* entschieden vor, oder das `ocr-datum` der offenen Fassung
   weicht vom protokollierten ab. Ergebnis: Status `neu-erzeugt`, alte
   Entscheidung wandert in `vorher`, die Zeile trägt das Badge „Neu erzeugt".
   **„Alte Fassung ersetzen"** (⋯-Menü) benennt die alte Fassung nach
   `_abgelehnt/<stem>-<altes-ocr-datum>.md` um — nichts geht verloren, die
   alte Fassung bekommt über den Abgleich einen eigenen Eintrag.

Verschieben läuft ausschließlich über `fileManager.renameFile` (aktualisiert
Links im Vault), nie über `vault.rename`. Deshalb funktionieren die
Diagrammbilder (`![[…png]]`, liegen geteilt in `_ocr-vorschau/assets/`) nach
dem Verschieben weiter. Zielordner werden vorher per `getFolderByPath`
geprüft und sonst angelegt. Schreibvorgänge auf das Manifest sind entprellt
(500 ms) und über eine Promise-Kette serialisiert; unlesbares JSON wird nach
`review-status.json.kaputt` umbenannt und aus der Ordnerlage neu aufgebaut.

## Wie die PDF-Spalte funktioniert

**`loadPdfJs()` ist öffentliche, dokumentierte Obsidian-API** und lädt die
pdf.js-Bibliothek, die Obsidian selbst mitbringt — inklusive des bereits
verdrahteten Workers (`GlobalWorkerOptions.workerSrc`). Das Plugin baut keinen
Blob-Worker und keinen Main-Thread-Fallback; das Bundle bleibt deshalb bei
~45 kB statt ~2,5 MB. Benutzt wird nur die seit Jahren stabile Fläche:
`getDocument`, `numPages`, `getPage`, `getViewport`, `render`, `destroy` —
alles in `src/pdf-pane.ts`, das eigentliche Rendern in einer Funktion, damit
eine geänderte Signatur ein Einzeiler-Fix bleibt. Obsidians *Viewer* wird
nicht angefasst. cMaps sind gesetzt (`/lib/pdfjs/cmaps/`): PDFs mit
eingebetteten CID/Type0-Fonts — genau das Material hier — rendern sonst leer.

Lazy mit vorgemessener Geometrie: Nach `getDocument` holt die Spalte **alle**
Viewports bei Maßstab 1 (nur Seiten-Dictionary, keine Rasterung) und setzt
jeder Seite ihr Seitenverhältnis als CSS-Custom-Property. Höhe und Breite
folgen daraus via `aspect-ratio` — die Scrollbar hat ab Frame eins die
richtige Geometrie, Springen beim Nachladen ist verhindert statt kompensiert.
Gerastert wird per `IntersectionObserver` (rootMargin 200 %), parallel 2, mit
Pixel-Skala `min(Breite/Seite · devicePixelRatio, pdfZoomMax)` und
LRU-Räumung bei 12 Canvases (beim Räumen `canvas.width = height = 0`, sonst
bleibt der Puffer liegen). `doc.destroy()` bei jedem Dateiwechsel und beim
Schließen; `RenderTask.cancel()` vor jedem Neuzeichnen. **Fehler degradieren:**
Banner im PDF-Kopf mit „Im PDF-Viewer öffnen" und „PDF zuordnen…" — nie eine
tote Spalte.

### Fallback, falls `loadPdfJs` je verschwindet

Dokumentierte Reserve: `pdfjs-dist` mitbündeln und den Worker per
esbuild-Textloader inline als Blob-URL erzeugen. Kosten: Bundle wächst auf
~2,5 MB, CSP-Anpassung kann nötig werden, und der Fork von Obsidian weicht
vom npm-Paket ab. Solange `loadPdfJs` existiert, wird das nicht gebaut.

## Bekannte Grenzen (bewusst)

- **`MarkdownRenderer.render` löst interne Embeds nicht auf** — die
  Diagrammbilder werden deshalb nach dem Rendern nachgebessert (Bild-Embeds
  per `getFirstLinkpathDest` + `<img>`). Findet Obsidian sie künftig selbst,
  ist die Schleife folgenlos.
- **Blockweises Rendern statt eines Blocks:** nötig, weil `%%…%%` in der
  Leseansicht unsichtbar ist (kein DOM-Knoten am Marker); der
  Seiten-Container ist der Sync-Anker. Nebenwirkung, positiv: die
  Fußnoten-Kollision über Seiten hinweg ist behoben.
- **12-Canvas-Deckel** (~4,5 MB pro A4-Canvas): weiter entfernte Seiten
  werden beim Zurückscrollen neu gerastert.
- **Zoom ist ein Layout-Zoom** (CSS `zoom`), kein Re-Render: eingezoomte
  Seiten können weicher wirken. Für Pixel-Exaktheit gibt es „Im PDF-Viewer
  öffnen".
- **minAppVersion 1.8.7** statt der ursprünglich geplanten 1.5.3: `revealLeaf`
  und das aktuelle `Notice`-Layout brauchen neuere Versionen. Der Plan hatte
  1.5.3, die tatsächliche API-Fläche verlangt mehr — ehrlich dokumentiert
  statt stillschweigend genutzt.
- Was headless nicht testbar ist (alles an `window.pdfjsLib`,
  `MarkdownRenderer`, DOM), ist es auch hier nicht — siehe Rauchtest.

## Einstellungen

Sichtbar: Vorschau-Ordner, Ordner für Angenommenes, Ordner für Abgelehntes,
Status-Datei (alle `normalizePath()`-bereinigt, mit Live-Hinweis wenn der
Ordner fehlt) und der Default der Markdown-Spalte. Intern persistiert:
Spaltenbreiten (Default 20/40/40, über die Ziehgriffe einstellbar),
`pdfZoomMax` (2.0), `syncAktiv`, `mdEagerLimit` (200 Seiten). Eine
Einstellungsänderung stößt den Abgleich an.

## Testen

`cd plugin` — `npm run check` (tsc), `npm run lint` (eslint mit
`eslint-plugin-obsidianmd`), `npm test` (33 Tests für Parser und Abgleich,
laufen unter `node --test` ohne Obsidian), `npm run build`.

### Rauchtest im echten Vault

1. `VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh`, Plugin aktivieren.
2. Vorschau auf einem Dokument mit **OCR- und Diagrammseite** erzeugen.
3. Ribbon → Ansicht öffnet, Datei steht unter „Offen".
4. **Schnell auf Seite 20 scrollen** → Platzhalter, dann Inhalt, **kein
   Springen**. ⇒ prüft die vorgemessenen Höhen.
5. Beide Richtungen scrollen, dann anhalten → **kein Schwingen, kein
   Zittern**. ⇒ prüft die drei Sync-Sicherungen.
6. `Gerendert`/`Quelltext` umschalten → Diagrammbild sichtbar bzw. roher
   `![[…]]`. ⇒ prüft die Embed-Nachbesserung.
7. `a` drücken → Datei in `_akzeptiert/`, **Bild rendert dort weiterhin**,
   Manifest-Eintrag da, Ansicht springt weiter. ⇒ prüft `renameFile`.
8. Obsidian beenden, Datei **im Finder** zurückschieben, neu starten → steht
   wieder unter „Offen", Manifest korrigiert sich, **nichts wurde
   zurückbewegt**. ⇒ prüft „Dateisystem gewinnt".
9. `pdf2md.py` erneut laufen lassen → Badge „Neu erzeugt — vorher akzeptiert",
   „Alte Fassung ersetzen" benennt die alte um.
10. `review-status.json` löschen, neu öffnen → alles listet korrekt.
    ⇒ prüft, dass das Manifest nur Cache ist.
11. `Cmd+R` bei offener Ansicht → dieselbe Datei ist wieder da.
