#!/usr/bin/env bash
# Baut das Obsidian-Plugin und legt es im Vault ab.
# Idempotent — mehrfaches Ausführen ist unschädlich.
#
# Aufruf:  VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh [--symlink]

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="ocr-vorschau"
SYMLINK=0

for arg in "$@"; do
    case "$arg" in
        --symlink) SYMLINK=1 ;;
        -h|--help)
            cat <<'HILFE'
install-plugin.sh — Obsidian-Plugin bauen und im Vault ablegen

  VAULT_ROOT=<pfad> plugin/install-plugin.sh [--symlink]

  --symlink   Statt zu kopieren den Plugin-Ordner in den Vault verlinken.
              Nur für die Entwicklung. NICHT benutzen, wenn der Vault in
              iCloud Drive liegt: Symlinks werden dort nicht zuverlässig
              synchronisiert und können Dateien verlieren.
              Standard ist deshalb Kopieren.
HILFE
            exit 0 ;;
        *) echo "!! unbekannte Option: $arg"; exit 1 ;;
    esac
done

echo "== Werkzeuge"
for cmd in node npm; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "   ok      $cmd $("$cmd" --version)"
    else
        echo "   FEHLT   $cmd"
        echo
        echo "Node fehlt:  brew install node"
        exit 1
    fi
done

echo
echo "== Vault"
if [ -z "${VAULT_ROOT:-}" ]; then
    echo "   !! VAULT_ROOT ist nicht gesetzt."
    echo "      VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh"
    exit 1
fi
# Tilde-Expansion greift bei 'VAULT_ROOT=~/x' vor dem Skript, bei einem
# gequoteten Wert aber nicht. Beide Formen sollen funktionieren.
VAULT_ROOT="${VAULT_ROOT/#\~/$HOME}"
if [ ! -d "$VAULT_ROOT/.obsidian" ]; then
    echo "   !! '$VAULT_ROOT' sieht nicht nach einem Obsidian-Vault aus (.obsidian/ fehlt)"
    exit 1
fi
echo "   ok      $VAULT_ROOT"

ZIEL="$VAULT_ROOT/.obsidian/plugins/$PLUGIN_ID"

echo
echo "== Bauen"
cd "$PLUGIN_DIR"
# npm install nur, wenn node_modules fehlt oder älter als package.json ist.
if [ ! -d node_modules ] || [ package.json -nt node_modules ]; then
    echo "   npm install ..."
    npm install --silent
else
    echo "   node_modules aktuell — übersprungen"
fi
echo "   npm run build ..."
npm run build --silent
[ -f main.js ] || { echo "   !! main.js wurde nicht erzeugt"; exit 1; }
echo "   ok      main.js ($(( $(wc -c < main.js) / 1024 )) kB)"

echo
if [ "$SYMLINK" = 1 ]; then
    echo "== Verlinken nach $ZIEL"
    mkdir -p "$(dirname "$ZIEL")"
    if [ -L "$ZIEL" ] && [ "$(readlink "$ZIEL")" = "$PLUGIN_DIR" ]; then
        echo "   schon verlinkt"
    else
        [ -e "$ZIEL" ] && [ ! -L "$ZIEL" ] && {
            echo "   !! $ZIEL existiert und ist kein Symlink — von Hand entfernen"; exit 1; }
        ln -sfn "$PLUGIN_DIR" "$ZIEL"
        echo "   verlinkt"
    fi
    echo "   Hinweis: bei einem Vault in iCloud Drive stattdessen ohne --symlink laufen lassen."
else
    echo "== Kopieren nach $ZIEL"
    mkdir -p "$ZIEL"
    for datei in main.js manifest.json styles.css; do
        [ -f "$datei" ] || { echo "   !! fehlt: $datei"; exit 1; }
        cp "$datei" "$ZIEL/$datei"
        echo "   kopiert: $datei"
    done
fi

echo
echo "Fertig. In Obsidian: Einstellungen → Community-Plugins → 'OCR-Vorschau' aktivieren."
echo "Bei laufendem Obsidian einmal neu laden (Cmd+R)."
