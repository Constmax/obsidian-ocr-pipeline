#!/usr/bin/env python3
"""Document conversion runner with explicit inputs, results, and run state."""

from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import dictionary
import page_cache
from assembly import (AssemblyContext, as_callout, assemble_paragraphs,
                      build_document, build_frontmatter, clean_text,
                      merge_fragments, page_marker)
from layout import (assign_boxes, detect_boxes, detect_layout, image_ratio,
                    split_columns, tables_markdown)
from ocr import (CHARACTERS_PER_INK, OVERLAP, TOKEN_MAX, _ink_amount,
                 tile_horizontally, tile_lines, tile_vertically, trim_overlap)


class OcrAdapter(Protocol):
    """Replaceable boundary around the ML OCR model."""

    def __call__(self, image: Path, max_tokens: int = TOKEN_MAX) -> str: ...

    def prepare(self) -> None:
        """Load whatever the first call would load, outside page timing."""


EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ConversionRequest:
    pdf: Path
    output_dir: Path
    dpi: int = 150
    tile_from: int = 3000
    no_bold: bool = False
    ocr_only: bool = False
    retries: int = 1
    lines_dump: Path | None = None
    no_dictionary: bool = False
    dictionaries: tuple[Path, ...] = ()
    dictionary_correct: bool = False
    dictionary_report: Path | None = None
    image_dir: Path | None = None
    image_max_edge: int = 1800
    forced_diagram_pages: frozenset[int] = frozenset()
    selected_pages: frozenset[int] | None = None
    diagram_image_only: bool = False
    model_name: str | None = None
    # Scan pages are rendered here. Deliberately not `output_dir`: that folder
    # is a vault folder in plugin runs, and Obsidian would index every
    # intermediate PNG. None means the system temporary directory.
    temp_root: Path | None = None
    # Part of the page-cache fingerprint (Issue #11), not of OCR behavior
    # itself — the adapter already has its own prompt baked in.
    ocr_prompt: str | None = None
    # Pages to force through recomputation even when the cache has a
    # matching, valid entry (--neu/--refresh-cache).
    refresh_pages: frozenset[int] = frozenset()
    cancel_requested: Callable[[], bool] = field(
        default=lambda: False, compare=False, repr=False)


@dataclass(frozen=True)
class AnalyzedPage:
    number: int
    image_path: Path | None
    text_characters: int
    layout_type: str
    gutter: float | None
    textlayer_lines: list[list[Any]] | None
    boxes: list[Any]
    is_diagram: bool

    @property
    def needs_ocr(self) -> bool:
        return self.image_path is not None


@dataclass(frozen=True)
class PageResult:
    number: int
    source: str
    paragraphs: list[str]
    discarded: list[str]
    trace: list[str]
    is_diagram: bool
    seconds: float
    text_characters: int


@dataclass(frozen=True)
class ConversionResult:
    target: Path | None
    markdown: str | None
    pages: tuple[PageResult, ...]
    total_pages: int
    completed: bool
    cancelled: bool
    # Page processing only — analysis and the one-off model load are excluded,
    # so s/page stays comparable across runs and against the benchmarks.
    seconds: float
    pages_textlayer: int = 0
    pages_ocr: int = 0
    pages_diagram: int = 0
    pages_derailed: int = 0
    words_suspect: int = 0
    words_corrected: int = 0
    lines_dump: tuple[dict[str, Any], ...] = ()
    dictionary_findings: tuple[dict[str, Any], ...] = ()


def running_lines(doc, header_zone=0.09, footer_zone=0.93, min_pages=2):
    """Return repeated header/footer texts for one document."""
    from collections import Counter

    counter = Counter()
    for i in range(doc.page_count):
        page = doc[i]
        height = page.rect.height or 1
        seen = set()
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                rel = ((line["bbox"][1] + line["bbox"][3]) / 2) / height
                if not (rel <= header_zone or rel >= footer_zone):
                    continue
                text = re.sub(
                    r"\s+", " ",
                    "".join(span["text"] for span in line["spans"]),
                ).strip()
                if len(text) < 6 or re.fullmatch(r"[\d\s\-–—.]+", text):
                    continue
                seen.add(text)
        counter.update(seen)
    return frozenset(text for text, count in counter.items() if count >= min_pages)


