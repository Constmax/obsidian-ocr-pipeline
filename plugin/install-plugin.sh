#!/usr/bin/env bash
# Installs the Obsidian plugin in the vault.
# Default: copies checked-in main.js (no Node required).
# With --build: builds from src/ (requires node/npm — dev machine only).
# Idempotent — multiple executions are harmless.
#
# main.js is verified against src/ in CI (.github/workflows/ci.yml);
# locally the script warns if src/ or main.js differs from the commit.
#
# Usage:  VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh [--symlink] [--build]

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="ocr-preview"
SYMLINK=0
BUILD=0

for arg in "$@"; do
    case "$arg" in
        --symlink) SYMLINK=1 ;;
        --build) BUILD=1 ;;
        -h|--help)
            cat <<'HELP'
install-plugin.sh — Install Obsidian plugin in vault

  VAULT_ROOT=<path> plugin/install-plugin.sh [--symlink] [--build]

  --build     Build from src/ instead of using checked-in main.js (npm, dev only).
  --symlink   Link plugin folder into vault instead of copying.
              For development only. DO NOT use if vault is on
              iCloud Drive: symlinks are not reliably synced there
              and can lose files. Copying is default.
HELP
            exit 0 ;;
        *) echo "!! unknown option: $arg"; exit 1 ;;
    esac
done

if [ "$BUILD" = 1 ]; then
    echo "== Tools"
    for cmd in node npm; do
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "   ok      $cmd $("$cmd" --version)"
        else
            echo "   MISSING $cmd"
            echo
            echo "Node missing: brew install node"
            exit 1
        fi
    done

    echo
    echo "== Build"
    cd "$PLUGIN_DIR"
    if [ ! -d node_modules ] || [ package.json -nt node_modules ]; then
        echo "   npm install ..."
        npm install --silent
    else
        echo "   node_modules up to date — skipped"
    fi
    echo "   npm run build ..."
    npm run build --silent
    [ -f main.js ] || { echo "   !! main.js was not generated"; exit 1; }
    echo "   ok      main.js ($(( $(wc -c < main.js) / 1024 )) kB)"
else
    echo "== main.js (checked in, no build — --build for npm)"
    [ -f "$PLUGIN_DIR/main.js" ] || { echo "   !! main.js missing — build with --build"; exit 1; }
    if git -C "$PLUGIN_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        if ! git -C "$PLUGIN_DIR" diff --quiet HEAD -- src main.js; then
            echo "   ⚠ src/ or main.js differs from commit — rebuild with --build if necessary"
        fi
    fi
fi

echo
echo "== Vault"
if [ -z "${VAULT_ROOT:-}" ]; then
    echo "   !! VAULT_ROOT is not set."
    echo "      VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh"
    exit 1
fi
VAULT_ROOT="${VAULT_ROOT/#\~/$HOME}"
if [ ! -d "$VAULT_ROOT/.obsidian" ]; then
    echo "   !! '$VAULT_ROOT' does not look like an Obsidian vault (.obsidian/ missing)"
    exit 1
fi
echo "   ok      $VAULT_ROOT"

DEST="$VAULT_ROOT/.obsidian/plugins/$PLUGIN_ID"

echo
if [ "$SYMLINK" = 1 ]; then
    echo "== Symlinking to $DEST"
    mkdir -p "$(dirname "$DEST")"
    if [ -L "$DEST" ] && [ "$(readlink "$DEST")" = "$PLUGIN_DIR" ]; then
        echo "   already linked"
    else
        [ -e "$DEST" ] && [ ! -L "$DEST" ] && {
            echo "   !! $DEST exists and is not a symlink — remove manually"; exit 1; }
        ln -sfn "$PLUGIN_DIR" "$DEST"
        echo "   linked"
    fi
    echo "   Note: for a vault in iCloud Drive, run without --symlink instead."
else
    echo "== Copying to $DEST"
    mkdir -p "$DEST"
    for file in main.js manifest.json styles.css; do
        [ -f "$PLUGIN_DIR/$file" ] || { echo "   !! missing: $file"; exit 1; }
        cp "$PLUGIN_DIR/$file" "$DEST/$file"
        echo "   copied: $file"
    done
fi

echo
echo "Done. In Obsidian: Settings → Community plugins → Enable 'OCR Preview'."
echo "Reload Obsidian once (Cmd+R)."
