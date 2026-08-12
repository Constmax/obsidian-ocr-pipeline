#!/usr/bin/env python3
"""Woerterbuchabgleich: was geprueft wird, was geschuetzt bleibt.

Kein Modell, kein System-Woerterbuch noetig — die Regeln arbeiten auf einer
Wortliste, die hier im Test steht.

  python3 -m pytest pdf2md/test/test_woerterbuch.py
"""
import pytest

from woerterbuch import Woerterbuch, kandidaten, pruefen, stellen

# Kleines Woerterbuch in Grundformen. Flexion und Komposita muessen die Regeln
# des Moduls leisten, nicht diese Liste.
WOERTER = """
akte anspruch belehrung besitz bestimmt buch eigentum erklärung fall geld
haus hand hans hehler herausgabe klage kraft liegen mittlung nachforschung
obliegen partner recht rechts schaden schuld sein verhältnis verletzen
verschaffung verwaltung vollständig widrig wille wirkung
""".split()


@pytest.fixture
def wb():
    return Woerterbuch(WOERTER)


# --- 1. Was das Woerterbuch kennen muss, ohne es zu enthalten ---------------

def test_flexion_gilt_als_bekannt(wb):
    assert wb.kennt("Anspruches")
    assert wb.kennt("Klagen")


def test_kompositum_aus_bekannten_teilen(wb):
    # Steht in keinem Woerterbuch, ist aber der Normalfall dieses Bestands.
    assert wb.kennt("Besitzmittlungsverhältnis")
    assert wb.kennt("Rechtsbehelfsbelehrung".replace("behelfs", "schuld"))


def test_fugen_s_wird_zugelassen(wb):
    assert wb.kennt("Rechtsanspruch")


def test_unbekanntes_bleibt_unbekannt(wb):
    assert not wb.kennt("Verhaltungsakte")
    assert not wb.kennt("Besitzverschaiung")


def test_kurzteile_setzen_kein_kompositum(wb):
    # "Verhaltungsakte" darf nicht ueber Zwei-Buchstaben-Bruchstuecke
    # zusammengesetzt werden — sonst faellt die ganze Erkennung aus.
    assert wb.unbekannt(["Verhaltungsakte"]) == {"Verhaltungsakte"}


def test_verbform_gilt_als_bekannt(wb):
    # .dic-Dateien halten den Infinitiv. Ohne diese Regel waere jedes
    # konjugierte Verb ein Fehlalarm.
    assert wb.kennt("liegt")
    assert wb.kennt("verletzte")


def test_ung_ableitung_gilt_als_bekannt(wb):
    # Auch das ist eine Affixregel: "Verletzung" steht in `de_DE_frami` nicht
    # als eigener Eintrag, sondern wird aus "verletzen" abgeleitet.
    assert wb.kennt("Verletzung")
    assert wb.kennt("Verletzungen")


def test_verneinung_gilt_als_bekannt(wb):
    assert wb.kennt("unvollständig")


def test_grenze_wohlgeformtes_scheinwort():
    """Die dokumentierte Grenze des Wortlisten-Wegs.

    Steht das Verb im Woerterbuch, macht die Ableitungsregel aus
    "Verhaltungsakte" ein regulaeres Kompositum ("verhalten" + "Akte") — der
    Lesefehler kommt durch. Der Test haelt das fest, damit die Grenze nicht
    fuer einen Zufall gehalten wird; enger gestellt waere jedes
    "-ung"-Substantiv ein Fehlalarm.
    """
    wb = Woerterbuch(WOERTER + ["verhalten"])
    assert wb.kennt("Verhaltungsakte")


# --- 2. Kandidaten ----------------------------------------------------------

def test_kandidat_haelt_grossschreibung():
    assert "Behler" in kandidaten("Hehler")
    assert "behler" not in kandidaten("Hehler")


def test_kandidat_findet_die_gemessenen_faelle():
    assert "Verwaltungsakte" in kandidaten("Verhaltungsakte")   # h → w
    assert "Besitzverschaffung" in kandidaten("Besitzverschaiung")  # i → ff
    assert "Schuld" in kandidaten("Schu1d")                     # 1 → l


# --- 3. Schutzregeln: was gar nicht erst geprueft wird ----------------------

