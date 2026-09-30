"""install-paddle.sh: PaddleOCR into the Stage-1 venv without risking it (#72).

pip, the venv's Python and ocrmypdf are stubs that log their calls. The
script must install with the current packages as constraints (so pip cannot
change what Apple Vision and Tesseract run on), prefetch the models, and run
the readiness check and an offline smoke test. Any failing step stops it
with exit 1 and a recovery command; nothing is uninstalled or upgraded.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "install-paddle.sh"
FROZEN = ["ocrmypdf==17.8.0", "ocrmypdf-appleocr==0.3.4", "pikepdf==10.9.1"]

pytestmark = pytest.mark.slow  # runs the installer end to end


def _stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    venv = tmp_path / "venvs" / "ocrmypdf" / "bin"
    log = tmp_path / "calls.log"
    frozen = "\\n".join(FROZEN)
    _stub(venv / "pip", f'''
echo "pip $*" >> "$FAKE_LOG"
case "$1" in
    freeze) printf '{frozen}\\n' ;;
    install)
        for a in "$@"; do case "$prev" in -c) cp "$a" "$FAKE_CONSTRAINTS" ;; esac; prev="$a"; done
        [ -z "${{FAKE_FAIL:-}}" ] || [ "$FAKE_FAIL" != install ] ;;
    check) [ "${{FAKE_FAIL:-}}" != check ] ;;
esac
''')
    _stub(venv / "python", '''
echo "python $*" >> "$FAKE_LOG"
case "$*" in
    *fetch-models*) [ "${FAKE_FAIL:-}" != fetch ] ;;
    "- "*) cat > /dev/null; : > "$2" ;;  # the smoke page
    *) exit 1 ;;
esac
''')
    _stub(venv / "ocrmypdf", '''
echo "ocrmypdf $*" >> "$FAKE_LOG"
case "$*" in
    *--paddle-check*) [ "${FAKE_FAIL:-}" != check-engine ] ;;
    *) [ "${FAKE_FAIL:-}" != smoke ] || exit 1
       cp "${@: -2:1}" "${@: -1}" ;;
esac
''')
    _stub(tmp_path / "stubs" / "pdftotext", 'echo "Paddle Probe § 823 BGB"\n')
    env = dict(os.environ, VENV_ROOT=str(tmp_path / "venvs"), FAKE_LOG=str(log),
               FAKE_CONSTRAINTS=str(tmp_path / "constraints.txt"),
               PATH=f"{tmp_path / 'stubs'}{os.pathsep}{os.environ['PATH']}")

    def run(**fake):
        return subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=60,
            env=dict(env, **{f"FAKE_{k.upper()}": v for k, v in fake.items()}))

    def calls():
        return log.read_text().splitlines() if log.exists() else []

    return type("Box", (), {"run": staticmethod(run), "calls": staticmethod(calls),
                            "constraints": tmp_path / "constraints.txt"})


def test_installs_with_the_current_packages_as_constraints(box):
    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    [install] = [c for c in box.calls() if c.startswith("pip install")]
    assert f"-e {REPO}/ocrmypdf_paddle[fast]" in install
    assert box.constraints.read_text().split() == FROZEN
    assert "--upgrade" not in install and " -U " not in install


def test_prefetches_models_and_checks_both_modes_offline(box):
    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    calls = box.calls()
    assert "python -m ocrmypdf_paddle fetch-models" in calls
    checks = [c for c in calls if "--paddle-check" in c]
    assert [c.split()[-1] for c in checks] == ["accurate", "fast"]
    [smoke] = [c for c in calls if c.startswith("ocrmypdf") and "--paddle-check" not in c]
    assert "--plugin ocrmypdf_paddle --paddle-mode fast" in smoke
    assert "PaddleOCR ready" in result.stdout


@pytest.mark.parametrize("step,recovery", [
    ("install", "install-paddle.sh"),
    ("check", "pip uninstall -y ocrmypdf-paddle"),
    ("fetch", "python -m ocrmypdf_paddle fetch-models"),
    ("check-engine", "--plugin ocrmypdf_paddle --paddle-check accurate"),
    ("smoke", "install-paddle.sh"),
])
def test_a_failed_step_stops_with_a_recovery_command(box, step, recovery):
    result = box.run(fail=step)

    assert result.returncode == 1, result.stdout
    assert recovery in result.stderr
    assert "Apple Vision and Tesseract are unchanged" in result.stderr
    # Nothing of the existing environment is touched on the way out.
    assert not [c for c in box.calls() if c.startswith("pip uninstall")]
    assert not [c for c in box.calls() if c.startswith("pip install") and "-U" in c.split()]
