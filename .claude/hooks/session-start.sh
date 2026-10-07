#!/bin/bash
# SessionStart hook for Claude Code on the web: installs the Linux toolchain
# that `make check` needs, mirroring .github/workflows/ci.yml. The Apple-only
# parts of setup.sh (brew, MLX, Apple Vision) are out of scope here.
# Idempotent: every step checks first, so a resumed session costs seconds.
# Async: the session starts at once while this runs in the background; it is
# finished when $VENV_ROOT/.session-start.done exists (log: .session-start.log).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

VENV_ROOT="${VENV_ROOT:-$HOME/.venvs}"
# Same pins as ci.yml / setup.sh.
SHELLCHECK_VERSION=v0.11.0
OCRMYPDF_VERSION=17.8.0
PYTHON_VERSION=3.12

DONE="$VENV_ROOT/.session-start.done"

# Session env first, so it is in place however long the install runs: the dev
# venv is the default python3; `make test-ocrmypdf` finds $VENV_ROOT/ocrmypdf
# on its own.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VENV_ROOT=\"$VENV_ROOT\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$VENV_ROOT/dev/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# Run in the background (async hook protocol: this must be the first stdout line).
echo '{"async": true, "asyncTimeout": 600000}'

mkdir -p "$VENV_ROOT"
rm -f "$DONE"
exec 2>>"$VENV_ROOT/.session-start.log"
log() { echo "[session-start $(date +%T)] $*" >&2; }

# System tools: tesseract (ocrmypdf demands it even with an engine plugin),
# poppler (pdftotext -raw for the quality gate), qpdf and ghostscript.
# German language data too: the tests stub it, but a real Stage-1 run passes
# -l deu (bin/pdf-lib.sh build_ocr_args).
missing=()
for pair in tesseract:tesseract-ocr pdftotext:poppler-utils qpdf:qpdf gs:ghostscript; do
  command -v "${pair%%:*}" >/dev/null 2>&1 || missing+=("${pair#*:}")
done
tesseract --list-langs 2>/dev/null | grep -qx deu || missing+=(tesseract-ocr-deu)
if [ ${#missing[@]} -gt 0 ]; then
  log "apt-get install ${missing[*]}"
  # Third-party PPAs in the base image may be blocked; their warnings are harmless.
  apt-get update -qq >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" >/dev/null
fi

# Pinned shellcheck, like CI: a newer release brings new default checks.
if ! shellcheck --version 2>/dev/null | grep -q "version: ${SHELLCHECK_VERSION#v}"; then
  log "shellcheck $SHELLCHECK_VERSION"
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" \
    | tar -xJf - -C "$tmp"
  install "$tmp/shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck
  rm -rf "$tmp"
fi

# Python 3.12 venvs via uv (as setup.sh does):
#   dev      — pytest job: pytest pytest-xdist pyyaml pymupdf numpy pillow (+ pikepdf)
#   ocrmypdf — pinned ocrmypdf + pytest; the Makefile picks it up by itself
# Standalone installer: pip into system Python is refused under PEP 668.
if ! command -v uv >/dev/null 2>&1; then
  log "installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh >&2
  export PATH="$HOME/.local/bin:$PATH"
fi
make_venv() {
  local name=$1; shift
  local py="$VENV_ROOT/$name/bin/python"
  if [ ! -x "$py" ]; then
    log "venv $name (Python $PYTHON_VERSION)"
    uv venv -q --python "$PYTHON_VERSION" "$VENV_ROOT/$name"
  fi
  # A no-op in milliseconds once everything is installed.
  uv pip install -q --python "$py" "$@"
}
make_venv dev pytest pytest-xdist pyyaml pymupdf numpy pillow pikepdf
make_venv ocrmypdf pytest "ocrmypdf==$OCRMYPDF_VERSION"

# Plugin: npm ci only when package-lock.json changed (same stamp the Makefile uses).
if [ ! -f plugin/node_modules/.package-lock.json ] || [ plugin/package-lock.json -nt plugin/node_modules/.package-lock.json ]; then
  log "npm ci in plugin/"
  (cd plugin && npm ci --no-audit --no-fund --loglevel=error >&2)
fi

touch "$DONE"
log "ready: make check / make test-fast"
