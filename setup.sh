#!/usr/bin/env bash
# Einmal-Setup für einen neuen Laptop: installiert alles, was die
# obsidian-ocr-pipeline braucht (Stufe 1, 2, 3 + Plugin).
# Idempotent — mehrfaches Ausführen ist unschädlich (auch nach git pull).
#
# Aufruf:  ./setup.sh
# Optional: VAULT_ROOT=<pfad> (Default: Repo-Parent — das Repo liegt im Vault)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ROOT="${VAULT_ROOT:-$(dirname "$REPO")}"
# venv-Konvention: alle Pipeline-venvs liegen unter $VENV_ROOT (Default
# ~/.venvs). Das ist die eine benannte Stelle — bin/pdf2md, bin/pdf-lib.sh
# und bin/reprocess-raw.sh leiten ihre Kandidaten-Pfade daraus ab.
# Überschreibbar: VENV_ROOT=<pfad> ./setup.sh
VENV_ROOT="${VENV_ROOT:-$HOME/.venvs}"
export PATH="$HOME/bin:$PATH"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ok      %s\n' "$*"; }
warn() { printf '\033[33m   ⚠ %s\033[0m\n' "$*"; }

# ─────────────────────────────────────────────────────── ① Preflight
say "Preflight"
[ "$(uname -s)" = "Darwin" ] || { echo "!! Nur macOS wird unterstützt"; exit 1; }
case "$(uname -m)" in
    arm64) ok "Apple Silicon ($(uname -m))" ;;
    *) warn "Intel-Mac erkannt — Stufe 2 (MLX) funktioniert dort vermutlich nicht (bleibt offen)" ;;
esac

if xcode-select -p >/dev/null 2>&1; then
    ok "Xcode Command Line Tools"
else
    echo "   Xcode CLT fehlen. Bitte ausführen und den macOS-Dialog bestätigen:"
    echo "      xcode-select --install"
    echo "   Danach ./setup.sh erneut starten."
    exit 1
fi

if command -v brew >/dev/null 2>&1; then
    ok "Homebrew ($(brew --version | head -1))"
else
    echo "   Homebrew fehlt — installiere (offizielles Installer-Skript) ..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    case "$(uname -m)" in
        arm64) eval "$(/opt/homebrew/bin/brew shellenv)" ;;
        *)     eval "$(/usr/local/bin/brew shellenv)" ;;
    esac
fi
command -v brew >/dev/null 2>&1 || { echo "!! brew nicht verfügbar"; exit 1; }
# Der nicht-interaktive Homebrew-Installer schreibt nicht in ~/.zprofile — das
# eval oben wirkt nur im laufenden Prozess. Sonst fehlen in neuen Terminals
# gs, tesseract, qpdf, pdfinfo, und die Verifikation in ⑧ sieht sie nur,
# weil sie im Prozess mit dem eval-PATH läuft.
if grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null; then
    ok "brew shellenv schon in ~/.zprofile"
else
    printf '\n# obsidian-ocr-pipeline\neval "$(%s/bin/brew shellenv)"\n' "$(brew --prefix)" >> "$HOME/.zprofile"
    echo "   brew shellenv ergänzt in ~/.zprofile (neues Terminal nötig)"
fi

# ─────────────────────────────────────────────── ② brew bundle
say "Systempakete (brew bundle)"
brew bundle --file="$REPO/Brewfile"
ok "Brewfile-Pakete"

# ───────────────────────────────────── ③ Stufe-1-venv (ocrmypdf)
say "Stufe 1 — ocrmypdf-venv"
# Python 3.12 via uv (python-build-standalone): bundelt expat selbst mit.
# Homebrew-Python-Bottles haben auf macOS ein kaputtes pyexpat
# (Symbol not found: _XML_SetAllocTrackerActivationThreshold).
uv python install 3.12 >/dev/null || true          # stderr sichtbar lassen
PY312="$(ls "$HOME/.local/share/uv/python"/cpython-3.12*/bin/python3.12 2>/dev/null | head -1 || true)"
[ -n "$PY312" ] || PY312="$(uv python find 3.12 2>/dev/null | head -1 || true)"
[ -n "$PY312" ] || { echo "!! kein Python 3.12 gefunden (uv python install 3.12)"; exit 1; }
if ! "$PY312" -c "import pyexpat" >/dev/null 2>&1; then
    echo "!! Python $PY312 hat ein kaputtes pyexpat (libexpat-Problem auf macOS)."
    echo "   Abhilfe: uv python uninstall 3.12 && uv python install --force 3.12"
    exit 1
