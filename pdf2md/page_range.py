#!/usr/bin/env python3
"""Page-range grammar shared by --pages and --diagram-pages (Issue #52).

A selection is a comma-separated list of 1-based pages and ascending ranges,
e.g. `1,3-5,8`. Blank input means "no selection". Every entry must be
well-formed: an empty entry as in `1,,3` or `,` is an error instead of being
skipped, so a non-blank selection can never collapse to zero pages.

Pure: raises PageRangeError instead of exiting; the CLI turns it into a
one-line message.
"""
import re

_ENTRY = re.compile(r"(\d+)(?:\s*-\s*(\d+))?", re.ASCII)


class PageRangeError(ValueError):
    """Invalid page selection; the message is shown to the user as is."""


def parse_page_range(text, page_count):
    """'1,3-5,8' -> {1, 3, 4, 5, 8}; blank -> None.

    Raises PageRangeError for empty or malformed entries, page 0, descending
    ranges and pages beyond `page_count`. Bounds are checked before a range is
    expanded, so a typo like `1-99999999` fails fast.
    """
    if text is None or not text.strip():
        return None
    pages = set()
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            raise PageRangeError(f"empty entry in {text.strip()!r}")
        m = _ENTRY.fullmatch(entry)
        if not m:
            raise PageRangeError(f"invalid page specification: {entry!r}")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo < 1:
            raise PageRangeError(f"page numbers start at 1: {entry!r}")
        if lo > hi:
            raise PageRangeError(f"descending range: {entry!r}")
        if hi > page_count:
            raise PageRangeError(f"page {hi} does not exist "
                                 f"(PDF has {page_count} pages)")
        pages.update(range(lo, hi + 1))
    return pages
