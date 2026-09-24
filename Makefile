# One entry point for linting and testing every stage (Issue #107).
# CI (.github/workflows/ci.yml) calls these same targets, so `make check`
# runs locally what a pull request runs.
#
#   make check      everything CI runs: plugin, shellcheck, Python, OCRmyPDF
#   make test-fast  unit tests only (pytest -m "not slow" + plugin tests), a few seconds

PYTHON ?= python3
VENV_ROOT ?= $(HOME)/.venvs
# The pinned OCRmyPDF lives in its own venv (setup.sh); CI passes its python.
OCRMYPDF_PYTHON ?= $(firstword $(wildcard $(VENV_ROOT)/ocrmypdf/bin/python) $(PYTHON))
NPM ?= npm

# Tracked scripts only: an untracked Finder copy ("pdf-lib 2.sh") is not ours to lint.
SHELL_SCRIPTS := setup.sh install.sh $(shell git ls-files 'bin/*.sh') bin/pdf2md plugin/install-plugin.sh
PY_TESTS := pdf2md/test bin/test
BENCH_TESTS := bench/test_entrypoints.py bench/test_reading_order.py
NODE_MODULES := plugin/node_modules/.package-lock.json

.PHONY: check test-fast plugin lint-plugin test-plugin build-plugin shellcheck test-py test-ocrmypdf

check: plugin shellcheck test-py test-ocrmypdf

test-fast: $(NODE_MODULES)
	$(PYTHON) -m pytest $(PY_TESTS) -q -m "not slow"
	cd plugin && $(NPM) test

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
		{ echo "!! plugin/main.js is not versioned: the committed build is what ships (AGENTS.md)."; exit 1; }
	@git diff --quiet -- plugin/main.js || \
		{ echo "!! plugin/main.js differs from src/: commit the rebuilt main.js."; exit 1; }
	@echo "ok  plugin/main.js is versioned and matches src/"

shellcheck:
	shellcheck -x -P bin $(SHELL_SCRIPTS)

test-py:
	$(PYTHON) -m pytest $(PY_TESTS) -q
	$(PYTHON) -m pytest $(BENCH_TESTS) -q

# Without OCRMYPDF_PYTHON pointing at an ocrmypdf install these tests skip;
# CI sets REQUIRE_OCRMYPDF=1 so a missing install fails there instead.
test-ocrmypdf:
	$(OCRMYPDF_PYTHON) -m pytest bin/test/test_hocr_text_layer_order.py ocrmypdf_paddle/test -q