def textlayer_lines(page):
    """Read positioned lines from an existing PDF text layer."""
    width = page.rect.width or 1
    height = page.rect.height or 1
    tables = tables_markdown(page)
    frames = [table[2] for table in tables]

    def in_table(bbox):
        middle_x = (bbox[0] + bbox[2]) / 2
        middle_y = (bbox[1] + bbox[3]) / 2
        return any(x0 <= middle_x <= x1 and y0 <= middle_y <= y1
                   for x0, y0, x1, y1 in frames)

    lines, prose, rotated = [], [], []
    for _, markdown, bbox in tables:
        lines.append([
            markdown,
            (int(bbox[0] / width * 1000), int(bbox[1] / height * 1000),
             int(bbox[2] / width * 1000), int(bbox[3] / height * 1000)),
            "tabelle",
        ])
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            if in_table(line["bbox"]):
                continue
            parts = []
            for span in line["spans"]:
                text = clean_text(span["text"])
                if not text.strip():
                    parts.append(text)
                    continue
                if (text.strip() in ("o", "O")
                        and "courier" in span.get("font", "").lower()):
                    parts.append("-")
                    continue
                bold = (bool(span.get("flags", 0) & 16)
                        or "bold" in span.get("font", "").lower())
                before = text[:len(text) - len(text.lstrip())]
                after = text[len(text.rstrip()):]
                parts.append(f"{before}**{text.strip()}**{after}" if bold else text)
            text = "".join(parts).strip()
            if not text:
                continue
            target = prose if tuple(line.get("dir", (1, 0))) == (1, 0) else rotated
            target.append([text, tuple(line["bbox"])])
    for text, box in merge_fragments(prose, width) + rotated:
        lines.append([
            text,
            (int(box[0] / width * 1000), int(box[1] / height * 1000),
             int(box[2] / width * 1000), int(box[3] / height * 1000)),
        ])
    return lines


def analyze_pages(request: ConversionRequest, temporary_dir: Path):
    """Analyze selected pages and return named page records plus context."""
    import fitz

    pages: list[AnalyzedPage] = []
    with fitz.open(request.pdf) as doc:
        for page in doc:
            if page.rotation:
                page.remove_rotation()
        context = AssemblyContext(running_lines(doc))
        for index in range(doc.page_count):
            number = index + 1
            if (request.selected_pages is not None
                    and number not in request.selected_pages):
                continue
            page = doc[index]
            characters = len(page.get_text("text").strip())
            scan = image_ratio(page) >= 0.5 or characters < 100
            table_frames = [] if scan else [table[2] for table in tables_markdown(page)]
            boxes, diagram = detect_boxes(page, scan, table_frames)
            if not scan and not request.ocr_only:
                pages.append(AnalyzedPage(
                    number=number, image_path=None,
                    text_characters=characters, layout_type="vektoriell",
                    gutter=None, textlayer_lines=textlayer_lines(page),
                    boxes=boxes, is_diagram=diagram,
                ))
                continue
            image_path = temporary_dir / f"_seite{number:03d}.png"
            page.get_pixmap(dpi=request.dpi).save(image_path)
            layout_type, gutter = detect_layout(page)
            pages.append(AnalyzedPage(
                number=number, image_path=image_path,
                text_characters=characters, layout_type=layout_type,
                gutter=gutter, textlayer_lines=None, boxes=boxes,
                is_diagram=diagram,
            ))
    return pages, context


def diagram_image(pdf: Path, number: int, image_dir: Path, max_edge=1800):
    """Save a page image and return its filename and path."""
    import fitz

    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{pdf.stem}-s{number:03d}.png".replace(" ", "-")
    with fitz.open(pdf) as doc:
        page = doc[number - 1]
        long_side = max(page.rect.width, page.rect.height) or 1
        zoom = min(max_edge / long_side, 4.0)
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(image_dir / name)
    return name, image_dir / name


# Model-specific inputs never touch textlayer pages, so a model upgrade must
# not discard their cache (Issue #11 over-invalidation).
_TEXTLAYER_IGNORED_KEYS = frozenset({"model", "model_revision", "prompt"})


def _cache_context(request: ConversionRequest) -> dict:
    """Build the page-cache fingerprint (Issue #11) for one request."""
    return _cache_contexts(request)[0]