fi

mkvenv() { # $1 = venv-Pfad, $2 = Python-Interpreter
    if [ -d "$1" ] && [ ! -x "$1/bin/pip" ]; then
        warn "$1 ohne pip — wird neu erstellt"
        rm -rf "$1"
    fi
    [ -d "$1" ] || uv venv --seed --python "$2" "$1"
}

mkvenv "$VENV_ROOT/ocrmypdf" "$PY312"
# ocrmypdf bewusst auf 17.8.0 gepinnt: die bin/-Skripte nutzen die 17.8-CLI
# (--engine auto|apple|tesseract). Neuere Versionen (>=17.10) heißen das
# Flag --ocr-engine und registrieren appleocr selbst als Plugin (entry point).
# ocrmypdf-appleocr 0.3.4: ab 0.4.0 registriert sich das Paket per entry
# point selbst und kollidiert mit dem --plugin-Check in install.sh.
# Upgrade-Pfad: Skripte auf die neue CLI umstellen, dann Pin lösen.
"$VENV_ROOT/ocrmypdf/bin/pip" install -q -U pip "ocrmypdf==17.8.0" "ocrmypdf-appleocr==0.3.4"
mkdir -p "$HOME/bin"
ln -sfn "$VENV_ROOT/ocrmypdf/bin/ocrmypdf" "$HOME/bin/ocrmypdf"
ok "ocrmypdf + Apple-Vision-Plugin"

# ─────────────────────────────────────────────── ④ PATH (~/bin)
say "PATH (~/bin)"
if grep -q 'HOME/bin' "$HOME/.zshrc" 2>/dev/null; then
    ok "export PATH=\"\$HOME/bin:\$PATH\" schon in ~/.zshrc"
else
    printf '\n# obsidian-ocr-pipeline\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.zshrc"
    echo "   ergänzt in ~/.zshrc (neues Terminal nötig; dieser Lauf nutzt es bereits)"
fi

# ─────────────────────────────── ⑤ Stufe-1-Links + Verifikation
say "Stufe 1 — CLI-Skripte"
# Sicherung: bestehende ~/bin-Verknüpfungen, die woandershin zeigen,
# vor dem Ersetzen protokollieren (Wiederherstellung per ln -sfn).
BACKUP="$REPO/.setup-bin-backup-$(date +%Y%m%d-%H%M%S).txt"
FOUND=0
for name in pdf-auto pdf-combine pdf-workflow reprocess-raw; do
    if [ -L "$HOME/bin/$name" ] && [ "$(readlink "$HOME/bin/$name")" != "$REPO/bin/$name.sh" ]; then
        printf '%s -> %s\n' "$name" "$(readlink "$HOME/bin/$name")" >> "$BACKUP"
        FOUND=1
    fi
done
if [ "$FOUND" = 1 ]; then
    echo "   alte Verknüpfungen gesichert: $BACKUP"
else
    rm -f "$BACKUP"
fi
bash "$REPO/install.sh"

# ─────────────────────────────── ⑥ Stufe-2-venv + pdf2md-Wrapper
say "Stufe 2 — MLX-venv (Status offen, wird bestmöglich versucht)"
MLX_OK=0
if mkvenv "$VENV_ROOT/mlxocr" "$PY312"; then
    if "$VENV_ROOT/mlxocr/bin/pip" install -q -U pip -r "$REPO/pdf2md/requirements.txt"; then
        ln -sfn "$REPO/bin/pdf2md" "$HOME/bin/pdf2md"
        MLX_OK=1
        ok "mlx-vlm in $VENV_ROOT/mlxocr"
    else
        warn "mlx-vlm-Installation fehlgeschlagen — Stufe 2 ohne Modell, Stufe 1 + Plugin funktionieren"
    fi
