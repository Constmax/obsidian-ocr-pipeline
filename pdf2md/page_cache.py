"""Persistent per-page cache for the Stage-2 OCR pipeline.

The cache stores model-independent JSON so Markdown assembly can be repeated
without loading MLX.  A cache entry is accepted only when its complete input
fingerprint matches; corrupt or outdated entries are treated as misses.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

from assembly import RecognizedLine


CACHE_SCHEMA = 1
# How an entry holds its recognized lines. Format 2: one object per line
# (Issue #144). Entries without `line_format` hold `[text, box, container?]`
# lists and, from Issue #14 on, a parallel `columns` array; they stay valid
# and are read through `recognized_lines()`.
LINE_FORMAT = 2


class CacheParameters(TypedDict, total=False):
    """Fingerprint of every input that affects a cached page result."""

    dpi: int
    tile_from: int
    bold: bool
    ocr_only: bool
    retries: int
    model: str | None
    model_revision: str
    prompt: str | None
    diagram_image_only: bool
    diagram_pages: list[int]
    # Textlayer pages only: version of the column assignment they carry.
    column_version: int


class CacheContext(TypedDict):
    """Complete, serializable fingerprint context for one run."""

    schema: int
    pdf_sha256: str
    parameters: CacheParameters


class _CachedPageRequired(TypedDict):
    number: int
    source: str
    characters: int
    layout: str
    mode: str
    lines: list[Any]
    trace: list[str]


class CachedPage(_CachedPageRequired, total=False):
    """One parsed page: its recognized lines plus page metadata.

    `write_page()` takes the lines as RecognizedLine records and stores them
    in `LINE_FORMAT`; read them back with `recognized_lines()`.
    """

    line_format: int


def line_json(line: RecognizedLine) -> dict:
    """One recognized line as format 2 stores it; None fields are left out."""
    return {key: value for key, value in asdict(line).items()
            if value is not None}


def recognized_lines(page: dict) -> list[RecognizedLine]:
    """The recognized lines of a page-cache entry or a page case's page.

    The one upgrade path for older entries: a line list `[text, box,
    container?]` takes its column from the entry's `columns` (Issue #14)
    where present, else None. Page cases call it too.
    """
    def box(value):
        return None if value is None else tuple(value)

    if page.get("line_format") == LINE_FORMAT:
        return [RecognizedLine(line["text"], box(line.get("box")),
                               line.get("column"), line.get("container"))
                for line in page["lines"]]
    lines = page["lines"]
    columns = page.get("columns")
    if not columns or len(columns) != len(lines):
        # As assembly read them: a columns array that does not fit is unknown.
        columns = [None] * len(lines)
    return [RecognizedLine(text, box(value), column, *rest[:1])
            for (text, value, *rest), column in zip(lines, columns)]


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_revision(model: str) -> str:
    """Resolve a stable local model revision without importing MLX.

    ``MLX_OCR_MODEL_REVISION`` is useful for explicitly pinned or remote
    models.  Hugging Face stores the resolved commit in ``refs/main``.  Local
    model directories use a digest of file names, sizes and mtimes, avoiding a
    second hash over multi-gigabyte weights.

    An unresolvable revision is a guaranteed cache miss, never a shared key:
    reusing a page parsed by an unknown model version would silently mix
    results (Issue #11), which is worse than losing the cache.
    """
    explicit = os.environ.get("MLX_OCR_MODEL_REVISION")
    if explicit:
        return explicit

    model_path = Path(model).expanduser()
    if model_path.is_dir():
        digest = hashlib.sha256()
        for path in sorted(p for p in model_path.rglob("*") if p.is_file()):
            stat = path.stat()
            digest.update(str(path.relative_to(model_path)).encode())
            digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
        return f"local-{digest.hexdigest()}"

    hub = (os.environ.get("HF_HUB_CACHE")
           or os.path.join(os.environ.get("HF_HOME", "~/.cache/huggingface"),
                           "hub"))
    repo = Path(os.path.expanduser(hub)) / f"models--{model.replace('/', '--')}"
    main_ref = repo / "refs" / "main"
    try:
        revision = main_ref.read_text(encoding="utf-8").strip()
    except OSError:
        revision = ""
    if revision:
        return revision

    snapshots = repo / "snapshots"
    try:
        names = sorted(path.name for path in snapshots.iterdir()
                       if path.is_dir())
    except OSError:
        names = []
    if names:
        return ",".join(names)
    return f"unresolved-{uuid.uuid4().hex}"


def build_context(pdf: Path, parameters: dict) -> dict:
    """Build the complete, serializable fingerprint context for one run."""
    return {
        "schema": CACHE_SCHEMA,
        "pdf_sha256": file_sha256(pdf),
        "parameters": parameters,
    }


def page_key(context: dict, page_number: int) -> str:
    """Return a deterministic cache key for a page and run context."""
    value = {"context": context, "page": page_number}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_directory(out: Path, pdf: Path) -> Path:
    """Return the cache directory prescribed by Issue #11."""
    return out / ".cache" / pdf.stem


def page_path(directory: Path, page_number: int) -> Path:
    """Return the JSON path for a one-based page number."""
    return directory / f"{page_number:03d}.json"


def _valid_box(box: Any) -> bool:
    """Check a thousandths box: None or four finite numbers."""
    if box is None:
        return True
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    return all(isinstance(value, (int, float)) for value in box)


def _valid_column(column: Any) -> bool:
    return column is None or (isinstance(column, int)
                               and not isinstance(column, bool))


def _valid_line(line: Any, line_format: int | None) -> bool:
    """Check one cached line: text, box, container and, in format 2, column."""
    if line_format == LINE_FORMAT:
        return (isinstance(line, dict) and isinstance(line.get("text"), str)
                and _valid_box(line.get("box"))
                and _valid_column(line.get("column"))
                and isinstance(line.get("container"), (str, type(None))))
    if not isinstance(line, (list, tuple)) or not 2 <= len(line) <= 3:
        return False
    text, box, *container = line
    return (isinstance(text, str) and _valid_box(box)
            and all(isinstance(value, str) for value in container))


def read_page(directory: Path, page_number: int, expected_key: str):
    """Read a valid page entry, returning ``None`` on any cache miss."""
    return _read_entry(directory, page_number, expected_key)


def read_latest_page(directory: Path, page_number: int):
    """Read the last result stored for a page, whatever run produced it.

    Only for describing a page, never for reusing it: a merged `--pages` run
    (Issue #106) counts a kept page's derailment and dictionary findings from
    it. There is one entry per page number, overwritten by every run that
    computes the page, so it belongs to the block the preview shows.
    """
    return _read_entry(directory, page_number, None)


def read_latest_context(directory: Path, page_number: int):
    """The fingerprint context of the run that stored a page's last result.

    A page case takes the source hash and `diagram_image_only` from it.
    ``None`` when the page has no readable entry.
    """
    if _read_entry(directory, page_number, None) is None:
        return None
    try:
        context = json.loads(page_path(directory, page_number).read_text(
            encoding="utf-8"))["context"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if (not isinstance(context, dict)
            or not isinstance(context.get("pdf_sha256"), str)
            or not isinstance(context.get("parameters"), dict)):
        return None
    return context


def _read_entry(directory: Path, page_number: int, expected_key: str | None):
    try:
        payload = json.loads(page_path(directory, page_number).read_text(
            encoding="utf-8"))
        page = payload["page"]
        if payload.get("schema") != CACHE_SCHEMA:
            return None
        if expected_key is not None and payload.get("key") != expected_key:
            return None
        lines = page.get("lines")
        trace = page.get("trace", [])
        if page.get("number") != page_number or not isinstance(lines, list):
            return None
        if page.get("source") not in {"ocr", "textlayer"}:
            return None
        if not isinstance(page.get("characters"), int):
            return None
        if not isinstance(page.get("layout"), str):
            return None
        if not isinstance(page.get("mode"), str):
            return None
        line_format = page.get("line_format")
        if line_format not in (None, LINE_FORMAT):
            return None
        if any(not _valid_line(line, line_format) for line in lines):
            return None
        columns = page.get("columns")
        if columns is not None and (
                line_format is not None
                or not isinstance(columns, list) or len(columns) != len(lines)
                or not all(_valid_column(c) for c in columns)):
            return None
        if not isinstance(trace, list) or any(not isinstance(item, str)
                                              for item in trace):
            return None
        return page
    except (OSError, ValueError, TypeError, KeyError):
        return None


def write_page(directory: Path, context: dict, page: dict) -> Path:
    """Atomically publish a page entry after it has finished processing.

    `page["lines"]` holds RecognizedLine records; they are stored in
    `LINE_FORMAT`.
    """
    page = {**page, "line_format": LINE_FORMAT,
            "lines": [line_json(line) for line in page["lines"]]}
    page_number = page["number"]
    directory.mkdir(parents=True, exist_ok=True)
    target = page_path(directory, page_number)
    payload = {
        "schema": CACHE_SCHEMA,
        "key": page_key(context, page_number),
        "context": context,
        "page": page,
    }
    write_text_atomic(
        target, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    return target


def write_text_atomic(target: Path, text: str) -> None:
    """Replace `target` so readers see the old or the new file, never a
    truncated one (Issue #105).

    The text goes to a hidden sibling first, which then replaces `target` in
    one rename. An exception, a full disk or an interrupt before the rename
    leaves the previous file untouched and removes the sibling; only SIGKILL
    can leave the hidden `.<name>.<pid>.tmp` behind.
    """
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
