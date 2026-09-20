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
from pathlib import Path
from typing import Any, TypedDict


CACHE_SCHEMA = 1


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


class CacheContext(TypedDict):
    """Complete, serializable fingerprint context for one run."""

    schema: int
    pdf_sha256: str
    parameters: CacheParameters


class CachedPage(TypedDict):
    """One parsed page: lines with boxes plus page metadata."""

    number: int
    source: str
    characters: int
    layout: str
    mode: str
    lines: list[list[Any]]
    trace: list[str]


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


def _valid_line(line: Any) -> bool:
    """Check one cached line: text, box, and optional marker."""
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        return False
    if not isinstance(line[0], str):
        return False
    if not _valid_box(line[1]):
        return False
    if len(line) > 2 and not isinstance(line[2], str):
        return False
    return True


def read_page(directory: Path, page_number: int, expected_key: str):
    """Read a valid page entry, returning ``None`` on any cache miss."""
    try:
        payload = json.loads(page_path(directory, page_number).read_text(
            encoding="utf-8"))
        page = payload["page"]
        if payload.get("schema") != CACHE_SCHEMA:
            return None
        if payload.get("key") != expected_key:
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
        if any(not _valid_line(line) for line in lines):
            return None
        if not isinstance(trace, list) or any(not isinstance(item, str)
                                              for item in trace):
            return None
        return page
    except (OSError, ValueError, TypeError, KeyError):
        return None


def write_page(directory: Path, context: dict, page: dict) -> Path:
    """Atomically publish a page entry after it has finished processing."""
    page_number = page["number"]
    directory.mkdir(parents=True, exist_ok=True)
    target = page_path(directory, page_number)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = {
        "schema": CACHE_SCHEMA,
        "key": page_key(context, page_number),
        "context": context,
        "page": page,
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target