else
    warn "venv-Erstellung fehlgeschlagen — Stufe 2 übersprungen"
fi

# ─────────────────────────────────────────────── ⑦ Plugin (Stufe 3)
say "Stufe 3 — Plugin"
if [ -d "$VAULT_ROOT/.obsidian" ]; then
    VAULT_ROOT="$VAULT_ROOT" bash "$REPO/plugin/install-plugin.sh"
    JSON="$VAULT_ROOT/.obsidian/community-plugins.json"
    python3 - "$JSON" <<'PY'
import json, sys
p = sys.argv[1]
try:
    with open(p) as f:
        d = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    d = []
if "ocr-vorschau" not in d:
    d.append("ocr-vorschau")
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
    print("   aktiviert: ocr-vorschau")
else:
    print("   schon aktiv: ocr-vorschau")
PY
    if pgrep -x Obsidian >/dev/null 2>&1; then
        warn "Obsidian läuft — einmal neu laden (Cmd+R)"
    fi
else
    warn "'$VAULT_ROOT' hat kein .obsidian/ — Plugin-Schritt übersprungen (Stufe 1+2 sind installiert)"
fi

# ─────────────────────────────────────────────── ⑧ Verifikation
say "Verifikation"
FAIL=0
WARN=0
for cmd in pdf-auto pdf-combine pdf-workflow reprocess-raw ocrmypdf qpdf gs img2pdf tesseract pdfinfo pdftotext; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd"
    else
        echo "   FEHLT   $cmd"
        FAIL=1
    fi
done
if [ "$MLX_OK" = 1 ]; then
    if command -v pdf2md >/dev/null 2>&1; then
        ok "pdf2md"
    else
        echo "   FEHLT   pdf2md"
        FAIL=1
    fi
else
    warn "pdf2md fehlt (Stufe 2 offen)"
    WARN=1
fi
if ocrmypdf --plugin ocrmypdf_appleocr --help >/dev/null 2>&1; then
    ok "Apple-Vision-Plugin"
else
    warn "Apple-Vision-Plugin nicht nutzbar"
    WARN=1
fi
if tesseract --list-langs 2>/dev/null | grep -qx deu; then
    ok "Tesseract-Sprachpaket 'deu'"
else
    echo "   FEHLT   Tesseract 'deu'"
    FAIL=1
fi
if [ "$MLX_OK" = 1 ] && "$VENV_ROOT/mlxocr/bin/python" -c "import mlx_vlm" 2>/dev/null; then
    ok "mlx-vlm importierbar"
else
    warn "mlx-vlm nicht importierbar (Stufe 2 offen)"
    WARN=1
fi
if [ -d "$VAULT_ROOT/.obsidian" ]; then
    if [ -f "$VAULT_ROOT/.obsidian/plugins/ocr-vorschau/main.js" ]; then
        ok "Plugin in $VAULT_ROOT/.obsidian/plugins/ocr-vorschau/"
    else
        echo "   FEHLT   Plugin-Dateien (Stufe 3)"
        FAIL=1
    fi
else
    warn "kein .obsidian/ — Plugin-Schritt übersprungen (Stufe 1+2 sind installiert)"
    WARN=1
fi

echo
if [ "$FAIL" = 1 ]; then
    echo "Fertig — mit Fehlern (siehe oben)."
    exit 1
fi
if [ "$WARN" = 1 ]; then
    echo "Fertig — mit offenen Punkten (siehe ⚠)."
else
    echo "Fertig. Obsidian neu laden (Cmd+R), dann ist alles einsatzbereit."
fi
echo "Hinweis: für neue Shells einmal ein neues Terminal öffnen (PATH-Änderung in ~/.zshrc)."
