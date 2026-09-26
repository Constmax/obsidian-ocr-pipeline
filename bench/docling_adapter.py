#!/usr/bin/env python3
"""Docling evaluation adapter for issue #95.

Time-boxed spike, not a migration: this module lets the benchmark measure
Docling's document pipeline against the current ``pdf2md/`` path without
changing any production code path. Production code lives in ``pdf2md/``,
``bin/`` and ``plugin/``; this adapter lives in ``bench/`` and is imported
only by the spike harness (``bench/spike_docling.py``) and its tests.

Scope (issue #95):

- Docling runs with its documented local/ARM64 defaults. No configuration
  tuning beyond those defaults lives here; :func:`converter_kwargs` returns
  the empty mapping deliberately, so a future reader sees that defaults were
  used rather than guessing which options were set.
- One single-page PDF in, one Markdown string out. The spike feeds the same
  single-page PDFs that ``bench/reading_order.py prepare`` builds to both
  paths, so per-page Markdown is directly comparable and no page-splitting
  of a multi-page Docling document is needed.
- The Markdown text is fed to ``bench/reading_order.score_page`` unchanged.
  That scorer normalises to letters, digits and ``§``, so Markdown syntax
  itself does not affect the ordering measurement.

The module imports only the standard library at top level so that the CI
smoke check (``bench/test_entrypoints.py``) and the unit tests pass without
Docling installed. Every ``docling`` import is function-local; when Docling
is missing the helpers raise a ``RuntimeError`` whose message tells the user
how to install it, and :func:`missing_dependencies` reports the reason so the
spike can be closed with that finding (first completion criterion).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

#: What the spike means by "documented local/ARM64 defaults": a plain
#: ``DocumentConverter`` with no pipeline overrides. Docling supports macOS
#: on both x86_64 and arm64 (``pip install docling``), runs locally, and
#: downloads its models on first use. The standard pipeline is the default;
#: the VLM pipeline (``--pipeline vlm``) is explicitly out of scope.
PIPELINE = "standard"
INSTALL_HINT = "pip install docling  (Python >= 3.10; works on macOS arm64)"


@dataclass(frozen=True)
class DoclingPageRecord:
    """One converted single-page PDF: Markdown plus its own measurement."""

    page_id: str
    source_pdf: str
    markdown: str
    chars: int
    seconds: float


def missing_dependencies() -> list[str]:
    """Reasons Docling cannot run here; empty when it can.

    Pure and side-effect free: importing this module never touches Docling,
    so this check is safe in CI without the dependency installed.
    """
    try:
        import docling  # noqa: F401
    except ImportError:
        return [f"docling is not installed ({INSTALL_HINT})"]
    return []


def docling_available() -> bool:
    """True when :func:`missing_dependencies` reports nothing."""
    return not missing_dependencies()


def docling_version() -> str | None:
    """Installed Docling version, or None when it is not installed."""
    try:
        from importlib.metadata import version
    except ImportError:  # Python < 3.8, not a supported setup
        return None
    try:
        return version("docling")
    except Exception:
        return None


def converter_kwargs() -> dict:
    """Keyword arguments for ``DocumentConverter``.

    Empty on purpose: the spike measures Docling's documented local/ARM64
    defaults. Any tuning beyond those defaults is out of scope (issue #95)
    and must not be smuggled in here.
    """
    return {}


def convert_pdf_to_markdown(pdf: Path) -> str:
    """Convert one (single-page) PDF to Markdown with default settings.

    Raises ``RuntimeError`` with an install hint when Docling is missing,
    and ``FileNotFoundError`` for a missing input.
    """
    source = Path(pdf)
    if not source.is_file():
        raise FileNotFoundError(f"not found: {source}")
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as error:
        raise RuntimeError(
            f"Docling is not installed ({INSTALL_HINT})") from error
    converter = DocumentConverter(**converter_kwargs())
    result = converter.convert(str(source))
    return result.document.export_to_markdown()


def convert_pages(pdfs: list[Path]) -> list[DoclingPageRecord]:
    """Convert single-page PDFs, timing each conversion separately.

    The one-off model download and converter construction happen inside the
    first :func:`convert_pdf_to_markdown` call and are therefore included in
    the first page's time; the harness records cold-to-ready separately when
    it needs that split (see ``bench/spike_docling.py``). For the benchmark
    itself every page is converted in document order and timed with
    ``time.perf_counter``.
    """
    records = []
    for pdf in pdfs:
        page_id = Path(pdf).stem
        started = time.perf_counter()
        markdown = convert_pdf_to_markdown(pdf)
        seconds = time.perf_counter() - started
        records.append(DoclingPageRecord(
            page_id=page_id,
            source_pdf=str(pdf),
            markdown=markdown,
            chars=len(markdown.strip()),
            seconds=seconds,
        ))
    return records


def page_records_to_texts(
        records: list[DoclingPageRecord]) -> dict[str, str]:
    """Map page id to Markdown text consumable by ``score_page``.

    ``bench/reading_order.score_page(truth, extracted)`` takes the extracted
    page text as a plain string; Markdown decorations do not affect the score
    because the scorer normalises to letters, digits and ``§``.
    """
    return {record.page_id: record.markdown for record in records}


def records_to_jsonable(
        records: list[DoclingPageRecord]) -> list[dict]:
    """Serialise page records for ``result.json``."""
    return [asdict(record) for record in records]
