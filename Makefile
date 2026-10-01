# One entry point for linting and testing every stage (Issue #107).
# CI (.github/workflows/ci.yml) calls these same targets, so `make check`
# runs locally what a pull request runs.
#
#   make check      everything CI runs: plugin, shellcheck, Python, OCRmyPDF;
#                   one run per machine at a time (CHECK_LOCK)
#   make test-fast  unit tests only (pytest -m "not slow" + plugin tests), a few seconds
#   make check-cases [ISSUE=n] [PROMOTE=1]
#                   replay the page cases of the vault (VAULT_ROOT); not part of check or CI

PYTHON ?= python3
VENV_ROOT ?= $(HOME)/.venvs
# The pinned OCRmyPDF lives in its own venv (setup.sh); CI passes its python.
# setup.sh installs no pytest there, so the venv is used only if it has one.
OCRMYPDF_VENV_PYTHON := $(VENV_ROOT)/ocrmypdf/bin/python
OCRMYPDF_PYTHON ?= $(firstword $(shell "$(OCRMYPDF_VENV_PYTHON)" -c 'import pytest' 2>/dev/null && echo "$(OCRMYPDF_VENV_PYTHON)") $(PYTHON))
# Page cases are replayed with Stage 2's own venv (pymupdf; no model is loaded).
CASES_PYTHON ?= $(VENV_ROOT)/mlxocr/bin/python
NPM ?= npm
# Several sessions share this machine: concurrent `make check` runs queue on a
# machine-wide lock instead of overloading it (and tripping test timeouts).
CHECK_LOCK ?= /tmp/obsidian-ocr-pipeline-check.lock
LOCK_CMD := $(if $(shell command -v lockf),lockf -k $(CHECK_LOCK),$(if $(shell command -v flock),flock $(CHECK_LOCK)))
# pytest-xdist spreads the Python tests over all cores; without it they run serially.
XDIST = $(if $(shell $(PYTHON) -c 'import xdist' 2>/dev/null && echo y),-n auto)

# Tracked scripts only: an untracked Finder copy ("pdf-lib 2.sh") is not ours to lint.
SHELL_SCRIPTS := setup.sh install.sh install-paddle.sh .claude/hooks/session-start.sh $(shell git ls-files 'bin/*.sh') bin/pdf2md plugin/install-plugin.sh
PY_TESTS := pdf2md/test bin/test
BENCH_TESTS := bench/test_entrypoints.py bench/test_reading_order.py bench/test_structure.py
NODE_MODULES := plugin/node_modules/.package-lock.json

.PHONY: check check-unlocked check-cases test-fast plugin lint-plugin test-plugin build-plugin shellcheck test-py test-ocrmypdf

check:
	@echo "make check: waiting for $(CHECK_LOCK) if another run holds it"
	$(LOCK_CMD) $(MAKE) --no-print-directory check-unlocked

check-unlocked: plugin shellcheck test-py test-ocrmypdf

test-fast: test-plugin
	$(PYTHON) -m pytest $(PY_TESTS) -q -m "not slow"

# Cases hold page text and stay in the vault, so this cannot run in CI. It
# never skips: without a vault or a venv it stops and says what is missing.
check-cases:
	@test -n "$(VAULT_ROOT)" || \
		{ echo "!! VAULT_ROOT is unset: page cases live in the vault (<preview folder>/.cases/). Run: make check-cases VAULT_ROOT=<vault>"; exit 1; }
	@test -x "$(CASES_PYTHON)" || \
		{ echo "!! $(CASES_PYTHON) not found: run ./setup.sh, or pass CASES_PYTHON=<python with pymupdf>"; exit 1; }
	"$(CASES_PYTHON)" pdf2md/pdf2md.py case run "$(VAULT_ROOT)" $(if $(ISSUE),--issue $(ISSUE)) $(if $(PROMOTE),--promote)

plugin: lint-plugin test-plugin build-plugin

$(NODE_MODULES): plugin/package-lock.json
	cd plugin && $(NPM) ci

lint-plugin: $(NODE_MODULES)
	cd plugin && $(NPM) run check && $(NPM) run lint

test-plugin: $(NODE_MODULES)
	cd plugin && $(NPM) test

# main.js is committed so a clone runs without Node; it must match src/.
build-plugin: $(NODE_MODULES)
	cd plugin && $(NPM) run build
	@git ls-files --error-unmatch plugin/main.js >/dev/null 2>&1 || \
		{ echo "!! plugin/main.js is not versioned: the committed build is what ships (plugin/AGENTS.md)."; exit 1; }
	@git --no-pager diff --exit-code -- plugin/main.js || \
		{ echo "!! plugin/main.js differs from src/: commit the rebuilt main.js."; exit 1; }
	@echo "ok  plugin/main.js is versioned and matches src/"

shellcheck:
	shellcheck -x -P bin $(SHELL_SCRIPTS)

test-py:
	$(PYTHON) -m pytest $(PY_TESTS) -q $(XDIST)
	$(PYTHON) -m pytest $(BENCH_TESTS) -q

# Without OCRMYPDF_PYTHON pointing at an ocrmypdf install these tests skip;
# CI sets REQUIRE_OCRMYPDF=1 so a missing install fails there instead.
test-ocrmypdf:
	$(OCRMYPDF_PYTHON) -m pytest bin/test/test_hocr_text_layer_order.py ocrmypdf_paddle/test -q
