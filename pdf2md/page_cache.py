"""Persistent per-page results for resumable Stage 2 conversions."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


CACHE_VERSION = 1
REQUIRED_RESULT_FIELDS = {
    "source",
    "chars",
    "layout_type",
    "gutter",
    "boxes",
    "diagram",
    "lines",
    "trace",
    "mode",
}


def _valid_result(result):
    if not isinstance(result, dict) or not REQUIRED_RESULT_FIELDS.issubset(result):
        return False
    return (
        result["source"] in {"textlayer", "ocr"}
        and isinstance(result["chars"], int)
        and isinstance(result["layout_type"], str)
        and (result["gutter"] is None
             or isinstance(result["gutter"], (int, float)))
        and isinstance(result["boxes"], list)
        and isinstance(result["diagram"], bool)
        and isinstance(result["lines"], list)
        and isinstance(result["trace"], list)
        and all(isinstance(item, str) for item in result["trace"])
        and isinstance(result["mode"], str)
    )


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_key(pdf, parameters):
    """Return a stable key for the PDF content and page-affecting options."""
    payload = {
        "cache_version": CACHE_VERSION,
        "pdf_sha256": _file_sha256(pdf),
        "parameters": parameters,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PageCache:
    """Read and atomically write page results for one document configuration."""

    def __init__(self, output_dir, pdf, parameters):
        self.directory = Path(output_dir) / ".cache" / Path(pdf).stem
        self.key = document_key(pdf, parameters)

    def path_for(self, page_number):
        return self.directory / f"{page_number:04d}.json"

    def load(self, page_number):
        path = self.path_for(page_number)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            record.get("cache_version") != CACHE_VERSION
            or record.get("document_key") != self.key
            or record.get("page") != page_number
        ):
            return None
        result = record.get("result")
        if not _valid_result(result):
            return None
        return result

    def store(self, page_number, result):
        if not _valid_result(result):
            raise ValueError("invalid cache result")
        record = {
            "cache_version": CACHE_VERSION,
            "document_key": self.key,
            "page": page_number,
            "result": result,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{page_number:04d}-",
                suffix=".tmp",
                delete=False,
            ) as target:
                temporary = Path(target.name)
                json.dump(record, target, ensure_ascii=False, separators=(",", ":"))
                target.write("\n")
            os.replace(temporary, self.path_for(page_number))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