def test_zitat_wird_nicht_geprueft():
    assert [w for w, _, _ in stellen("Anspruch aus § 823 I BGB gegeben")] \
        == ["Anspruch", "gegeben"]


def test_wikilink_und_fussnote_bleiben_aussen_vor():
    text = "Vgl. [[raw/ZR/skript.pdf]] und die Fussnote[^12] dazu"
    assert "skript" not in [w for w, _, _ in stellen(text)]
    assert "Fussnote" in [w for w, _, _ in stellen(text)]


def test_tabellenzeile_wird_uebersprungen():
    assert stellen("| § 275 BGB | Unmöglichkeit | ja |") == []


def test_abkuerzung_und_kurzwort_bleiben_aussen_vor():
    gefunden = [w for w, _, _ in stellen("Der VwVfG-Fall und hM zum Hehler")]
    assert gefunden == ["Fall", "Hehler"]


# --- 4. Melden und Korrigieren ---------------------------------------------

def test_meldet_ohne_zu_aendern(wb):
    absaetze = ["Der Verhaltungsakte ist rechtswidrig."]
    raus, befunde = pruefen(absaetze, wb)
    assert raus == absaetze                       # Voreinstellung aendert nichts
    assert [b.wort for b in befunde] == ["Verhaltungsakte"]
    assert befunde[0].vorschlag == "Verwaltungsakte"
    assert befunde[0].korrigiert is False


def test_korrigiert_nur_auf_verlangen(wb):
    raus, befunde = pruefen(["Der Verhaltungsakte ist rechtswidrig."], wb,
                            korrigieren=True)
    assert raus == ["Der Verwaltungsakte ist rechtswidrig."]
    assert befunde[0].korrigiert is True


def test_mehrdeutiges_bleibt_stehen():
    # "Hans" und "Haus" sind beide bekannt: aus "Hann" wird nichts eindeutig.
    wb = Woerterbuch(WOERTER)
    raus, befunde = pruefen(["Der Hann steht daneben."], wb, korrigieren=True)
    assert raus == ["Der Hann steht daneben."]
    assert befunde[0].vorschlag is None


def test_direkter_treffer_schlaegt_zusammensetzbaren():
    """Der Fall, an dem die Korrektur sonst ausbliebe.

    Aus `Besitzverschaiung` wird ueber die Verwechslungstabelle beides:
    `Besitzverschaffung` (steht so im Woerterbuch) und `Besitzverschalung`
    (nur aus `Besitz` + `Verschalung` zusammensetzbar). Ohne den engeren
    Massstab waeren es zwei Kandidaten und das Wort bliebe stehen.
    """
    wb = Woerterbuch(["besitz", "verschaffung", "verschalung",
                      "besitzverschaffung"])
    raus, befunde = pruefen(["Die Besitzverschaiung ist erfolgt."], wb,
                            korrigieren=True)
    assert raus == ["Die Besitzverschaffung ist erfolgt."]
    assert befunde[0].vorschlag == "Besitzverschaffung"


def test_zitat_wird_auch_beim_korrigieren_nicht_angefasst(wb):
    # Der teuerste Fehler der Pipeline: ein "verbessertes" Normzitat.
    text = ["Anspruch aus § 8l2 I 1 Alt. 1 BGB gegen den Hehler."]
    raus, _ = pruefen(text, wb, korrigieren=True)
    assert raus == text


def test_alle_vorkommen_gleich_ersetzt(wb):
    raus, _ = pruefen(["Der Verhaltungsakte.", "Auch der Verhaltungsakte."],
                      wb, korrigieren=True)
    assert raus == ["Der Verwaltungsakte.", "Auch der Verwaltungsakte."]


def test_ohne_woerterbuch_passiert_nichts():
    absaetze = ["Der Verhaltungsakte ist rechtswidrig."]
    raus, befunde = pruefen(absaetze, Woerterbuch([]), korrigieren=True)
    assert (raus, befunde) == (absaetze, [])


def test_anzahl_wird_gezaehlt(wb):
    _, befunde = pruefen(["Der Verhaltungsakte.", "Der Verhaltungsakte."], wb)
    assert befunde[0].anzahl == 2
