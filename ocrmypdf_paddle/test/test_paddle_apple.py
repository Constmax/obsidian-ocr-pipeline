"""Fast mode without Apple Vision or RapidOCR: selection, geometry, re-reading."""
from types import SimpleNamespace

import pytest

from ocrmypdf_paddle import apple
from ocrmypdf_paddle.hocr import TextLine, Word


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def flat(points):
    return [c for point in points for c in point]


@pytest.mark.parametrize("text, expected", [
    ("Klagefrist gem. § 74 I 1 VwGO", True),
    ("ndung des $ 855 BGB zu er", True),       # Vision's "$" for "§"
    ("(hier: § 568 | BGB) -", True),
    ("Klage nach Art. 93 GG", True),
    ("Widerspruch (568 Il BGB) und", True),    # "Il" for "II" without a sign
    ("Die Klagefrist beträgt einen Monat", False),
    ("Verwaltungsprozessrecht, Seite 14", False),
    ("Artikel und Artenschutz", False),
])
def test_citation_lines_are_selected_for_rereading(text, expected):
    assert apple.needs_rereading(text) is expected


def test_vision_points_become_top_left_pixels():
    points = [(0.1, 0.9), (0.5, 0.9), (0.5, 0.8), (0.1, 0.8)]
    assert flat(apple.pixel_polygon(points, 1000, 2000)) == pytest.approx(
        flat(((100, 200), (500, 200), (500, 400), (100, 400))))


def test_word_boxes_are_trimmed_along_their_edges():
    trimmed = apple.trim_word(rect(100, 0, 300, 40), line_height=40)
    assert flat(trimmed) == pytest.approx(flat(((110, 0), (290, 0), (290, 40), (110, 40))))

    skewed = apple.trim_word(((0, 20), (200, 0), (204, 40), (4, 60)), line_height=40)
    (x0, y0), (x1, y1), _, (x3, y3) = skewed
    assert [x0, y0] == pytest.approx([200 * 10 / 201, 20 - 20 * 10 / 201], rel=1e-3)
    assert x1 < 200 and x3 > 4


def test_short_words_keep_most_of_their_box():
    # "I" is narrower than two trims; at most 30 % of the width goes per side.
    assert flat(apple.trim_word(rect(0, 0, 20, 40), line_height=40)) == pytest.approx(
        flat(((6, 0), (14, 0), (14, 40), (6, 40))))


def test_crop_box_adds_a_margin_and_stays_in_the_image():
    assert apple.crop_box(rect(100, 200, 900, 240), 1000, 1000) == (94, 194, 906, 246)
    assert apple.crop_box(rect(0, 0, 1000, 40), 1000, 1000) == (0, 0, 1000, 46)
    assert apple.crop_box((), 1000, 1000) is None
    assert apple.crop_box(rect(1200, 10, 1300, 50), 1000, 1000) is None


def test_utf16_offsets_count_surrogate_pairs():
    text = "a😀 § 1"
    assert apple._utf16_offset(text, 3) == 4
    assert apple._utf16_offset(text, len(text)) == 7


def test_reread_text_joins_the_recognized_pieces():
    engine = lambda crop, **kwargs: SimpleNamespace(txts=("§ 935", "BGB"))
    assert apple.reread_text(engine, None) == "§ 935 BGB"
    assert apple.reread_text(lambda crop, **kwargs: SimpleNamespace(txts=None), None) == ""


def test_missing_vision_outside_macos(monkeypatch):
    monkeypatch.setattr(apple.sys, "platform", "linux")
    assert apple.missing_vision() == ["fast mode needs Apple Vision, which exists only on macOS"]


def words(text, x0, y0, step=100):
    return tuple(Word(token, rect(x0 + i * step, y0, x0 + i * step + 80, y0 + 40), 0.5)
                 for i, token in enumerate(text.split()))


class FakeReader:
    """RapidOCR's recognizer: returns the given readings in call order."""

    def __init__(self, readings):
        self.readings = readings
        self.calls = []

    def __call__(self, crop, **kwargs):
        self.calls.append((crop.size, kwargs))
        return SimpleNamespace(txts=(self.readings[len(self.calls) - 1],))


@pytest.fixture
def page(tmp_path):
    from PIL import Image

    path = tmp_path / "page.png"
    Image.new("L", (1000, 800), 255).save(path)
    return path


def vision_with(lines):
    return lambda path, width, height: [TextLine(text, rect(10, y, 900, y + 40), 0.5,
                                                 words(text, 10, y))
                                        for text, y in lines]


def test_only_citation_lines_are_reread(page):
    reader = FakeReader(["§ 935 BGB", "§ 568 I BGB", ""])
    factories = []
    recognizer = apple.FastRecognizer(
        vision=vision_with([("Die Klagefrist beträgt", 100), ("$ 935 BGB", 200),
                            ("§ 568 IBGB", 300), ("nach § 44a VwGO", 400)]),
        reader_factory=lambda: factories.append(1) or reader)

    result = recognizer.recognize(page)

    assert (result.width, result.height) == (1000, 800)
    assert [line.text for line in result.lines] == [
        "Die Klagefrist beträgt", "§ 935 BGB", "§ 568 I BGB", "nach § 44a VwGO"]
    assert len(factories) == 1
    assert [kwargs for _, kwargs in reader.calls] == [
        dict(use_det=False, use_cls=False, use_rec=True)] * 3
    assert reader.calls[0][0] == (902, 52)  # line box plus 15 % of its height
    first, dollar, roman, unread = result.lines
    assert first.words == words("Die Klagefrist beträgt", 10, 100)
    # Word for word, Vision's boxes carry the re-read words ...
    assert [w.text for w in dollar.words] == ["§", "935", "BGB"]
    assert [w.polygon for w in dollar.words] == [w.polygon for w in words("x x x", 10, 200)]
    # ... otherwise the hOCR spreads them; an empty reading keeps Vision's line.
    assert roman.words == ()
    assert unread.words == words("nach § 44a VwGO", 10, 400)


def test_rapidocr_is_not_loaded_without_citation_lines(page):
    def fail():
        raise AssertionError("RapidOCR created for a page without citations")

    recognizer = apple.FastRecognizer(vision=vision_with([("Einleitung", 100)]),
                                      reader_factory=fail)
    assert [line.text for line in recognizer.recognize(page).lines] == ["Einleitung"]
