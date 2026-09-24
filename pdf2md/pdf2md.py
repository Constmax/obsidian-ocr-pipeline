#!/usr/bin/env python3
"""Command-line adapter for the PDF-to-Markdown conversion runner."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import cancellation
from conversion import (INPUT_SUFFIXES, ConversionRequest, PreviewFormatError,
                        UnsupportedInput, convert_document,
                        ensure_supported_input, open_document)
from ocr import TOKEN_MAX
from page_range import PageRangeError, parse_page_range

BENCH = Path(__file__).resolve().parent
OUT = BENCH / "out-C"
MODEL = os.environ.get("MLX_OCR_MODEL", "mlx-community/PaddleOCR-VL-1.5-4bit")
PROMPT = "Parse this document page to Markdown."
TILE_THRESHOLD = 3000
KACHEL_AB = TILE_THRESHOLD
EXIT_CHECK = 4
# Cancellation (Issues #25, #105): a partial file was written, or none was.
EXIT_CANCELLED_PARTIAL = 6
EXIT_CANCELLED_EMPTY = 7


def _progress(event):
    sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024


def _hf_cache_path(repo_id):
    cache = (os.environ.get("HF_HUB_CACHE")
             or (os.environ.get("HF_HOME", "~/.cache/huggingface") + "/hub"))
    return Path(os.path.expanduser(cache)) / f"models--{'--'.join(repo_id.split('/', 1))}"


def preflight(out):
    """Run dependency and environment checks without loading the model."""
    checks, warnings = [], []
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(("python", sys.version_info >= (3, 9), version))
    try:
        import pymupdf as fitz
        checks.append(("fitz", True, getattr(fitz, "__version__", "?")))
    except ImportError:
        checks.append(("fitz", False, "nicht installiert"))
    try:
        import mlx_vlm
        checks.append(("mlx_vlm", True, getattr(mlx_vlm, "__version__", "?")))
    except ImportError:
        checks.append(("mlx_vlm", False, "nicht installiert"))
    model_path = Path(MODEL)
    if model_path.is_dir():
        checks.append(("modell", True, f"lokal: {model_path}"))
    else:
        cache_path = _hf_cache_path(MODEL)
        ok = cache_path.exists() and any(cache_path.iterdir())
        detail = str(cache_path) if ok else f"nicht im Cache ({cache_path})"
        if ok:
            blobs = cache_path / "blobs"
            files = blobs.glob("*") if blobs.is_dir() else cache_path.rglob("*")
            size = sum(path.stat().st_size for path in files
                       if path.is_file() and not path.is_symlink())
            detail += f" ({_human_size(size)})"
        checks.append(("modell", ok, detail))
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".check-probe"
        probe.write_text("ok")
    except OSError as error:
        checks.append(("ausgabe", False, str(error)))
    else:
        try:
            probe.unlink()
        except OSError:
            pass
        checks.append(("ausgabe", True, str(out)))
    try:
        import subprocess
        memory = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], text=True).strip()) / (1024 ** 3)
        detail = f"{memory:.1f} GiB"
        if memory < 8:
            detail += " (weniger als 8 GiB — Parallelbetrieb riskiert OOM)"
            warnings.append("speicher: " + detail)
        checks.append(("speicher", True, detail))
    except Exception:
        checks.append(("speicher", True, "nicht bestimmbar"))
    return checks, warnings


def page_count_of(source):
    """Pages of the input — always 1 for an image, or UnsupportedInput.

    A multi-frame image has no honest page count here (Issue #100), so
    `open_document()` rejects it rather than reporting one.
    """
    with open_document(source) as doc:
        return doc.page_count


def page_option(flag, text, page_count):
    try:
        return parse_page_range(text, page_count)
    except PageRangeError as error:
        sys.exit(f"{flag}: {error}")


class LazyMlxOcrAdapter:
    """Load the model only when the run actually needs it.

    The runner calls prepare() once it knows OCR pages exist and before it
    starts timing pages, so the one-off load stays out of the per-page and
    total durations.
    """

    def __init__(self, model_name):
        self.model_name = model_name
        self._generate = None

    def prepare(self):
        if self._generate is None:
            self._load()

    def _load(self):
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        model, processor = load(self.model_name)
        formatted = apply_chat_template(
            processor, load_config(self.model_name), PROMPT, num_images=1)

        def generate_text(image, max_tokens):
            result = generate(
                model, processor, formatted, image=[str(image)],
                max_tokens=max_tokens, temperature=0.0, verbose=False)
            return (result if isinstance(result, str)
                    else getattr(result, "text", str(result)))
        self._generate = generate_text

    def __call__(self, image, max_tokens=TOKEN_MAX):
        if self._generate is None:
            self._load()
        return self._generate(image, max_tokens)


def _event_sink(progress):
    """Create the console/progress formatter for structured runner events."""
    def handle(event):
        kind = event["type"]
        if kind == "analysis_started":
            selection = event["selection"]
            suffix = f" (pages {sorted(selection)})" if selection else ""
            print(f"Analyzing {event['file']} (scan pages @ {event['dpi']} dpi){suffix} ...")
        elif kind == "analysis_complete":
            print(f"   {event['pages']} pages — {event['pages_textlayer']} from textlayer, "
                  f"{event['pages_ocr']} through model\n")
        elif kind == "start" and progress:
            _progress({"typ": "start", "datei": event["file"],
                       "seiten": event["pages"], "dpi": event["dpi"]})
        elif kind == "cache":
            print(f"   cache: {event['reused']} page(s) reused from "
                  f"{event['directory']}\n")
        elif kind == "dictionary":
            wordbook = event["wordbook"]
            print("Dictionary: " + (wordbook.source if wordbook else
                                     "none found — check skipped."))
            if wordbook and not event["correct"]:
                print("   only report — replace with --dictionary-correct\n")
            elif wordbook:
                print("   unambiguous cases will be replaced\n")
        elif kind == "page":
            page, image_path = event["page"], event["image_path"]
            extra = (f" | → {image_path.name} ({image_path.stat().st_size // 1024} kB)"
                     if image_path else "")
            source = "DIAGRAM as image" if page.is_diagram else event["source_detail"]
            print(f"→ p.{page.number}: {page.seconds:5.1f} s | "
                  f"{page.text_characters:5d} chars textlayer → "
                  f"{sum(len(text) for text in page.paragraphs):5d} chars | "
                  f"{len(page.paragraphs):3d} paragraphs | {source}{extra}")
            if page.discarded:
                print(f"     discarded ({len(page.discarded)}): "
                      + " ¦ ".join(text[:34] for text in page.discarded[:6])
                      + (" …" if len(page.discarded) > 6 else ""))
            findings = event["findings"]
            if findings:
                print(f"     ⌕ {len(findings)} words: " + " ¦ ".join(
                    (f"{item.word} → {item.suggestion}" if item.suggestion
                     else f"{item.word} ?") + (" ✓" if item.corrected else "")
                    + ("" if item.count == 1 else f" ({item.count}x)")
                    for item in findings[:6]) + (" …" if len(findings) > 6 else ""))
            for warning in page.trace:
                print(f"     ⚠ {warning}")
            if progress:
                origin = ("diagramm" if page.is_diagram else
                          "textlayer" if page.source == "textlayer" else "ocr")
                payload = {"typ": "seite", "nr": page.number,
                           "von": event["total_pages"],
                           "sekunden": round(page.seconds, 1),
                           "herkunft": origin, "entgleist": bool(page.trace)}
                if page.trace:
                    payload["grund"] = page.trace[0]
                _progress(payload)
        elif kind == "artifact":
            print(f"→ {event['path']} ({event['count']} {event['unit']})")
        elif kind == "complete":
            result = event["result"]
            if progress:
                _progress({"typ": "fertig", "ziel": str(result.target),
                           "sekunden": round(result.seconds, 1),
                           "entgleist": result.pages_derailed})
            print(f"\n{result.seconds:.1f} s total "
                  f"({result.seconds / len(result.pages):.1f} s/page)\n→ {result.target}")
        elif kind == "cancelled":
            if event.get("target"):
                print(f"\nCancellation: partial file written "
                      f"({event['written']} of {event['total_pages']} pages) "
                      f"→ {event['target']}")
            else:
                print("Cancellation before first page — no partial file written.")
    return handle


def _run_preflight(args, parser):
    if args.source:
        parser.error("--check does not require an input file "
                     "(only runs preflight check)")
    temporary = None
    target = args.out
    if args.out == OUT:
        temporary = Path(tempfile.mkdtemp(prefix="pdf2md-preflight-"))
        target = temporary
    checks, warnings = preflight(target)
    all_ok = all(ok for _, ok, _ in checks)
    if args.progress:
        print(json.dumps({"typ": "check", "ok": all_ok,
                          "checks": [{"name": name, "ok": ok, "detail": detail}
                                     for name, ok, detail in checks],
                          "warnungen": warnings}, ensure_ascii=False, indent=1))
    else:
        for name, ok, detail in checks:
            print(f"{'[ ok ]' if ok else '[fehlt]'} {name}: {detail}")
        for warning in warnings:
            print(f"[warn] {warning}")
        print("—")
        print("alle ok" if all_ok else "fehlgeschlagen")
    if temporary is not None:
        try:
            os.rmdir(temporary)
        except OSError:
            pass
    return 0 if all_ok else EXIT_CHECK


def _parser():
    parser = argparse.ArgumentParser()
    accepted = ", ".join(sorted(INPUT_SUFFIXES))
    parser.add_argument("source", nargs="?", default=None, metavar="INPUT",
                        help=f"Input file — PDF or image ({accepted})")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--tile-from", "--kachel-ab", dest="tile_from", type=int,
                        default=TILE_THRESHOLD)
    parser.add_argument("--no-bold", "--kein-fett", dest="no_bold", action="store_true")
    parser.add_argument("--ocr-only", "--nur-ocr", dest="ocr_only", action="store_true")
    parser.add_argument("--retries", "--neuversuche", dest="retries", type=int, default=1)
    parser.add_argument("--lines-dump", "--zeilen-dump", dest="lines_dump", type=Path)
    parser.add_argument("--no-dictionary", "--kein-woerterbuch",
                        dest="no_dictionary", action="store_true")
    parser.add_argument("--dictionary", "--woerterbuch", dest="dictionary",
                        action="append", default=[], type=Path, metavar="FILE")
    parser.add_argument("--dictionary-correct", "--woerterbuch-korrigieren",
                        dest="dictionary_correct", action="store_true")
    parser.add_argument("--dictionary-report", "--woerterbuch-bericht",
                        dest="dictionary_report", type=Path, metavar="FILE")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--image-dir", "--bild-dir", dest="image_dir", type=Path)
    parser.add_argument("--image-max-edge", "--bild-max-kante",
                        dest="image_max_edge", type=int, default=1800)
    parser.add_argument("--diagram-pages", "--diagramm-seiten",
                        dest="diagram_pages", default="")
    parser.add_argument("--diagram-image-only", "--diagramm-nur-bild",
                        dest="diagram_image_only", action="store_true")
    parser.add_argument("--pages", "--seiten", dest="pages", default="")
    parser.add_argument("--refresh-cache", "--neu", dest="refresh_cache",
                        nargs="?", const="all", default=None, metavar="PAGES",
                        help="Recalculate all pages, or only a page list/range")
    parser.add_argument("--progress", "--fortschritt", dest="progress", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def main():
    parser = _parser()
    args = parser.parse_args()
    if args.check:
        sys.exit(_run_preflight(args, parser))
    if not args.source:
        parser.error("Requires an input file — PDF or image "
                     "(or --check for preflight check)")
    source = Path(args.source)
    if not source.exists():
        sys.exit(f"not found: {source}")
    try:
        # The suffix is checked before the file is opened; the frame count
        # only `page_count_of()` can see (Issue #100). Both exit the same way.
        ensure_supported_input(source)
        page_count = page_count_of(source)
    except UnsupportedInput as error:
        sys.exit(str(error))
    selection = page_option("--pages/--seiten", args.pages, page_count)
    forced = page_option("--diagram-pages/--diagramm-seiten",
                         args.diagram_pages, page_count) or set()
    if args.refresh_cache == "all":
        refresh = selection or set(range(1, page_count + 1))
    elif args.refresh_cache is None:
        refresh = set()
    else:
        refresh = page_option("--refresh-cache/--neu", args.refresh_cache,
                              page_count) or set()
    cancellation.reset()
    cancellation.install()
    request = ConversionRequest(
        pdf=source, output_dir=args.out, dpi=args.dpi, tile_from=args.tile_from,
        no_bold=args.no_bold, ocr_only=args.ocr_only, retries=args.retries,
        lines_dump=args.lines_dump, no_dictionary=args.no_dictionary,
        dictionaries=tuple(args.dictionary),
        dictionary_correct=args.dictionary_correct,
        dictionary_report=args.dictionary_report, image_dir=args.image_dir,
        image_max_edge=args.image_max_edge,
        forced_diagram_pages=frozenset(forced),
        selected_pages=frozenset(selection) if selection is not None else None,
        diagram_image_only=args.diagram_image_only, model_name=MODEL,
        ocr_prompt=PROMPT, refresh_pages=frozenset(refresh),
        cancel_requested=cancellation.requested)
    try:
        result = convert_document(
            request, LazyMlxOcrAdapter(MODEL), _event_sink(args.progress))
    except cancellation.Interrupted:
        # A repeated signal outside the page loop: during analysis, the model
        # load or the result write. That write is atomic, so the vault keeps
        # its previous preview.
        print("Cancellation — no file written.")
        sys.exit(EXIT_CANCELLED_EMPTY)
    except PreviewFormatError as error:
        sys.exit(str(error))
    if result.cancelled:
        sys.exit(EXIT_CANCELLED_PARTIAL if result.pages else EXIT_CANCELLED_EMPTY)
    if not result.pages:
        sys.exit(f"no pages to convert: {source.name}")


if __name__ == "__main__":
    main()