def _cache_contexts(request: ConversionRequest) -> tuple[dict, dict]:
    """Build full and textlayer page-cache fingerprints for one request."""
    parameters = {
        "dpi": request.dpi,
        "tile_from": request.tile_from,
        "bold": not request.no_bold,
        "ocr_only": request.ocr_only,
        "retries": request.retries,
        "model": request.model_name,
        "model_revision": (page_cache.model_revision(request.model_name)
                           if request.model_name else ""),
        "prompt": request.ocr_prompt,
        "diagram_image_only": request.diagram_image_only,
        "diagram_pages": sorted(request.forced_diagram_pages),
    }
    full = page_cache.build_context(request.pdf, parameters)
    textlayer = page_cache.build_context(
        request.pdf,
        {key: value for key, value in parameters.items()
         if key not in _TEXTLAYER_IGNORED_KEYS},
    )
    return full, textlayer


def _page_cache_key(page: AnalyzedPage, number: int, full: dict,
                    textlayer: dict) -> str:
    """Return the cache key matching a page's source (Issue #11)."""
    context = textlayer if page.textlayer_lines is not None else full
    return page_cache.page_key(context, number)


def _document_text(request, page_blocks, page_results, total_pages, model_name,
                   words_suspect, words_corrected, completed):
    pages_textlayer = sum(page.source == "textlayer" for page in page_results)
    pages_ocr = len(page_results) - pages_textlayer
    pages_diagram = sum(page.is_diagram for page in page_results)
    pages_derailed = sum(bool(page.trace) for page in page_results)
    aborted = None
    if not completed and page_results:
        aborted = f"seite {page_results[-1].number} von {total_pages}"
    header = build_frontmatter(
        title=request.pdf.stem,
        source_pdf_path=request.pdf,
        pages=len(page_results),
        pages_textlayer=pages_textlayer,
        pages_ocr=pages_ocr,
        pages_diagram=pages_diagram,
        pages_derailed=pages_derailed,
        words_suspect=words_suspect,
        words_corrected=words_corrected,
        ocr_model=model_name,
        ocr_date=date.today().isoformat(),
        ocr_timestamp=datetime.now().isoformat(timespec="seconds"),
        aborted=aborted,
    )
    source_link = f"Quelle: [[{request.pdf.as_posix()}]]\n"
    return build_document(header, source_link, page_blocks)


def _write_result(request, markdown, lines_dump, report, emit):
    """Write complete and partial artifacts through the same path."""
    target = request.output_dir / f"{request.pdf.stem}.md"
    target.write_text(markdown, encoding="utf-8")
    if request.lines_dump:
        request.lines_dump.write_text(
            json.dumps(lines_dump, ensure_ascii=False), encoding="utf-8")
        emit({"type": "artifact", "kind": "lines_dump",
              "path": request.lines_dump, "count": len(lines_dump),
              "unit": "pages"})
    if request.dictionary_report:
        request.dictionary_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        emit({"type": "artifact", "kind": "dictionary_report",
              "path": request.dictionary_report, "count": len(report),
              "unit": "findings"})
    return target


