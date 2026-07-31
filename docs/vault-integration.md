# Vault-Integration — Zusammenarbeit mit CLAUDE.md-Workflows

Dieser Skill ist die Pre-Processing-Stufe für Vault-Workflows. Nach der PDF-Verarbeitung übernimmt der Vault-spezifische Ingest-Workflow (definiert in `<vault>/CLAUDE.md`).

## Rollenverteilung

| Komponente | Zuständig für |
|---|---|
| **Dieser Skill** | Bilder/PDFs → durchsuchbare, komprimierte PDF in `raw/` |
| **Vault CLAUDE.md** | `raw/` → `wiki/` (Zusammenfassungen, Probleme, Schemata, Queryverlinkung) |

Der Skill **beendet** seine Arbeit wenn das finale PDF in `raw/` liegt. Er schreibt **keine Wiki-Seiten** und aktualisiert **keinen Index**.

## Typische Ingest-Sequenz

### Szenario A: Einzelne Quelle (Urteil, kurzes Skript, Klausur)

```
1. Nutzer: "Ich habe ein neues BGH-Urteil gescannt, bitte ingest"
2. Skill aktiv
   ├─ raw/assets/ prüfen → Bilder oder schon eine PDF?
   ├─ pdf-workflow oder pdf-combine entsprechend
   └─ Output nach raw/ verschieben
3. Skill beendet → CLAUDE.md-Workflow "Ingest" startet
   ├─ Quelle lesen
   ├─ Zusammenfassung in wiki/ erstellen
   ├─ Probleme extrahieren
   └─ Index + Log updaten
```

### Szenario B: Massen-Import (ganzes Hemmer-Paket)

```
1. Nutzer: "Hier sind 40 Skripte aus dem Semester, batch-ingest"
2. Skill aktiv
   ├─ pdf-auto raw/assets --cleanup --fast --engine tesseract
   │    (tesseract weil Hemmer = Zweispalter)
   ├─ _processed/*.pdf nach raw/ verschieben
   └─ _archive/ mit Originalen prüfen
3. Skill beendet → CLAUDE.md "Batch-Ingest" Workflow übernimmt
```

### Szenario C: Klausur-Ingest (Spezialfall!)

CLAUDE.md unterscheidet strikt zwischen normalem Ingest und Klausur-Ingest. Der Skill ist in beiden Fällen identisch — nur die nachfolgende Wiki-Arbeit unterscheidet sich.

**Wichtig**: Der Skill markiert nichts als "Klausur". Das passiert erst im Wiki-Workflow basierend auf Nutzerangabe.

## Engine-Empfehlungen nach Quellentyp

Passend zur CLAUDE.md-Taxonomie:

| Quellentyp (CLAUDE.md) | Layout | Engine | Grund |
|---|---|---|---|
| Hemmer/Kaiser-Skripte | 2-spaltig, kleiner Druck | `tesseract` | Spaltenerkennung kritisch |
| BGH/BVerwG-Urteile | 1-spaltig, Fließtext | `apple` | schneller, sauber |
| Lehrbuchkapitel (Scans) | 1-spaltig meist | `apple` | besser bei Handyfotos |
| Aufsätze (JuS/NJW) | 2-spaltig oft | `tesseract` | Spalten |
| Vorlesungsnotizen | variabel | `apple` | Handschrift möglich |
| Klausuren / Musterlösungen | 1-spaltig | `apple` | Standard |

## Konventionen beachten

Aus CLAUDE.md:

> Dateinamen: Kleinbuchstaben, Bindestriche statt Leerzeichen
> Keine Umlaute in Dateinamen (ue, ae, oe, ss statt ü, ä, ö, ß)

**Relevanz für diesen Skill**: Beim Erzeugen von Output-Namen diese Konventionen einhalten:

```bash
# ✅ Konvention-konform
pdf-combine raw/assets/urteile urteil-bgh-ix-zr-42-24

# ❌ Verletzt Konvention (Großbuchstaben, Leerzeichen, Umlaute)
pdf-combine raw/assets/urteile "Urteil BGH IX ZR 42 über Bürgschaft"
```

Wenn der Nutzer einen Namen mit Umlauten/Großbuchstaben nennt, empfehlen oder stillschweigend umbenennen — je nach Präferenz.

## pdf-auto Teil-Detection und CLAUDE.md

Das "Teil N"-Pattern in `pdf-auto` passt nicht direkt zur Dateinamens-Konvention:
- Teil-Pattern verlangt Leerzeichen: `Verwaltungsrecht AT Teil 1.pdf`
- Konvention verlangt Bindestriche: `verwaltungsrecht-at-teil-1.pdf`

**Praktische Empfehlung**:
- **In raw/assets/** (Input): beliebige Namen, oft mit Leerzeichen + "Teil N"
- **In raw/** (Output): gemäß Konvention in Kleinbuchstaben

`pdf-auto` produziert Outputs wie `Verwaltungsrecht AT.pdf` (Teil-Suffix gestripped, aber Rest unverändert). Der nachfolgende Vault-Workflow kann dann umbenennen wenn nötig:

```bash
mv "raw/assets/_processed/Verwaltungsrecht AT.pdf" "raw/verwaltungsrecht-at.pdf"
```

## Spezialfall: Obsidian iCloud-Sync

Wenn der Vault in iCloud liegt, passiert nach dem Script-Output:
1. Scripts schreiben `raw/foo.pdf` lokal
2. iCloud-Sync läuft automatisch im Hintergrund
3. Obsidian auf anderen Geräten bekommt die PDF nach 1-5 Minuten

Wenn der Skill mehrere PDFs in schneller Folge erzeugt: iCloud kann mit dem Upload hinterherhängen. Kein Problem für den Skill, nur Info für den Nutzer falls er auf iPad/iPhone arbeitet.

## Zusammenspiel mit Batch-Ingest

CLAUDE.md Batch-Ingest-Schritt 2 sagt:
> Führe `pdf-auto raw/assets --cleanup --fast` aus. Dies erstellt für jeden Unterordner/jede Gruppe eine fertige PDF in `raw/assets/_processed/`.

Der Skill implementiert genau das. Die zusätzliche Intelligenz dieses Skills: **Engine-Auswahl basierend auf Quellentyp** (wenn der Nutzer Hemmer erwähnt → `--engine tesseract` automatisch).
