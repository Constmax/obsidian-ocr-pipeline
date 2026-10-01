"""The page-block function: recognized lines in, page block out (Issue #138).

No model and no PDF — the lines come from page-cache entries, which is what a
replay reads.
"""

import fitz
import pytest

import dictionary
import page_cache
from assembly import AssemblyContext, build_document, split_preview
from conversion import (BlockContext, ConversionRequest, PageMeta,
                        assembly_context, convert_document, page_block)

WORDS = """
anspruch auf den der des entstanden für haftet ist käufers leistung nach
rechtsfolge schaden schadensersatz statt übereignung verkäufer
""".split()

OCR_LINES = [
    ["Der Anspruch des Käufers auf Ubereignung ist entstanden.",
     [100, 100, 800, 130]],
    ["Der Verkaufer haftet nach § 280 Abs. 1 BGB für den Schaden.",
     [100, 140, 800, 170]],
    ["1. Rechtsfolge ist der Schadensersatz statt der Leistung Xqzwmpfk.",
     [100, 400, 800, 430]],
]


def _cached_lines(tmp_path, source, lines):
    """Lines as a replay gets them: through a page-cache entry."""
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    directory = page_cache.cache_directory(tmp_path / "out", pdf)
    page_cache.write_page(directory, page_cache.build_context(pdf, {}), {
        "number": 3, "source": source, "characters": 0,
        "layout": "einspaltig", "mode": "ganz", "lines": lines, "trace": [],
    })
    return page_cache.read_latest_page(directory, 3)["lines"]


def _context(**kwargs):
    return BlockContext(assembly=AssemblyContext(), **kwargs)


def test_a_textlayer_page_gets_its_marker_and_no_dictionary_pass(tmp_path):
    lines = _cached_lines(tmp_path, "textlayer", [
        ["Skript Schuldrecht AT", [100, 20, 500, 40]],
        *OCR_LINES,
    ])
    context = BlockContext(
        assembly=AssemblyContext(frozenset({"Skript Schuldrecht AT"})),
        wordbook=dictionary.Dictionary(WORDS), dictionary_correct=True)

    block = page_block(lines, context, PageMeta(number=3, source="textlayer"))

    assert block.markdown == (
        "%% S. 3 | textlayer %%\n\n"
        "Der Anspruch des Käufers auf Ubereignung ist entstanden. "
        "Der Verkaufer haftet nach § 280 Abs. 1 BGB für den Schaden.\n\n"
        "1. Rechtsfolge ist der Schadensersatz statt der Leistung Xqzwmpfk.")
    assert block.markdown == "%% S. 3 | textlayer %%\n\n" + "\n\n".join(
        block.paragraphs)
    assert block.discarded == ["Skript Schuldrecht AT"]
    assert block.findings == []


def test_an_ocr_page_is_corrected_by_the_dictionary_pass(tmp_path):
    lines = _cached_lines(tmp_path, "ocr", OCR_LINES)
    meta = PageMeta(number=3, source="ocr", source_detail="einspaltig, ganz")

    reported = page_block(
        lines, _context(wordbook=dictionary.Dictionary(WORDS)), meta)
    corrected = page_block(
        lines,
        _context(wordbook=dictionary.Dictionary(WORDS), dictionary_correct=True),
        meta)

    assert reported.markdown.startswith("%% S. 3 | ocr | einspaltig, ganz %%\n\n")
    assert "Ubereignung" in reported.markdown
    assert "Verkaufer" in reported.markdown
    assert sorted((item.word, item.suggestion, item.corrected)
                  for item in reported.findings) == [
        ("Ubereignung", "Übereignung", False),
        ("Verkaufer", "Verkäufer", False),
        ("Xqzwmpfk", None, False),
    ]
    assert corrected.markdown == (
        "%% S. 3 | ocr | einspaltig, ganz %%\n\n"
        "Der Anspruch des Käufers auf Übereignung ist entstanden. "
        "Der Verkäufer haftet nach § 280 Abs. 1 BGB für den Schaden.\n\n"
        "1. Rechtsfolge ist der Schadensersatz statt der Leistung Xqzwmpfk.")
    assert sorted(item.word for item in corrected.findings
                  if item.corrected) == ["Ubereignung", "Verkaufer"]