def convert_document(request: ConversionRequest, ocr_adapter: OcrAdapter | None,
                     event_sink: EventSink | None = None) -> ConversionResult:
    """Convert one PDF without argparse, console I/O, or process exits."""
    emit = event_sink or (lambda _event: None)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = request.image_dir or request.output_dir / "assets"
    if request.temp_root is not None:
        request.temp_root.mkdir(parents=True, exist_ok=True)
    emit({"type": "analysis_started", "file": request.pdf.name,
          "dpi": request.dpi, "selection": request.selected_pages})

    with tempfile.TemporaryDirectory(
            prefix=f"_tmp-{request.pdf.stem}-",
            dir=request.temp_root) as temp_name:
        analyzed, assembly_context = analyze_pages(request, Path(temp_name))
        if not analyzed:
            return ConversionResult(
                target=None, markdown=None, pages=(), total_pages=0,
                completed=False, cancelled=False, seconds=0.0,
            )
        ocr_page_count = sum(page.needs_ocr for page in analyzed)
        emit({"type": "analysis_complete", "pages": len(analyzed),
              "pages_textlayer": len(analyzed) - ocr_page_count,
              "pages_ocr": ocr_page_count})

        # Issue #11: a page whose complete input fingerprint (PDF content and
        # every parameter that affects its result) still matches is read back
        # from disk instead of recomputed. The fingerprint (which hashes the
        # whole PDF) is built here, before "start", so cancellation timing
        # right after "start" is unaffected — lookups themselves stay lazy,
        # one page at a time, rather than an upfront scan of every page.
        # Textlayer pages never touch the model, so their key excludes the
        # model name, revision, and prompt: a model upgrade must not discard
        # them. Both contexts are built here; per-page selection stays lazy.
        cache_dir = page_cache.cache_directory(request.output_dir, request.pdf)
        cache_context, textlayer_cache_context = _cache_contexts(request)

        emit({"type": "start", "file": request.pdf.name,
              "pages": len(analyzed), "dpi": request.dpi})

        def cache_hit(page: AnalyzedPage):
            if page.number in request.refresh_pages:
                return None
            expected_source = ("textlayer" if page.textlayer_lines is not None
                               else "ocr")
            entry = page_cache.read_page(
                cache_dir, page.number,
                _page_cache_key(page, page.number, cache_context,
                                textlayer_cache_context))
            if entry is not None and entry["source"] == expected_source:
                return entry
            return None

        wordbook = None
        if ocr_page_count and not request.no_dictionary:
            wordbook = dictionary.load(list(request.dictionaries))
            emit({"type": "dictionary", "wordbook": wordbook,
                  "correct": request.dictionary_correct})

        # Load the model before the clock starts, so neither the first page's
        # duration nor the total carries the one-off load. Pages the cache
        # already satisfies never reach the adapter at all.
        needs_adapter = any(
            page.needs_ocr
            and not ((page.is_diagram or page.number in request.forced_diagram_pages)
                     and request.diagram_image_only)
            and cache_hit(page) is None
            for page in analyzed
        )
        if needs_adapter and ocr_adapter is not None:
            prepare = getattr(ocr_adapter, "prepare", None)
            if prepare is not None:
                prepare()
        started = time.perf_counter()
        reused_count = 0

        page_blocks: list[str] = []
        page_results: list[PageResult] = []
        lines_dump: list[dict[str, Any]] = []
        report: list[dict[str, Any]] = []
        words_suspect = words_corrected = 0
        stop_requested = False

        for page in analyzed:
            if request.cancel_requested():
                stop_requested = True
                break
            page_started = time.perf_counter()
            diagram = (page.is_diagram
                       or page.number in request.forced_diagram_pages)
            trace: list[str] = []
            findings = []
            image_only = False
            entry = cache_hit(page)
            reused = entry is not None

            if reused:
                reused_count += 1
                lines = entry["lines"]
                trace = list(entry.get("trace", []))
                source = entry["source"]
                layout_type = entry["layout"]
                mode = entry["mode"]
                image_only = mode == "image-only"
                source_detail = ("" if image_only else
                                 "Textlayer, without model" if source == "textlayer"
                                 else f"{layout_type}, {mode}")
            elif page.textlayer_lines is not None:
                lines = split_columns(assign_boxes(page.textlayer_lines, page.boxes))
                source = "textlayer"
                layout_type, mode = page.layout_type, "textlayer"
                source_detail = "Textlayer, without model"
            elif diagram and request.diagram_image_only:
                lines = []
                source = "ocr"
                layout_type, mode = page.layout_type, "image-only"
                source_detail = ""
                image_only = True
            else:
                if ocr_adapter is None:
                    raise RuntimeError("OCR pages require an OCR adapter")
                if page.layout_type == "zweispaltig":
                    mode = f"senkrecht @{page.gutter:.0%}"
                    overlap = int(OVERLAP * 1000)
                    gutter = int(page.gutter * 1000)
                    tiles = list(zip(
                        tile_vertically(page.image_path, page.gutter),
                        [(0, min(gutter + overlap, 1000)),
                         (max(gutter - overlap, 0), 1000)],
                    ))
                elif page.text_characters >= request.tile_from:
                    mode = "waagerecht"
                    tiles = [(path, None)
                             for path, _, _ in tile_horizontally(page.image_path)]
                else:
                    mode = "ganz"
                    tiles = [(page.image_path, (0, 1000))]

                ink = _ink_amount(page.image_path, request.dpi)
                calibrated = page.text_characters >= 400 and ink > 0
                factor = (page.text_characters / ink
                          if calibrated else CHARACTERS_PER_INK)
                lines = []
                for part, window in tiles:
                    parsed, tile_trace = tile_lines(
                        part, ocr_adapter, not request.no_bold, factor,
                        request.dpi, calibrated, max_depth=request.retries,
                    )
                    trace += tile_trace
                    if window:
                        parsed = assign_boxes(parsed, page.boxes, window)
                    ordered = (split_columns(parsed) if len(tiles) == 1
                               else sorted(parsed,
                                           key=lambda line: line[1][1]
                                           if line[1] else 0))
                    lines += trim_overlap(lines, ordered)
                source = "ocr"
                layout_type = page.layout_type
                source_detail = f"{page.layout_type}, {mode}"

            if not reused:
                write_context = (textlayer_cache_context
                                 if source == "textlayer" else cache_context)
                cached_page: page_cache.CachedPage = {
                    "number": page.number, "source": source,
                    "characters": page.text_characters, "layout": layout_type,
                    "mode": mode, "lines": lines, "trace": trace,
                }
                page_cache.write_page(cache_dir, write_context, cached_page)

            if not image_only:
                lines_dump.append({"seite": page.number,
                                   "quelle": source if source == "textlayer"
                                   else source_detail, "zeilen": lines})
            assembled = assemble_paragraphs(lines, assembly_context)
            paragraphs = assembled.paragraphs
            if source == "ocr" and lines:
                paragraphs, findings = dictionary.check(
                    paragraphs, wordbook, request.dictionary_correct)
                words_corrected += sum(item.count for item in findings
                                       if item.corrected)
                words_suspect += sum(item.count for item in findings
                                     if not item.corrected)
                report += [
                    {"seite": page.number, "wort": item.word,
                     "anzahl": item.count, "vorschlag": item.suggestion,
                     "korrigiert": item.corrected}
                    for item in findings
                ]

            marker_extra = "diagramm" if diagram else (
                "textlayer" if source == "textlayer"
                else f"ocr | {source_detail}")
            block = page_marker(page.number, marker_extra)
            image_path = None
            if diagram:
                name, image_path = diagram_image(
                    request.pdf, page.number, image_dir, request.image_max_edge)
                parts = [f"![[{name}]]"]
                if not request.diagram_image_only and paragraphs:
                    parts.append(as_callout(
                        paragraphs,
                        "Text der Seite (Reihenfolge nicht verlässlich)",
                    ))
                block += "\n\n".join(parts)
            else:
                block += "\n\n".join(paragraphs)
            page_blocks.append(block)
            seconds = 0.0 if reused else time.perf_counter() - page_started
            result = PageResult(
                number=page.number, source=source, paragraphs=paragraphs,
                discarded=assembled.discarded, trace=trace,
                is_diagram=diagram, seconds=seconds,
                text_characters=page.text_characters,
            )
            page_results.append(result)
            emit({"type": "page", "page": result, "total_pages": len(analyzed),
                  "source_detail": source_detail + (" (cache)" if reused else ""),
                  "findings": findings, "image_path": image_path,
                  "cached": reused})

        if reused_count:
            emit({"type": "cache", "reused": reused_count, "directory": cache_dir})

        completed = len(page_results) == len(analyzed)
        cancelled = stop_requested and not completed
        elapsed = time.perf_counter() - started
        if not page_results:
            emit({"type": "cancelled", "written": 0,
                  "total_pages": len(analyzed), "target": None})
            return ConversionResult(
                target=None, markdown=None, pages=(),
                total_pages=len(analyzed), completed=False,
                cancelled=cancelled, seconds=elapsed,
            )

        markdown = _document_text(
            request, page_blocks, page_results, len(analyzed),
            request.model_name if ocr_page_count else None,
            words_suspect, words_corrected, completed,
        )
        target = _write_result(request, markdown, lines_dump, report, emit)
        pages_textlayer = sum(page.source == "textlayer" for page in page_results)
        result = ConversionResult(
            target=target, markdown=markdown, pages=tuple(page_results),
            total_pages=len(analyzed), completed=completed,
            cancelled=cancelled, seconds=elapsed,
            pages_textlayer=pages_textlayer,
            pages_ocr=len(page_results) - pages_textlayer,
            pages_diagram=sum(page.is_diagram for page in page_results),
            pages_derailed=sum(bool(page.trace) for page in page_results),
            words_suspect=words_suspect, words_corrected=words_corrected,
            lines_dump=tuple(lines_dump), dictionary_findings=tuple(report),
        )
        emit({"type": "complete" if completed else "cancelled",
              "result": result, "written": len(page_results),
              "total_pages": len(analyzed), "target": target})
        return result
