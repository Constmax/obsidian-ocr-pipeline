"""Fast mode without Apple Vision or RapidOCR: selection, geometry, re-reading."""
import math
import sys
from types import SimpleNamespace

import pytest

from ocrmypdf_paddle import apple, runtime
from ocrmypdf_paddle.hocr import TextLine, Word


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def flat(points):
    return [c for point in points for c in point]


@pytest.mark.parametrize("text, expected", [
    ("Klagefrist gem. § 74 I 1 VwGO", True),
    ("ndung des $ 855 BGB zu er", True),       # Vision's "$" for "§"
    ("(hier: § 568 | BGB) -", True),
    ("568 | BGB", True),                       # wrapped after "§"
    ("Widerspruch (568 | BGB) und", True),
    ("Klage nach Art. 93 GG", True),
    ("Widerspruch (568 Il BGB) und", True),    # "Il" for "II" without a sign
    ("Die Klagefrist beträgt einen Monat", False),
    ("Verwaltungsprozessrecht, Seite 14", False),
    ("Artikel und Artenschutz", False),
    ("im Jahr 2019 Ileana", False),
])
def test_citation_lines_are_selected_for_rereading(text, expected):
    assert apple.needs_rereading(text) is expected


def test_vision_points_become_top_left_pixels():
    points = [(0.1, 0.9), (0.5, 0.9), (0.5, 0.8), (0.1, 0.8)]
    assert flat(apple.pixel_polygon(points, 1000, 2000)) == pytest.approx(
        flat(((100, 200), (500, 200), (500, 400), (100, 400))))


def test_crop_quad_adds_a_margin_along_the_line():
    quad, size = apple.crop_quad(rect(100, 200, 900, 240), 1000, 1000)
    assert flat(quad) == pytest.approx(flat(rect(94, 194, 906, 246)))
    assert size == (812, 52)
    assert apple.crop_quad((), 1000, 1000) is None
    assert apple.crop_quad(rect(1200, 10, 1300, 50), 1000, 1000) is None
    assert apple.crop_quad(rect(10, 10, 900, 10.5), 1000, 1000) is None


def rotated(cx, cy, length, height, degrees):
    a = math.radians(degrees)
    ux, uy, vx, vy = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    return tuple((cx + sx * length / 2 * ux + sy * height / 2 * vx,
                  cy + sx * length / 2 * uy + sy * height / 2 * vy)
                 for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)))


def test_skewed_lines_are_cropped_upright_without_their_neighbours():
    from PIL import Image, ImageDraw

    # Three dark 40 px lines 60 px apart, skewed by 2°: over 2000 px the
    # middle line rises 70 px, so its axis-aligned box holds both neighbours.
    image = Image.new("L", (2400, 600), 255)
    draw = ImageDraw.Draw(image)
    lines = [rotated(1200, cy, 2000, 40, -2) for cy in (240, 300, 360)]
    for polygon in lines:
        draw.polygon(polygon, fill=0)

    _, size = apple.crop_quad(lines[1], *image.size)
    assert size == (2012, 52)
    crop = apple.crop_line(image, lines[1])
    assert crop.mode == "RGB" and crop.size == size
    gray = crop.convert("L")
    # Line in the middle, background in the margins: nothing of the neighbours.
    assert gray.crop((20, 12, 1992, 40)).getextrema()[1] < 128
    for band in ((20, 0, 1992, 4), (20, 48, 1992, 52)):
        assert gray.crop(band).getextrema()[0] > 128


def test_crop_line_fills_beyond_the_image_with_white():
    from PIL import Image

    image = Image.new("1", (1000, 100), 0)
    crop = apple.crop_line(image, rect(0, 0, 1000, 40))
    assert crop.size == (1012, 52)
    assert crop.getpixel((2, 2)) == (255, 255, 255)
    assert crop.getpixel((500, 20)) == (0, 0, 0)


def test_utf16_offsets_count_surrogate_pairs():
    text = "a😀 § 1"
    assert apple._utf16_offset(text, 3) == 4
    assert apple._utf16_offset(text, len(text)) == 7


def test_line_reading_joins_the_pieces_and_keeps_the_weakest_score():
    reading = SimpleNamespace(txts=("§ 935", "BGB"), scores=(0.97, 0.91))
    assert runtime.line_reading(reading) == ("§ 935 BGB", 0.91)
    text, score = runtime.line_reading(SimpleNamespace(txts=None, scores=[1.0]))
    assert text == "" and math.isnan(score)


def test_recognize_line_runs_the_recognizer_alone_on_the_shared_engine():
    calls = []

    def engine(image, **kwargs):
        calls.append((image, kwargs))
        return SimpleNamespace(txts=("§ 935 BGB",), scores=(0.95,))

    factories = []
    reader = runtime.Recognizer(factory=lambda: factories.append(1) or engine)
    assert reader.recognize_line("crop") == ("§ 935 BGB", 0.95)
    assert reader.recognize_line("crop") == ("§ 935 BGB", 0.95)
    assert factories == [1]
    assert calls[0] == ("crop", dict(use_det=False, use_cls=False, use_rec=True))


