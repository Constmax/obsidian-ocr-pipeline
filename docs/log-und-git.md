# Log & GitHub Sync — Gemeinsame Vorlage

## Einheitliches Log-Format (wiki/log.md)

Jeder Eintrag folgt exakt diesem Schema:

```markdown
## [YYYY-MM-DD] aktion | Titel
Kurze Beschreibung was passiert ist.
```

### Aktionstypen und Pflichtfelder

| Aktion | Titelformat | Pflichtinhalt |
|---|---|---|
| `ingest` | Name der Quelle | Erstellte/aktualisierte Seiten (Liste) |
| `batch-ingest` | Thema / Ordner | Anzahl Quellen, erstellte/aktualisierte Seiten |
| `klausur-ingest` | Klausurbezeichnung | Klausurseite, extrahierte Probleme, Schema-Änderungen |
| `lint` | Scope (z.B. "Zivilrecht" oder "Ganzes Wiki") | Probleme gefunden / gefixt, Raw-Checks, Lücken |
| `pdf-workflow` | Verarbeitete Dateien | Input-Dateien, Output nach raw/ |
| `update` | Betroffene Seite(n) | Was und warum geändert |

### Beispiele

```markdown
## [2026-04-25] ingest | BGB AT Teil 1 — Skript WS 2026
Zusammenfassung erstellt: [[bgb-at-teil-1-klausurtechnik]]. Problem aktualisiert: [[erklaerungsbewusstsein]]. Schema ergänzt: [[pruefungsaufbau-primaeranspruch]].

## [2026-04-25] batch-ingest | Strafrecht AT Skripte (3 Quellen)
3 Quellen verarbeitet. Neu: [[strafrecht-at-teil-1-grundlagen]], [[notwehr]], [[notwehrexzess]]. Aktualisiert: [[schuldfaehigkeit]].

## [2026-04-25] klausur-ingest | BGB-AT Fall 12 — Minderjähriger
Klausurseite: [[bgb-at-fall-12-minderjaehriger-suesswaren]]. Probleme extrahiert: [[geschaeftsfaehigkeit]] (Klausurfundstelle ergänzt), [[anfechtung]] (Klausurfundstelle ergänzt).

## [2026-04-25] lint | Zivilrecht
Struktur: 4 kaputte Links gefixt, 2 verwaiste Seiten gefunden. Raw-Check auf 3 Seiten. Lücken: Bereicherungsrecht-Übersicht fehlt.

## [2026-04-25] pdf-workflow | Hemmer BGB AT Teil 4+5 (Scans)
Input: raw/assets/hemmer-bgb-at/ (23 Bilder). Output: raw/4_BGB_AT_Teil_4.pdf, raw/5_BGB_AT_Teil_5.pdf (tesseract, 200 DPI).
```

---

## Git-Commit

**Kein Auto-Push.** Commits erfolgen **nur auf ausdrücklichen Wunsch** des Nutzers oder im **täglichen Sammel-Commit** — nicht nach jedem einzelnen Workflow (das würde die History fluten und kollidiert mit der tatsächlichen Praxis).

Wenn ein Commit fällig ist, vom **Vault-Root** aus arbeiten (Vault liegt unter `~/JuraExamenVault`, **nicht** unter dem alten iCloud-Pfad `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/PW` — dieser Pfad ist tot):

```bash
# vom Vault-Root aus (CWD ist bereits Vault-Root beim Skill-Aufruf)
git add -A
git commit -m "YYYY-MM-DD aktion: Titel"
```

Commit-Nachricht entspricht dem Log-Eintrag-Titel, z.B.:
- `2026-04-25 ingest: BGB AT Teil 1 — Skript WS 2026`
- `2026-04-25 lint: Zivilrecht`
- `2026-04-25 pdf-workflow: Hemmer BGB AT Teil 4+5`

**Push** nur, wenn der Nutzer es verlangt. Wenn kein Remote konfiguriert ist, `git push` überspringen und Nutzer informieren. Kein `--force`, kein Auto-Push auf `main`/`master`.
