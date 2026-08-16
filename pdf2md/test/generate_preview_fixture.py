#!/usr/bin/env python3
"""Generates fixture file plugin/test/fixtures/beispiel-vorschau.md
from pure assembly (assembly module + fixed inputs).

Uses pure functions build_frontmatter, page_marker, and build_document
from assembly.py — the same ones that pdf2md.py main() calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pdf2md"))

from assembly import build_frontmatter, page_marker, build_document


# ---------------------------------------------------------------------------
# Fixed inputs — assembly itself lives in assembly.build_frontmatter.
# ---------------------------------------------------------------------------
PDF_STEM = "Verwaltungsrecht AT Fall 8"
PDF_PATH = Path("raw/VwR/Verwaltungsrecht AT Fall 8.pdf")
PAGES = 3
PAGES_TEXTLAYER = 1
PAGES_OCR = 1
PAGES_DIAGRAM = 1
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"


def fixture_text() -> str:
    """Build complete preview text from pure functions."""
    frontmatter_text = build_frontmatter(
        title=PDF_STEM,
        source_pdf_path=PDF_PATH,
        pages=PAGES,
        pages_textlayer=PAGES_TEXTLAYER,
        pages_ocr=PAGES_OCR,
        pages_diagram=PAGES_DIAGRAM,
        ocr_model=MODEL,
        ocr_date="2026-07-30",
        ocr_timestamp="2026-08-14T15:30:29",
    )

    source_text = f"Quelle: [[{PDF_PATH.as_posix()}]]\n"

    block1 = page_marker(1, "textlayer") + (
        "**A. Zulässigkeit der Klage**\n\n"
        "Die Klage ist zulässig, wenn der Verwaltungsrechtsweg eröffnet ist.[^1]\n\n"
        "[^1]: § 40 Abs. 1 S. 1 VwGO."
    )
    block2 = page_marker(2, "ocr | zweispaltig, senkrecht @48%") + (
        "**I. Verwaltungsakt**\n\n"
        "Ein Verwaltungsakt liegt vor, § 35 S. 1 VwVfG. Ein Beispiel für einen "
        "Marker in einem Codeblock, der keine Seitengrenze sein darf:\n\n"
        "```\n%% S. 99 %%\nnoch immer Seite 2\n```\n"
        "Nach dem Codeblock geht Seite 2 weiter.[^1]\n\n"
        "[^1]: BVerwGE 100, 83."
    )
    block3 = page_marker(3, "diagramm") + (
        "![[Verwaltungsrecht-AT-Fall-8-s003.png]]\n\n"
        "> [!note]- Text der Seite (Reihenfolge nicht verlässlich)\n"
        "> Prüfungsaufbau Anfechtungsklage\n"
        "> >\n"
        "> 1. Zulässigkeit\n"
        "> > 2. Begründetheit"
    )

    return build_document(frontmatter_text, source_text, [block1, block2, block3])


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent.parent / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
    target.write_text(fixture_text(), encoding="utf-8")
    print(f"→ Fixture written to {target}")
