"""Unit tests for the page-range grammar shared by --pages and --diagram-pages.

Run with: python3 -m pytest pdf2md/test/test_page_range.py -q
"""
import pytest

from page_range import PageRangeError, parse_page_range


@pytest.mark.parametrize("text", [None, "", "   "])
def test_blank_means_no_selection(text):
    assert parse_page_range(text, 4) is None


@pytest.mark.parametrize("text, expected", [
    ("2", {2}),
    ("1,3", {1, 3}),
    ("2-4", {2, 3, 4}),
    ("1,3-5,8", {1, 3, 4, 5, 8}),
    (" 1 , 3 - 5 ,8 ", {1, 3, 4, 5, 8}),
    ("1-3,2", {1, 2, 3}),
    ("4-4", {4}),
])
def test_valid_selections(text, expected):
    assert parse_page_range(text, 8) == expected


@pytest.mark.parametrize("text, message", [
    (",", "empty entry"),
    ("1,,3", "empty entry"),
    ("1,", "empty entry"),
    ("a", "invalid page specification"),
    ("1-", "invalid page specification"),
    ("-3", "invalid page specification"),
    ("1-2-3", "invalid page specification"),
    ("+1", "invalid page specification"),
    ("1.5", "invalid page specification"),
    ("٣", "invalid page specification"),
    ("0", "start at 1"),
    ("0-2", "start at 1"),
    ("3-1", "descending range"),
])
def test_malformed_selections_are_rejected(text, message):
    with pytest.raises(PageRangeError, match=message):
        parse_page_range(text, 8)


@pytest.mark.parametrize("text", ["5", "3-5", "1,9"])
def test_pages_beyond_the_pdf_are_rejected(text):
    with pytest.raises(PageRangeError, match=r"does not exist \(PDF has 4 pages\)"):
        parse_page_range(text, 4)


def test_last_page_is_in_range():
    assert parse_page_range("1-4", 4) == {1, 2, 3, 4}


def test_huge_range_fails_before_expanding():
    with pytest.raises(PageRangeError, match="does not exist"):
        parse_page_range("1-99999999999", 4)
