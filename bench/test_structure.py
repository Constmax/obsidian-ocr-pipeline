"""Structure metric and truth-set validation for Issue #20.

Pure: no model, no vault, no fitz. The truth file is committed, so schema
and coverage checks run in CI; scoring against live candidates needs the
vault and stays in `structure_bench.py score`.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import structure as S  # noqa: E402

TRUTH = Path(__file__).resolve().parent / "structure_truth.json"


def test_identical_structure_passes():
    paragraphs = ["#### 1. Claim under section 816",
                  "The owner may demand return.[^1]",
                  "",
                  "[^1]: BGH, NJW 2014, 1524."]
    reference = S.reference_paragraphs(paragraphs)
    result = S.compare_structure(reference, list(paragraphs))
    assert result["passed"]
    assert result["paragraphs"]
    assert result["heading_levels"][0]
    assert result["footnote_numbers"][0]


def test_merged_paragraphs_fail():
    reference = S.reference_paragraphs(["First paragraph.", "Second paragraph."])
    result = S.compare_structure(reference, ["First paragraph. Second paragraph."])
    assert not result["passed"]
    assert not result["paragraphs"]
    assert result["paragraph_count"] == (False, 2, 1)


def test_reordered_paragraphs_fail():
    reference = S.reference_paragraphs(["First paragraph.", "Second paragraph."])
    result = S.compare_structure(reference, ["Second paragraph.", "First paragraph."])
    assert not result["passed"]


def test_wrong_heading_level_fails():
    reference = S.reference_paragraphs(["#### 1. Claim"])
    result = S.compare_structure(reference, ["### 1. Claim"])
    assert not result["passed"]
    assert result["heading_levels"] == (False, [4], [3])


def test_misassigned_footnote_fails():
    reference = S.reference_paragraphs(["Text.[^1]", "", "[^1]: Source A."])
    result = S.compare_structure(
        reference, ["Text.[^2]", "", "[^2]: Source A."])
    assert not result["passed"]
    assert result["footnote_numbers"] == (False, [1], [2])


def test_unlinked_reference_fails():
    # Fingerprints strip [^n] markers by design, so ref linking needs its
    # own check: same words, lost reference.
    reference = S.reference_paragraphs(["Text.[^1]", "", "[^1]: Source A."])
    result = S.compare_structure(reference, ["Text.", "", "[^1]: Source A."])
    assert not result["passed"]
    assert result["paragraphs"]
    assert result["footnote_refs"] == (False, [1], [])


def test_content_changes_fail_exactly():
    reference = S.reference_paragraphs(["#### 1. Claim", "Some prose here."])
    result = S.compare_structure(
        reference, ["#### 1. Clain", "Some prose here."])
    assert not result["passed"]  # fingerprints cover content exactly
    assert result["kinds"]
    assert result["heading_levels"][0]


def test_normalize_strips_markup():
    assert S.normalize("**Beispiel:** Mutter M putzt.") == \
        S.normalize("Beispiel: Mutter M putzt.")
    assert S.normalize("#### 1. Claim") == "1. claim"


def test_fingerprint_is_stable_and_short():
    assert S.fingerprint("Hello World") == S.fingerprint("hello  world")
    assert len(S.fingerprint("Hello World")) == 12


# ── Truth file validation ─────────────────────────────────────────────


def load_truth():
    return json.loads(TRUTH.read_text(encoding="utf-8"))


def test_truth_has_twenty_blessed_pages():
    truth = load_truth()
    assert len(truth["pages"]) == 20
    for page in truth["pages"]:
        assert page["id"] and page["source"] and page["page"]
        assert page["layout"]
        assert "blocks" in page, f"{page['id']} not blessed"
        assert len(page["blocks"]) >= 2, f"{page['id']} too small"


def test_truth_covers_all_layouts():
    truth = load_truth()
    layouts = " ".join(p["layout"] for p in truth["pages"]).lower()
    for keyword in ("column", "footnote", "outline", "heading", "margin",
                    "table", "diagram", "box", "grid", "hyphen"):
        assert keyword in layouts, f"no page covers {keyword}"


def test_truth_blocks_have_hashes_and_anchors():
    truth = load_truth()
    for page in truth["pages"]:
        kinds = set()
        for block in page["blocks"]:
            assert re_match_hash(block["hash"]), f"{page['id']}: bad hash"
            assert block["anchor"] and len(block["anchor"]) <= 80, \
                f"{page['id']}: anchor missing or too long"
            kinds.add(kind_of(block))
        assert "text" in kinds or "heading" in kinds, \
            f"{page['id']}: no body content"


def kind_of(block):
    for kind in ("footnote", "heading", "table"):
        if kind in block:
            return kind
    return "text"


def re_match_hash(value):
    import re
    return bool(re.fullmatch(r"[0-9a-f]{12}", value))


def test_truth_scores_itself():
    """Self-consistency: rebuilding references from identical paragraphs passes."""
    paragraphs = ["#### 1. Claim under section 816",
                  "The owner may demand return.[^1]",
                  "",
                  "[^1]: BGH, NJW 2014, 1524.",
                  "| a | b |",
                  "| --- | --- |",
                  "| c | d |"]
    reference = S.reference_paragraphs(paragraphs)
    assert S.compare_structure(reference, list(paragraphs))["passed"]
