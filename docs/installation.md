# Installation & Troubleshooting

Die Einrichtung läuft über das Einmal-Setup — `./setup.sh` im Repo-Root
(dokumentiert in der [README](../README.md)). Diese Seite ist die
Fehlersuche-Seite: was zu tun ist, wenn das Setup abbricht, und was zu tun
ist, wenn danach etwas nicht funktioniert.

## Wenn `setup.sh` bei Schritt X abbricht

Die Abschnitte folgen den Blöcken des Skripts.

### ① Xcode-CLT / Homebrew

- „Xcode CLT fehlen …" → `xcode-select --install` ausführen und den
  macOS-Dialog bestätigen, danach `./setup.sh` erneut starten.
- „Homebrew fehlt …" → das Skript installiert Homebrew selbst (offizielles
  Installer-Skript). Das `eval` daraus wirkt nur im laufenden Prozess —
  `setup.sh` trägt das `brew shellenv` deshalb in `~/.zprofile` nach.
  Prüfen: `command -v brew` in einem **neuen** Terminal.
- Schlägt die Installation fehl: Fehlermeldung des Installer-Skripts ansehen
  (meist Netz oder fehlende Xcode-CLT, siehe oben).

### ② `brew bundle` schlägt fehl

- Von Hand wiederholen: `brew bundle --file=Brewfile` — dort steht die
  eigentliche Fehlermeldung.
- `ocrmypdf` steht bewusst **nicht** im Brewfile (pyexpat, siehe unten) —
  nicht nachinstallieren; es kommt aus dem venv-Schritt ③.

### ③ uv / Python 3.12 / pyexpat

- „kein Python 3.12 gefunden (uv python install 3.12)" → offline? Von Hand
  `uv python install 3.12` — stderr zeigt den Grund.
- „Python … hat ein kaputtes pyexpat" → die Abhilfe aus dem Skript:
  ```bash
  uv python uninstall 3.12 && uv python install --force 3.12
  ```
  danach `./setup.sh` erneut.

### ④ PATH (~/bin)

- „ergänzt in ~/.zshrc" → in **neuen** Terminals ist `~/bin` im PATH; offene
  Terminals sehen die Änderung nicht.
- `command not found: pdf-auto` (nach dem Setup) → neues Terminal öffnen
  (`.zshrc`/`.zprofile` werden nur beim Start gelesen).

### ⑤ `~/bin`-Links

- Bestehende Verknüpfungen, die woandershin zeigten, werden vor dem Ersetzen
  nach `.setup-bin-backup-<datum>.txt` im Repo gesichert. Wiederherstellung
  per `ln -sfn <ziel> ~/bin/<name>`.
- „FEHLT   pdf-auto …" in der Verifikation → Schritt ④ prüfen.

### ⑥ MLX (Stufe 2) auf Intel-Macs

- Kein Fehler: Stufe 2 braucht Apple Silicon. Das Setup warnt („bleibt
  offen") und endet mit Exit 0; Stufe 1 + Plugin funktionieren trotzdem.

### ⑦ Vault ohne `.obsidian/`

- „'…' hat kein .obsidian/" → der Plugin-Schritt wird übersprungen; Stufe 1+2
  sind installiert. Obsidian einmal starten (legt `.obsidian/` an), dann
  `./setup.sh` erneut.

## Python-3.12-expat-Bug

Homebrew-Builds von ocrmypdf laufen manchmal gegen ein Python mit kaputtem
`pyexpat` auf macOS. Betroffen: Brew-Pythons ab 3.12 (u.a. 3.14). Symptome:

```
ImportError: No module named expat
Symbol not found: _XML_SetAllocTrackerActivationThreshold
```

Deshalb installiert `setup.sh` ocrmypdf nie über brew, sondern in einem
eigenen venv (`~/.venvs/ocrmypdf`) mit einem Python 3.12 aus **uv**
(python-build-standalone) — das bundelt expat selbst mit.

Prüfen, ob das eigene Python betroffen ist:

```bash
~/.venvs/ocrmypdf/bin/python -c "import pyexpat" || echo "pyexpat kaputt"
```

Abhilfe: das uv-Python neu installieren und das Setup erneut laufen lassen:

```bash
uv python uninstall 3.12 && uv python install --force 3.12
./setup.sh
```

## Plugin (Stufe 3)

```bash
VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh
```

- Kopiert `main.js`, `manifest.json` und `styles.css` nach
  `$VAULT_ROOT/.obsidian/plugins/ocr-vorschau/`. Das eingecheckte `main.js`
  ist der Default — kein Node nötig; nur `--build` (npm, Dev-Maschine)
  braucht node/npm.
- Kopie ist Default: bei einem Vault in iCloud Drive verlieren Symlinks
  Dateien. `--symlink` bleibt als Dev-Opt-in (nur lokal, nie iCloud).
- Danach in Obsidian: Einstellungen → Community-Plugins → „OCR-Vorschau"
  aktivieren, einmal `Cmd+R`.
- Bedienung: [review-ansicht.md](review-ansicht.md).

## Verifikation

```bash
# Hauptfunktionen testen
ocrmypdf --version
tesseract --list-langs    # muss 'deu' enthalten
gs --version
sort -V /dev/null && echo "natural sort OK"

# Apple Vision check
ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1 && echo "Apple Vision OK"
```

## Häufige Fehler

### `command not found: pdf-auto`

PATH enthält `~/bin` nicht — neues Terminal öffnen (siehe „④ PATH" oben).
Die Zeile steht in `~/.zshrc`:

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### `brew install ocrmypdf` schlägt fehl mit "pyexpat"

Bewusst so: brew-ocrmypdf nicht nutzen — siehe „Python-3.12-expat-Bug".
`setup.sh` nimmt den venv-Weg.

### `'pngquant' could not be executed`

Alte Script-Version die `--optimize 3` hardcoded. Scripts updaten.

### `zsh: killed` bei OCR

RAM-Kill durch macOS. Fix in dieser Reihenfolge probieren:
1. `--jobs 1 --dpi 150`
2. `--jobs 1 --dpi 100`
3. Datei splitten (weniger Seiten gleichzeitig)

### Pfade mit Leerzeichen werden missinterpretiert

Immer in Anführungszeichen:

```bash
pdf-auto "/path/with spaces/folder"
```

oder aus Finder ins Terminal ziehen (auto-escape).

## iCloud-Besonderheiten

### Scripts in iCloud?

Nein — Scripts gehören nach `~/bin/` (lokal, nicht iCloud). Nur die
Input/Output-PDFs können in iCloud sein.

### Vault in iCloud synchronisiert

Das ist normal. Die Scripts lesen/schreiben in iCloud-Pfade wie jeder andere
Pfad auch. Bei "Optimize Mac Storage":

```bash
# Alle Dateien in einem Ordner runterladen bevor Script läuft
brctl download "<path>"
```

### Obsidian External Terminal Plugin

Öffnet Terminal im Vault-Root. PATH/Scripts funktionieren normal, da sie in
`~/bin/` liegen, nicht im Vault.
