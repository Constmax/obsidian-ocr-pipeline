#!/usr/bin/env python3
"""Dictionary matching: what is checked, what remains protected.

No model, no system dictionary required — rules operate on a word list specified in this test.

  python3 -m pytest pdf2md/test/test_dictionary.py
"""
import pytest

from dictionary import Dictionary, candidates, check, word_positions

WORDS = """
akte anspruch belehrung besitz bestimmt buch eigentum erklärung fall geld
haus hand hans hehler herausgabe klage kraft liegen mittlung nachforschung
obliegen partner recht rechts schaden schuld sein verhältnis verletzen
verschaffung verwaltung vollständig widrig wille wirkung
""".split()


@pytest.fixture
def dict_obj():
    return Dictionary(WORDS)


# --- 1. What the dictionary must know without containing it ---------------

def test_inflection_considered_known(dict_obj):
    assert dict_obj.knows("Anspruches")
    assert dict_obj.knows("Klagen")


def test_compound_from_known_parts(dict_obj):
    assert dict_obj.knows("Besitzmittlungsverhältnis")
    assert dict_obj.knows("Rechtsbehelfsbelehrung".replace("behelfs", "schuld"))


def test_joining_s_allowed(dict_obj):
    assert dict_obj.knows("Rechtsanspruch")


def test_unknown_remains_unknown(dict_obj):
    assert not dict_obj.knows("Verhaltungsakte")
    assert not dict_obj.knows("Besitzverschaiung")


def test_short_parts_do_not_form_compound(dict_obj):
    assert dict_obj.unknown(["Verhaltungsakte"]) == {"Verhaltungsakte"}


def test_verb_form_considered_known(dict_obj):
    assert dict_obj.knows("liegt")
    assert dict_obj.knows("verletzte")


def test_ung_derivation_considered_known(dict_obj):
    assert dict_obj.knows("Verletzung")
    assert dict_obj.knows("Verletzungen")


def test_negation_considered_known(dict_obj):
    assert dict_obj.knows("unvollständig")


def test_boundary_well_formed_pseudo_word():
    dict_obj = Dictionary(WORDS + ["verhalten"])
    assert dict_obj.knows("Verhaltungsakte")


# --- 2. Candidates ----------------------------------------------------------

def test_candidate_preserves_capitalization():
    assert "Behler" in candidates("Hehler")
    assert "behler" not in candidates("Hehler")


def test_candidate_finds_measured_cases():
    assert "Verwaltungsakte" in candidates("Verhaltungsakte")
    assert "Besitzverschaffung" in candidates("Besitzverschaiung")
    assert "Schuld" in candidates("Schu1d")


# --- 3. Protection rules: what is not checked at all ----------------------

def test_citation_is_not_checked():
    assert [w for w, _, _ in word_positions("Anspruch aus § 823 I BGB gegeben")] \
        == ["Anspruch", "gegeben"]


def test_wikilink_and_footnote_remain_excluded():
    text = "Vgl. [[raw/ZR/skript.pdf]] und die Fussnote[^12] dazu"
    assert "skript" not in [w for w, _, _ in word_positions(text)]
    assert "Fussnote" in [w for w, _, _ in word_positions(text)]


def test_table_row_is_skipped():
    assert word_positions("| § 275 BGB | Unmöglichkeit | ja |") == []


def test_abbreviation_and_short_word_remain_excluded():
    found = [w for w, _, _ in word_positions("Der VwVfG-Fall und hM zum Hehler")]
    assert found == ["Fall", "Hehler"]


# --- 4. Report and Correct -------------------------------------------------

def test_reports_without_changing(dict_obj):
    paragraphs = ["Der Verhaltungsakte ist rechtswidrig."]
    out, findings = check(paragraphs, dict_obj)
    assert out == paragraphs
    assert [b.word for b in findings] == ["Verhaltungsakte"]
    assert findings[0].suggestion == "Verwaltungsakte"
    assert findings[0].corrected is False


def test_corrects_only_on_request(dict_obj):
    out, findings = check(["Der Verhaltungsakte ist rechtswidrig."], dict_obj,
                          correct=True)
    assert out == ["Der Verwaltungsakte ist rechtswidrig."]
    assert findings[0].corrected is True


def test_ambiguous_remains_unchanged():
    dict_obj = Dictionary(WORDS)
    out, findings = check(["Der Hann steht daneben."], dict_obj, correct=True)
    assert out == ["Der Hann steht daneben."]
    assert findings[0].suggestion is None


def test_direct_hit_beats_decomposable():
    dict_obj = Dictionary(["besitz", "verschaffung", "verschalung",
                           "besitzverschaffung"])
    out, findings = check(["Die Besitzverschaiung ist erfolgt."], dict_obj,
                          correct=True)
    assert out == ["Die Besitzverschaffung ist erfolgt."]
    assert findings[0].suggestion == "Besitzverschaffung"


def test_citation_not_touched_even_when_correcting(dict_obj):
    text = ["Anspruch aus § 8l2 I 1 Alt. 1 BGB gegen den Hehler."]
    out, _ = check(text, dict_obj, correct=True)
    assert out == text


def test_all_occurrences_replaced_identically(dict_obj):
    out, _ = check(["Der Verhaltungsakte.", "Auch der Verhaltungsakte."],
                   dict_obj, correct=True)
    assert out == ["Der Verwaltungsakte.", "Auch der Verwaltungsakte."]


def test_without_dictionary_nothing_happens():
    paragraphs = ["Der Verhaltungsakte ist rechtswidrig."]
    out, findings = check(paragraphs, Dictionary([]), correct=True)
    assert (out, findings) == (paragraphs, [])


def test_count_is_counted(dict_obj):
    _, findings = check(["Der Verhaltungsakte.", "Der Verhaltungsakte."], dict_obj)
    assert findings[0].count == 2
