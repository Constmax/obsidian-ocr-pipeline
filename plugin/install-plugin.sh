#!/usr/bin/env bash
# Installs the Obsidian plugin in the vault.
# Default: copies checked-in main.js (no Node required).
# With --build: builds from src/ (requires node/npm — dev machine only).
# Idempotent — multiple executions are harmless.
#
# main.js is verified against src/ in CI (.github/workflows/ci.yml);
# locally the script warns if src/ or main.js differs from the commit.
#
# Usage:  VAULT_ROOT=~/JuraExamenVault plugin/install-plugin.sh [--symlink] [--build] [--enable]
#         plugin/install-plugin.sh --print-id

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The plugin id lives in manifest.json only (Issue #104): Obsidian loads the
# plugin from the folder of that name and enables it under that name.
PLUGIN_ID="$(sed -n 's/^[[:space:]]*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/manifest.json" | head -1)"
[ -n "$PLUGIN_ID" ] || { echo "!! no \"id\" in $PLUGIN_DIR/manifest.json"; exit 1; }
# The id before the English rename (f220066). --enable disables it, so an old
# copy left in the vault never runs side by side with this one.
LEGACY_ID="ocr-vorschau"
SYMLINK=0
BUILD=0
ENABLE=0

for arg in "$@"; do
    case "$arg" in
        --print-id) echo "$PLUGIN_ID"; exit 0 ;;
        --symlink) SYMLINK=1 ;;
        --build) BUILD=1 ;;
        --enable) ENABLE=1 ;;
        -h|--help)
            cat <<'HELP'
install-plugin.sh — Install Obsidian plugin in vault

  VAULT_ROOT=<path> plugin/install-plugin.sh [--symlink] [--build] [--enable]
  plugin/install-plugin.sh --print-id

  --enable    Also enable the plugin in .obsidian/community-plugins.json
              (and disable the pre-rename id ocr-vorschau).
  --print-id  Print the plugin id from manifest.json and exit.
  --build    Build from src/ instead of using checked-in main.js (npm, dev only).
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
if [ "$ENABLE" = 1 ]; then
    echo "== Enabling in $VAULT_ROOT/.obsidian/community-plugins.json"
    python3 - "$VAULT_ROOT/.obsidian/community-plugins.json" "$PLUGIN_ID" "$LEGACY_ID" <<'PY'
import json, sys
path, plugin_id, legacy_id = sys.argv[1:]
try:
    with open(path) as f:
        enabled = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    enabled = []
if not isinstance(enabled, list):
    enabled = []
updated = [entry for entry in enabled if entry != legacy_id]
if legacy_id in enabled:
    print(f"   disabled: {legacy_id} (pre-rename copy; its settings are taken over"
          " on first start, then its folder can be deleted)")
if plugin_id in updated:
    print(f"   already active: {plugin_id}")
else:
    updated.append(plugin_id)
    print(f"   enabled: {plugin_id}")
if updated != enabled:
    with open(path, "w") as f:
        json.dump(updated, f, indent=2)
PY
    echo
    echo "Done. Reload Obsidian once (Cmd+R)."
else
    echo "Done. In Obsidian: Settings → Community plugins → Enable 'OCR Preview'."
    echo "Reload Obsidian once (Cmd+R)."
fi
