"""Legt pdf2md/ auf sys.path, damit die Tests die Module ohne Installation
importieren koennen.

Bewusst KEIN __init__.py: pdf2md.py laeuft weiterhin als Skript, und die
Vault-Kopie bleibt flach (siehe bench/pfade.py, Zwei-Orte-Konvention).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def make_vector_pdf():
    """Write a PDF whose pages carry a text layer: `make(path, pages=2)`."""
    import fitz

    def make(path: Path, pages=2):
        with fitz.open() as doc:
            for number in range(1, pages + 1):
                page = doc.new_page(width=600, height=800)
                page.insert_text(
                    fitz.Point(20, 100), f"Page {number}: " + "x" * 180,
                    fontsize=5)
            doc.save(path)

    return make