def test_an_ocr_page_without_a_wordbook_is_left_as_read(tmp_path):
    lines = _cached_lines(tmp_path, "ocr", OCR_LINES)

    block = page_block(lines, _context(), PageMeta(
        number=3, source="ocr", source_detail="einspaltig, ganz"))

    assert "Ubereignung" in block.markdown
    assert block.findings == []


def test_an_ocr_page_without_a_marker_detail_gets_a_plain_ocr_marker():
    block = page_block(OCR_LINES[:1], _context(),
                       PageMeta(number=3, source="ocr"))

    assert block.markdown.startswith("%% S. 3 | ocr %%\n\n")


def test_an_empty_page_is_its_marker_as_the_preview_reads_it_back():
    context = BlockContext(
        assembly=AssemblyContext(frozenset({"Skript Schuldrecht AT"})))

    empty = page_block([["Skript Schuldrecht AT", [100, 20, 500, 40]]],
                       context, PageMeta(number=1, source="textlayer"))
    text = page_block(OCR_LINES, context, PageMeta(number=2, source="textlayer"))

    assert empty.markdown == "%% S. 1 | textlayer %%"
    preview = build_document("---\ntitel: x\n---\n", "Quelle: [[x.pdf]]\n",
                             [empty.markdown, text.markdown])
    assert [page.text for page in split_preview(preview).pages] == [
        empty.markdown, text.markdown]


def test_a_diagram_page_embeds_its_image_above_the_text_callout(tmp_path):
    lines = _cached_lines(tmp_path, "ocr", OCR_LINES[:1])
    meta = PageMeta(number=3, source="ocr", source_detail="einspaltig, ganz",
                    diagram_image="skript-s003.png")

    with_text = page_block(lines, _context(), meta)
    image_only = page_block(lines, _context(diagram_image_only=True), meta)
    no_text = page_block([], _context(), meta)

    assert with_text.markdown == (
        "%% S. 3 | diagramm %%\n\n"
        "![[skript-s003.png]]\n\n"
        "> [!note]- Text der Seite (Reihenfolge nicht verlässlich)\n"
        "> Der Anspruch des Käufers auf Ubereignung ist entstanden.")
    assert image_only.markdown == (
        "%% S. 3 | diagramm %%\n\n![[skript-s003.png]]")
    assert no_text.markdown == image_only.markdown


def _loc(x0, y0, x1, y1):
    return "".join(f"<|LOC_{value}|>"
                   for value in (x0, y0, x1, y0, x1, y1, x0, y1))


@pytest.mark.slow  # end-to-end run; `make test-fast` skips it
def test_replaying_cached_lines_reproduces_the_preview_block(
        tmp_path, monkeypatch, make_vector_pdf):
    """What a page case relies on: the cache entry of a page, put through
    `page_block`, is the block the conversion wrote."""
    monkeypatch.setattr(dictionary, "hunspell_checker", lambda *_args: None)
    monkeypatch.delenv("PDF2MD_DICTIONARY", raising=False)
    pdf = tmp_path / "skript.pdf"
    output = tmp_path / "output"
    words = tmp_path / "words.dic"
    words.write_text("\n".join(WORDS), encoding="utf-8")
    make_vector_pdf(pdf, pages=2)

    def fake_ocr(image, max_tokens=None):
        return "\n".join(_loc(*box) + text for text, box in OCR_LINES)

    request = ConversionRequest(
        pdf=pdf, output_dir=output, ocr_only=True, model_name="fake",
        temp_root=tmp_path / "scratch", dictionaries=(words,),
        dictionary_correct=True, forced_diagram_pages=frozenset({2}))
    result = convert_document(request, fake_ocr)
    written = {page.number: page.text
               for page in split_preview(result.markdown).pages}
    assert "Übereignung" in written[1]

    with fitz.open(pdf) as doc:
        assembly = assembly_context(doc)
    context = BlockContext(
        assembly=assembly, wordbook=dictionary.load([words]),
        dictionary_correct=True)
    cache_dir = page_cache.cache_directory(output, pdf)
    for number, image in ((1, None), (2, "skript-s002.png")):
        entry = page_cache.read_latest_page(cache_dir, number)
        block = page_block(entry["lines"], context,
                           PageMeta.from_cache_entry(entry, image))
        assert block.markdown == written[number]
