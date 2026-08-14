"""Integration tests for --check preflight mode.

Run with: python3 -m pytest pdf2md/test/test_check.py -q
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
PDF2MD = REPO / "pdf2md" / "pdf2md.py"


def _run_check(*args):
    """Run pdf2md.py --check with given extra args."""
    return subprocess.run(
        [sys.executable, str(PDF2MD), "--check", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_human_output():
    """--check prints one line per check and exits 4 (mlx_vlm absent in CI)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "out"
        Ergebnis = _run_check("--out", str(out))

        assert Ergebnis.returncode == 4, (
            f"Expected exit 4 (mlx_vlm missing), got {Ergebnis.returncode}\n"
            f"stdout: {Ergebnis.stdout}\nstderr: {Ergebnis.stderr}"
        )

        lines = [l for l in Ergebnis.stdout.splitlines() if l.strip() and l.strip() != "—"]
        # Should have exactly 6 check lines (python, fitz, mlx_vlm, modell, ausgabe, speicher)
        check_lines = [l for l in lines
                       if l.startswith("[ ok ]") or l.startswith("[fehlt]")]
        assert len(check_lines) == 6, (
            f"Expected 6 check lines, got {len(check_lines)}:\n{check_lines}"
        )

        # Names should be present
        names = {"python", "fitz", "mlx_vlm", "modell", "ausgabe", "speicher"}
        found = set()
        for l in check_lines:
            for name in names:
                if f"] {name}:" in l:
                    found.add(name)
        assert found == names, f"Missing check names: {names - found}"

        # mlx_vlm should be fehlt
        mlx_line = [l for l in check_lines if "mlx_vlm" in l][0]
        assert "[fehlt]" in mlx_line, f"Expected mlx_vlm to be fehlt: {mlx_line}"

        # Summary line
        assert "fehlgeschlagen" in lines[-1].lower()


def test_json_output():
    """--check --fortschritt prints valid JSON on stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "out"
        Ergebnis = _run_check("--fortschritt", "--out", str(out))

        assert Ergebnis.returncode == 4, (
            f"Expected exit 4, got {Ergebnis.returncode}\n"
            f"stdout: {Ergebnis.stdout}\nstderr: {Ergebnis.stderr}"
        )

        doc = json.loads(Ergebnis.stdout)
        assert doc["typ"] == "check"
        assert doc["ok"] is False
        assert isinstance(doc["checks"], list)
        assert len(doc["checks"]) == 6
        assert isinstance(doc["warnungen"], list)

        names = {c["name"] for c in doc["checks"]}
        assert names == {"python", "fitz", "mlx_vlm", "modell", "ausgabe", "speicher"}

        mlx = [c for c in doc["checks"] if c["name"] == "mlx_vlm"][0]
        assert mlx["ok"] is False


def test_model_cache_detects_fake_dir():
    """Modellcheck findet einen fake HF-Cache ueber $HF_HUB_CACHE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_cache = Path(tmpdir) / "hub" / "models--mlx-community--PaddleOCR-VL-1.5-4bit"
        fake_cache.mkdir(parents=True)
        (fake_cache / "config.json").write_text('{}')

        env = {**os.environ, "HF_HUB_CACHE": str(Path(tmpdir) / "hub")}
        Ergebnis = subprocess.run(
            [sys.executable, str(PDF2MD), "--check", "--fortschritt",
             "--out", str(Path(tmpdir) / "out")],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
        )
        doc = json.loads(Ergebnis.stdout)
        modell = [c for c in doc["checks"] if c["name"] == "modell"][0]
        assert modell["ok"] is True, f"Modellcheck schlug fehl: {modell['detail']}"
        assert "lokal:" not in modell["detail"]


def test_modell_findet_default_cache_ueber_home():
    """Modellcheck findet den Default-Cache (~/.cache/huggingface/hub) via $HOME."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_pfad = (Path(tmpdir) / ".cache" / "huggingface" / "hub"
                      / "models--mlx-community--PaddleOCR-VL-1.5-4bit")
        cache_pfad.mkdir(parents=True)
        (cache_pfad / "config.json").write_text("{}")
        env = {k: v for k, v in os.environ.items()
               if k not in ("HF_HUB_CACHE", "HF_HOME")}
        env["HOME"] = tmpdir
        Ergebnis = subprocess.run(
            [sys.executable, str(PDF2MD), "--check", "--fortschritt",
             "--out", str(Path(tmpdir) / "out")],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
        )
        doc = json.loads(Ergebnis.stdout)
        modell = [c for c in doc["checks"] if c["name"] == "modell"][0]
        assert modell["ok"] is True, f"Modellcheck schlug fehl: {modell['detail']}"


def test_modell_groesse_zaehlt_blobs_nur_einfach():
    """Snapshots-Symlinks auf blobs/ werden nicht doppelt gezaehlt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hub = Path(tmpdir) / "hub"
        cache_pfad = hub / "models--mlx-community--PaddleOCR-VL-1.5-4bit"
        blobs = cache_pfad / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "abc").write_bytes(b"x" * 2048)
        snapshots = cache_pfad / "snapshots" / "main"
        snapshots.mkdir(parents=True)
        (snapshots / "modell.safetensors").symlink_to(blobs / "abc")
        env = {**os.environ, "HF_HUB_CACHE": str(hub)}
        Ergebnis = subprocess.run(
            [sys.executable, str(PDF2MD), "--check", "--fortschritt",
             "--out", str(Path(tmpdir) / "out")],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
        )
        doc = json.loads(Ergebnis.stdout)
        modell = [c for c in doc["checks"] if c["name"] == "modell"][0]
        assert modell["ok"] is True
        assert "(2.0 KB)" in modell["detail"], modell["detail"]


def test_no_pdf_with_check():
    """--check without a PDF argument succeeds (pdf not required)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Ergebnis = _run_check("--out", str(tmpdir))
        # Should not fail with "argparse error" about missing PDF
        assert "Erfordert eine PDF-Datei" not in (Ergebnis.stderr + Ergebnis.stdout)


def test_check_rejects_pdf_argument():
    """--check weist ein explizites PDF-Argument mit Meldung zurueck."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_pdf = Path(tmpdir) / "test.pdf"
        fake_pdf.touch()
        Ergebnis = _run_check("--out", str(tmpdir), str(fake_pdf))
        assert Ergebnis.returncode != 0
        assert "--check benoetigt keine PDF-Datei" in Ergebnis.stderr