@pytest.mark.parametrize("vision, text, score, accepted", [
    ("$ 935 BGB", "§ 935 BGB", 0.95, True),
    ("§ 568 IBGB", "§ 568 I BGB", 0.9, True),
    ("$ 935 BGB", "§ 935 BGB", 0.5, False),             # unsure reading
    ("$ 935 BGB", "§ 935 BGB", math.nan, False),
    ("$ 935 BGB", "", 0.99, False),
    ("$ 935 BGB", "$ 935 BGB", 0.99, False),            # nothing to change
    ("§ 568 | BGB", "Die Kündigung ist nach wirksam", 0.95, False),  # another line
])
def test_a_rereading_replaces_vision_only_when_sure_and_close(vision, text, score, accepted):
    assert apple.accepts_rereading(vision, text, score) is accepted


def test_missing_vision_outside_macos(monkeypatch):
    monkeypatch.setattr(apple.sys, "platform", "linux")
    assert apple.missing_vision() == ["fast mode needs Apple Vision, which exists only on macOS"]


@pytest.mark.parametrize("release, found", [("12.7.4", "12.7.4"), ("", "an unknown version")])
def test_missing_vision_before_macos_13(monkeypatch, release, found):
    monkeypatch.setattr(apple.sys, "platform", "darwin")
    monkeypatch.setattr(apple.platform, "mac_ver", lambda: (release, ("", "", ""), "arm64"))
    assert apple.missing_vision() == [
        f"fast mode needs macOS 13.0 or later for Apple Vision, not {found}"]


def test_missing_vision_without_pyobjc(monkeypatch):
    monkeypatch.setattr(apple.sys, "platform", "darwin")
    monkeypatch.setattr(apple.platform, "mac_ver", lambda: ("14.6", ("", "", ""), "arm64"))
    monkeypatch.setitem(sys.modules, "Vision", None)
    assert apple.missing_vision() == [
        "pyobjc-framework-Vision is not installed (pip install 'ocrmypdf-paddle[fast]')"]


def words(text, x0, y0, step=100):
    return tuple(Word(token, rect(x0 + i * step, y0, x0 + i * step + 80, y0 + 40), 0.5)
                 for i, token in enumerate(text.split()))


class FakeReader:
    """RapidOCR's engine: returns the given (text, score) readings in call order."""

    def __init__(self, readings):
        self.readings = readings
        self.calls = []

    def __call__(self, crop, **kwargs):
        self.calls.append((crop.size, kwargs))
        text, score = self.readings[len(self.calls) - 1]
        return SimpleNamespace(txts=(text,), scores=(score,))


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
    reader = FakeReader([("§ 935 BGB", 0.97), ("§ 568 I BGB", 0.93), ("", 0.0),
                         ("§ 1004 BGB analog", 0.6)])
    factories = []
    recognizer = apple.FastRecognizer(
        vision=vision_with([("Die Klagefrist beträgt", 100), ("$ 935 BGB", 200),
                            ("§ 568 IBGB", 300), ("nach § 44a VwGO", 400),
                            ("$ 1004 BGB analog", 500)]),
        reader=runtime.Recognizer(factory=lambda: factories.append(1) or reader))

    result = recognizer.recognize(page)

    assert (result.width, result.height) == (1000, 800)
    assert [line.text for line in result.lines] == [
        "Die Klagefrist beträgt", "§ 935 BGB", "§ 568 I BGB", "nach § 44a VwGO",
        "$ 1004 BGB analog"]
    assert len(factories) == 1
    assert [kwargs for _, kwargs in reader.calls] == [
        dict(use_det=False, use_cls=False, use_rec=True)] * 4
    assert reader.calls[0][0] == (902, 52)  # line box plus 15 % of its height
    first, dollar, roman, unread, unsure = result.lines
    assert first.words == words("Die Klagefrist beträgt", 10, 100)
    # Word for word, Vision's boxes carry the re-read words, scored by RapidOCR ...
    assert [w.text for w in dollar.words] == ["§", "935", "BGB"]
    assert [w.polygon for w in dollar.words] == [w.polygon for w in words("x x x", 10, 200)]
    assert dollar.confidence == 0.97 and {w.confidence for w in dollar.words} == {0.97}
    # ... otherwise the hOCR spreads them; an empty or unsure reading keeps
    # Vision's line.
    assert roman.words == () and roman.confidence == 0.93
    assert unread.words == words("nach § 44a VwGO", 10, 400)
    assert unsure.words == words("$ 1004 BGB analog", 10, 500) and unsure.confidence == 0.5


def test_rapidocr_is_not_loaded_without_citation_lines(page):
    def fail():
        raise AssertionError("RapidOCR created for a page without citations")

    recognizer = apple.FastRecognizer(vision=vision_with([("Einleitung", 100)]),
                                      reader=runtime.Recognizer(factory=fail))
    assert [line.text for line in recognizer.recognize(page).lines] == ["Einleitung"]
