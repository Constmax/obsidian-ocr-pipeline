#!/usr/bin/env python3
"""Structure fidelity for the assembly layer (Issue #20).

Word accuracy cannot judge the assembly layer: heading detection, paragraph
boundaries, outline depth, hyphen resolution, margin labels, footnote
assignment, and reading order. This module measures exactly those properties
by comparing candidate paragraphs against a hand-checked reference.

References hold fingerprints (SHA-1 over normalized text), never page text,
so the truth file stays free of copyrighted course material — the same rule
as bench/BENCHMARK-SET.md and bench/reading_order_truth.json. Short anchors
(single headings, footnote cues) remain readable for reviewers.

All functions are pure (no fitz, no model, no vault) so scoring runs in CI.
"""

import hashlib
import re
from collections import Counter

HEADING = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
FOOTNOTE_DEF = re.compile(r"^\[\^(\d+)\]:\s*(.*\S)\s*$")
FOOTNOTE_REF = re.compile(r"\[\^(\d+)\]")


def normalize(text):
    """Comparable form: markup removed, whitespace collapsed, lowered."""
    text = re.sub(r"\[\^\d+\]", " ", text)
    text = text.replace("**", " ").replace("|", " ")
    text = re.sub(r"^#{2,6}\s*", "", text)
    text = re.sub(r"^>\s?", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(text):
    """One-way fingerprint of a paragraph (SHA-1 over normalized text)."""
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]


def parse_paragraphs(paragraphs):
    """Split assembled paragraphs into structural features."""
    headings, body, defs, refs, tables = [], [], {}, [], 0
    for block in paragraphs:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) == 1:
            m = FOOTNOTE_DEF.match(lines[0].strip())
            if m:
                defs[int(m.group(1))] = m.group(2).strip()
                continue
        m = HEADING.match(lines[0].strip()) if len(lines) == 1 else None
        if m:
            headings.append({"level": len(m.group(1)),
                             "anchor": m.group(2).strip()})
            body.append(block)
        else:
            if block.lstrip().startswith("|"):
                tables += 1
            body.append(block)
        for line in lines:
            if FOOTNOTE_DEF.match(line.strip()):
                continue
            refs.extend(int(n) for n in FOOTNOTE_REF.findall(line))
    return {
        "headings": headings,
        "paragraphs": body,
        "footnote_defs": defs,
        "footnote_refs": refs,
        "tables": tables,
    }


def reference_paragraphs(paragraphs):
    """Build the committable reference for hand-checked paragraphs."""
    out = []
    for block in paragraphs:
        if not block.strip():
            continue
        parsed = parse_paragraphs([block])
        refs = sorted(parsed["footnote_refs"])
        if parsed["footnote_defs"]:
            number, text = next(iter(parsed["footnote_defs"].items()))
            out.append({"footnote": number, "hash": fingerprint(text),
                        "anchor": text[:60]})
        elif parsed["headings"]:
            heading = parsed["headings"][0]
            out.append({"heading": heading["level"],
                        "hash": fingerprint(block),
                        "anchor": heading["anchor"][:60],
                        "refs": refs})
        elif block.lstrip().startswith("|"):
            out.append({"table": True, "hash": fingerprint(block),
                        "anchor": block.splitlines()[0][:60]})
        else:
            out.append({"hash": fingerprint(block),
                        "anchor": block[:60].replace("\n", " "),
                        "refs": refs})
    return out


def _kind(block):
    """Structural kind of a reference/candidate block."""
    if "footnote" in block:
        return "footnote"
    if "heading" in block:
        return "heading"
    if "table" in block:
        return "table"
    return "text"


def compare_structure(reference_blocks, candidate_paragraphs):
    """Score candidate paragraphs against blessed reference blocks.

    Paragraph order and boundaries are exact: fingerprints cover content and
    sequence, so any merge, split, loss, or reorder fails the comparison.
    """
    parsed = parse_paragraphs(candidate_paragraphs)
    actual = reference_paragraphs(candidate_paragraphs)
    hashes_match = ([b["hash"] for b in reference_blocks]
                    == [b["hash"] for b in actual])
    ref_levels = [b["heading"] for b in reference_blocks if "heading" in b]
    act_levels = [b["heading"] for b in actual if "heading" in b]
    ref_numbers = sorted(b["footnote"] for b in reference_blocks
                         if "footnote" in b)
    footnote_numbers = sorted(parsed["footnote_defs"]) == ref_numbers
    ref_refs = sorted(r for b in reference_blocks for r in b.get("refs", []))
    act_refs = sorted(r for b in actual for r in b.get("refs", []))
    footnote_refs = ref_refs == act_refs
    result = {
        "paragraphs": hashes_match,
        "paragraph_count": (len(reference_blocks) == len(actual),
                            len(reference_blocks), len(actual)),
        "kinds": ([_kind(b) for b in reference_blocks]
                  == [_kind(b) for b in actual]),
        "heading_levels": (ref_levels == act_levels, ref_levels, act_levels),
        "footnote_numbers": (footnote_numbers, ref_numbers,
                             sorted(parsed["footnote_defs"])),
        "footnote_refs": (footnote_refs, ref_refs, act_refs),
    }
    result["passed"] = hashes_match and result["kinds"] \
        and result["heading_levels"][0] and result["footnote_numbers"][0] \
        and result["footnote_refs"][0]
    return result
