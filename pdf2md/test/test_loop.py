#!/usr/bin/env python3
"""Loop detection and trimming, literal and digit-blind.

  python3 -m pytest pdf2md/test/test_schleife.py
"""
from ocr import LOOP_THRESHOLD, loop_length, trim_loop


def z(text):
    return [text, (0, 0, 1000, 10), False]


def test_healthy_sentence():
    assert loop_length("Der Anspruch des K gegen B ergibt sich aus dem "
                       "Kaufvertrag nach Paragraf 433 Absatz 2 BGB.") < 2


def test_literal_loop():
    assert loop_length(" ".join(["a b c d e"] * 30)) >= LOOP_THRESHOLD


def test_counter_loop_digit_blind_detected():
    counter = " ".join(f"({j})" for j in range(1982, 2260))
    assert loop_length(counter) >= LOOP_THRESHOLD


def test_footnote_block_does_not_trigger():
    assert loop_length(
        "BGH, NJW 2014, 1524 Rn. 8 m.w.N. "
        "Staudinger/Heinze, Paragraf 935 Rn. 14. "
        "MueKoBGB/Schaefer, Paragraf 855 Rn. 25. "
        "Vgl. MueKoBGB/Oechsler, Paragraf 935 Rn. 10 m.w.N.") < LOOP_THRESHOLD


def test_counter_trimmed_to_two_occurrences():
    counter = " ".join(f"({j})" for j in range(1982, 2260))
    out = trim_loop([z(counter)])
    words = out[0][0].split()
    assert len(words) == 2


def test_counter_retains_real_values():
    counter = " ".join(f"({j})" for j in range(1982, 2260))
    out = trim_loop([z(counter)])
    assert out[0][0].split() == ["(1982)", "(1983)"]


def test_literal_repetition_trimmed():
    assert trim_loop([z(" ".join(["V."] * 40))])[0][0] == "V. V."


def test_norm_chain_remains_untouched():
    chain = "Ansprueche aus Paragraf 823, Paragraf 826, Paragraf 831, " \
            "Paragraf 840."
    assert trim_loop([z(chain)])[0][0] == chain


def test_prose_remains_untouched():
    prose = ("Der Erfolg ist objektiv zurechenbar, wenn der Taeter eine "
             "rechtlich missbilligte Gefahr geschaffen hat, die sich im "
             "tatbestandsmaessigen Erfolg verwirklicht. Das ist hier der Fall, "
             "weil A den B mit dem Messer verletzte und die Wunde unmittelbar "
             "zum Tod fuehrte.")
    assert trim_loop([z(prose)])[0][0] == prose


def test_same_line_hundredfold():
    assert len(trim_loop([z("Dieselbe Fussnote steht hier.")] * 100)) == 2
