# Installation & Troubleshooting

## Plugin (Stufe 3)

Voraussetzung: Node ≥ 22 und npm. Aus dem Repo:

```bash
VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh
```

- Prüft node/npm und `$VAULT_ROOT/.obsidian`, baut das Plugin (`npm run build`)
  und **kopiert** `main.js`, `manifest.json` und `styles.css` nach
  `$VAULT_ROOT/.obsidian/plugins/ocr-vorschau/`.
- Kopie ist Default: bei einem Vault in iCloud Drive verlieren Symlinks
  Dateien. `--symlink` bleibt als Dev-Opt-in (nur lokal, nie iCloud).
- Danach in Obsidian: Einstellungen → Community-Plugins → „OCR-Vorschau"
  aktivieren, einmal `Cmd+R`.
- Bedienung: [review-ansicht.md](review-ansicht.md).

## Einmalige Einrichtung

### Core-Tools
```bash
brew install ocrmypdf img2pdf qpdf tesseract-lang ghostscript
```

### Apple Vision Plugin (empfohlen)
```bash
~/.venvs/ocrmypdf/bin/pip install ocrmypdf-appleocr
```

Falls `~/.venvs/ocrmypdf/` nicht existiert, siehe "Python 3.14 Fix" unten.

### Kompressions-Helper
```bash
brew install pngquant jbig2enc unpaper
```

### Scripts installieren
Scripts gehören nach `~/bin/` mit Executable-Flag:
```bash
chmod +x ~/bin/pdf-auto ~/bin/pdf-workflow ~/bin/pdf-combine
```

Und `~/bin` muss im PATH sein:
```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## Python 3.14 Bug (`No module named expat`)

Homebrew-ocrmypdf wird manchmal gegen Python 3.14 gebaut, welches auf macOS einen kaputten `pyexpat` hat. Symptom:
```
ImportError: No module named expat
```

**Lösung**: Isoliere ocrmypdf in einem Python 3.12 venv.

```bash
brew uninstall ocrmypdf

/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv ~/.venvs/ocrmypdf
~/.venvs/ocrmypdf/bin/pip install --upgrade pip
~/.venvs/ocrmypdf/bin/pip install ocrmypdf ocrmypdf-appleocr

# Symlink nach ~/bin damit die Scripts ocrmypdf finden
ln -sf ~/.venvs/ocrmypdf/bin/ocrmypdf ~/bin/ocrmypdf
```

Prüfen:
```bash
ocrmypdf --version
ocrmypdf --plugin ocrmypdf_appleocr --help | grep -i apple
```

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
PATH enthält `~/bin` nicht. Fix:
```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### `brew install ocrmypdf` schlägt fehl mit "Python 3.14 pyexpat"
Siehe "Python 3.14 Fix" oben — nicht brew nutzen, venv-Weg gehen.

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
Nein — Scripts gehören nach `~/bin/` (lokal, nicht iCloud). Nur die Input/Output-PDFs können in iCloud sein.

### Vault in iCloud synchronisiert
Das ist normal. Die Scripts lesen/schreiben in iCloud-Pfade wie jeder andere Pfad auch. Bei "Optimize Mac Storage":

```bash
# Alle Dateien in einem Ordner runterladen bevor Script läuft
brctl download "<path>"
```

### Obsidian External Terminal Plugin
Öffnet Terminal im Vault-Root. PATH/Scripts funktionieren normal, da sie in `~/bin/` liegen, nicht im Vault.
