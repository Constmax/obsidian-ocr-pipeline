#!/usr/bin/env python3
"""Golden-Snapshot der reinen Zusammenbau-Funktionen.

  python3 pdf2md/test/erzeuge_snapshot.py            # snapshot.json neu schreiben
  python3 pdf2md/test/erzeuge_snapshot.py --pruefen  # gegen snapshot.json pruefen

Faengt die einzige Verhaltensaenderung, die der Split nach Issue #8 riskiert:
ein reines Verschieben von Funktionen zwischen Modulen darf die Ausgabe nicht
aendern. Jeder Fall ruft eine Funktion mit festen, im Skript stehenden Eingaben
und haelt Eingabe + Ausgabe in snapshot.json fest.

Kein Modell, kein fitz, kein Vault-Bestand noetig — die Funktionen arbeiten
auf Zeilenlisten. Die schweren Importe liegen funktionslokal in den Modulen.

Importiert wird ueber eine Namensliste statt fester Modulnamen: vor dem Split
liegt alles in pdf2md.py, danach in zusammenbau.py / layout.py / ocr.py. Das
Skript selbst bleibt in beiden Welten unveraendert lauffaehig.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def module():
    """Zielmodule laden; vor dem Split faellt alles auf pdf2md.py zurueck."""
    try:
        import layout
        import ocr
        import zusammenbau
    except ImportError:
        import pdf2md as zusammenbau
        layout = ocr = zusammenbau
    return zusammenbau, layout, ocr


def holen(name, module_map):
    for modul in module_map:
        if hasattr(modul, name):
            return getattr(modul, name)
    raise SystemExit(f"!! Funktion {name} nirgends gefunden")


# --- Feste Eingaben ---------------------------------------------------------

LOC_TEXT = ("<|LOC_100_210_880_225|><|LOC_100_210_880_225|>Er ist somit "
            "Besitzdiener iSd Paragraf 855 BGB.\n"
            "<|LOC_90_245_900_260|><|LOC_90_245_900_260|>Infolgedessen ist L "
            "kein Besitzer und nur M ist Besitzer des Mehls.\n"
            "<|LOC_120_290_300_305|><|LOC_120_290_300_305|>1.\n"
            "Und eine Zeile ganz ohne Koordinaten.")

SAEUBERN_TEXTE = [
    r"$\rightarrow$ der Pfeil und \text{Fett}",
    r"\underline{unterstrichen} und \( \alpha \)",
    r"$ 5 BGB und $\^{12} als Fussnotenzeichen",
    "§ § 929 und § 854 | BGB und | BGB am Satzanfang",
    "**gesetzliches** **Schuldverhaeltnis**",
    r"\leftarrow \Rightarrow \uparrow \to",
]

ENTPUA_TEXTE = [
    "\uf0f0 \uf0e0 \uf0d8 \uf0fc \uf0b7 \uf020 \uf001",
    "ganz normaler Text ohne Sonderzeichen",
]

EBENE_TEXTE = [
    "aa) Gemaess den Regeln der Dogmatik",
    "(1) Der Anspruch ist begruendet",
    "bb) Zweite Ebene",
    "a) Erster Unterpunkt",
    "1. Anspruch aus § 816 I S. 2 BGB",
    "II. Exkurs zum Streitstand",
    "A. Grundsaetzliches zum Unterlassen",
    "h. L.",
    "**a)** Gemaess herrschender Meinung",
    "Fliesstext ohne jeden Marker",
]

UEBERSCHRIFT_FAELLE = [
    ("**Beispiel:**", "Beispiel:"),
    ("**A. Grundsaetzliches zum Unterlassen**",
     "A. Grundsaetzliches zum Unterlassen"),
    ("**Merke**", "Merke"),
    ("**Bargeldes.**", "Bargeldes."),
    ("**IV. Exkurs: Uebersicht – V**", "IV. Exkurs: Uebersicht – V"),
]

BOILERPLATE_TEXTE = [
    ("Juristisches Repetitorium fuer Gesetz und Recht", None),
    ("hemmer", None),
    ("– 1 –", None),
    ("26-I", None),
    ("Mainz - Man", None),
    ("543", 950),
    ("Schuldrecht AT – Fall 12 | Begleitskript", None),
    ("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des Mehls.",
     None),
]

LAUFEND_TEXTE = [
    ("Fall 12  |  Begleitskript", None),
    ("Strafrecht BT V", None),
    ("Fall 12 | Begleitskript", None),
    ("Fall 12  |  Begleitskript", 300),
    ("Schuldrecht AT – Fall 12 | Begleitskript", None),
]

FETT_TEXTE = [
    "Text **fett",
    "**fett** Text **fett**",
    "ganz ohne Auszeichnung",
]

KURZE_ZEILEN = [
    ["Die erste Zeile eines Absatzes im Blocksatz.", (100, 100, 900, 115)],
    ["Die zweite Zeile laeuft voll bis an den Rand.", (100, 120, 900, 135)],
    ["Kurz, mit kleinem Nachsatz", (100, 140, 600, 155)],
    ["der hier direkt anschliesst.", (100, 155, 900, 170)],
    ["Die vierte Zeile ist wieder voll.", (100, 175, 900, 190)],
    ["Und die letzte Zeile endet frueh.", (100, 195, 700, 210)],
]

RANDLABEL_ZEILEN = [
    ["Der Garant ist zur Abwendung des Erfolges verpflichtet, so dass sein",
     (200, 100, 900, 114)],
    ["**Merke:**", (150, 118, 210, 132)],
    ["Unterlassen der Begehung durch aktives Tun entspricht, § 13 StGB.",
     (200, 118, 900, 132)],
    ["Daran schliesst sich die Frage der Garantenstellung an.",
     (200, 136, 900, 150)],
    ["Sie ist der Kern jeder Unterlassungspruefung.", (200, 154, 900, 168)],
]

FN_ANBINDEN_ZEILEN = [
    ["7", (8, 915, 20, 925)],
    ["Vgl. BGH, NJW 2014, 1524.", (50, 915, 400, 925)],
    ["Der Anspruch ist begruendet.", (90, 200, 800, 215)],
]

FN_OBSIDIAN_ABSAETZE = [
    "Er ist ein Besitzdiener.1",
    "1 Vgl. BGH, NJW 2014, 1524.",
    "2 So auch MüKo-BGB, § 855 Rn. 14.",
    "| a | b |",
]

GLIEDERUNG_ABSAETZE = [
    "1. Anspruch aus § 816 I S. 2 BGB",
    "cc) Diese Ansicht ist mit der h.M. abzulehnen.",
    "Fliesstext ohne Marker",
    "**1. Anspruch aus § 985 BGB auf Rueckgabe des Bargeldes.**",
    "3. Anspruch aus § 433 II BGB",
]

FRAGMENT_ZEILEN = [
    ["1.", (90, 210, 100, 225)],
    ["Die Abtretung als Verfuegung", (110, 210, 500, 225)],
    ["weiterer Text auf anderer Zeile", (90, 240, 400, 255)],
]

ZUSAMMENFUEGEN_MARKE = [
    ["**Beispiel:**", (166, 98, 237, 112)],
    ["Mutter M putzt gerade die Fenster ihrer Terrasse und stoesst dabei aus "
     "Unachtsamkeit einen", (166, 97, 899, 111)],
    ["Blumentopf herunter, welcher sodann den Dieb D trifft.",
     (166, 116, 899, 130)],
]

ZUSAMMENFUEGEN_FUSSNOTE = [
    ["Juristisches Repetitorium", (0, 50, 1000, 65)],
    ["Der Anspruch ist begruendet.1", (90, 100, 800, 115)],
    ["1 Vgl. BGH, NJW 2014, 1524.", (8, 915, 500, 930)],
]

CALLOUT_ABSAETZE = ["Absatz eins.", "Absatz zwei\nmit Umbruch",
                    "Letzter Absatz."]

RASTER_LINKS = [
    ["1. Was versteht man unter Geldwertvindikation?", (100, 100, 500, 115)],
    ["2. Welche Rechtsfolgen hat die Anfechtung?", (100, 130, 500, 145)],
    ["3. Was ist ein Anwartschaftsrecht?", (100, 160, 500, 175)],
    ["4. Wie unterscheiden sich Besitz und Eigentum?", (100, 190, 500, 205)],
    ["5. Was regelt § 985 BGB?", (100, 220, 500, 235)],
    ["6. Wer ist Besitzer?", (100, 250, 500, 265)],
]

RASTER_RECHTS = [
    ["Besitzdiener iSd Paragraf 855 BGB.", (520, 100, 900, 115)],
    ["Die Anfechtung macht den Vertrag von", (520, 130, 900, 145)],
    ["Anfang an nichtig.", (520, 160, 900, 175)],
    ["Ein Anwartschaftsrecht ist ein", (520, 190, 900, 205)],
    ["Recht auf den Erwerb einer Sache.", (520, 220, 900, 235)],
    ["Der Besitzer hat die Sachherrschaft.", (520, 250, 900, 265)],
]

SCHLEIFE_TEXTE = [
    " ".join(["a b c d e"] * 30),
    " ".join(f"({j})" for j in range(1982, 2260)),
    ("Der Anspruch des K gegen B ergibt sich aus dem Kaufvertrag nach "
     "Paragraf 433 Absatz 2 BGB."),
]

ENTGLEIST_FAELLE = [
    (" ".join(["a b c d e"] * 30), None, False),
    ("Der Anspruch ist begruendet und faellig, weil der Kaufvertrag "
     "wirksam zustande gekommen ist.", 500, False),
    ("Kurz abgebrochen.", 500, False),
    ("Satz." + " Und noch ein Satz mit ausreichend Substanz." * 100,
     500, False),
]

SCHLEIFE_KURZEN_ZEILEN = [
    ["Dieselbe Fussnote steht hier.", (0, 0, 1000, 10)],
    ["Ansprueche aus Paragraf 823, Paragraf 826, Paragraf 831, Paragraf 840.",
     (0, 0, 1000, 10)],
]

NAHT_OBEN = [
    ["Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen ist "
     "L kein Besitzer und nur M ist Besitzer des Mehls. Ob es",
     (0, 0, 1000, 10)],
]

NAHT_UNTEN = [
    ["Infolgedessen ist L kein Besitzer und nur M ist Besitzer des Mehls. "
     "Ob es dem M abhanden gekommen ist, richtet sich nach Paragraf 935 BGB.",
     (0, 0, 1000, 10)],
    ["Fussnote 13: BGH NJW 2014, 1524.", (0, 0, 1000, 10)],
]

NAHT_VERSCHOBEN = [
    ["Anknuepfungspunkt waere also, dass H trotz des aus der "
     "Besitzverschaiung folgenden Anscheins nicht nachgeforscht hat.",
     (0, 0, 1000, 10)],
    ["Anknuepfungspunkt waere also, dass H trotz des aus der "
     "Besitzverschaffung folgenden Anscheins nicht nachgeforscht hat. "
     "Fuer eine Nachforschungsobliegenheit spricht wenig.", (0, 0, 1000, 10)],
]

NAHT_DUPLETTE = [
    ["Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.",
     (0, 0, 1000, 10)],
    ["Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.",
     (0, 0, 1000, 10)],
    ["Ein Schaden ist entstanden.", (0, 0, 1000, 10)],
]


def faelle(module_map):
    """[(name, setup, arg_menge, funktion)] — setup laeuft vor den Aufrufen.

    `arg_menge` ist eine Liste von Argumentlisten: jede davon wird einmal
    aufgerufen, die Ergebnisse kommen als Liste in den Snapshot.
    """
    zusammenbau, layout, ocr = module_map

    def laufend_setzt(werte):
        if any(hasattr(m, "laufend_setzen") for m in module_map):
            holen("laufend_setzen", module_map)(werte)
        else:
            zusammenbau.LAUFEND.clear()
            zusammenbau.LAUFEND.update(werte)

    def verworfen_lesen(_):
        return getattr(holen("zusammenfuegen", module_map), "verworfen", [])

    z = lambda t: [t, (0, 0, 1000, 10)]
    M = lambda name: holen(name, module_map)
    eins = lambda args: [args]

    return [
        ("parse_zeilen", None, eins([LOC_TEXT]), M("parse_zeilen")),
        ("saeubern", None, [[t] for t in SAEUBERN_TEXTE], M("saeubern")),
        ("entpua", None, [[t] for t in ENTPUA_TEXTE], M("entpua")),
        ("ebene", None, [[t] for t in EBENE_TEXTE], M("ebene")),
        ("ist_ueberschrift", None,
         [[t, n] for t, n in UEBERSCHRIFT_FAELLE], M("ist_ueberschrift")),
        ("ist_boilerplate_ohne_laufend", None,
         [[t, y] for t, y in BOILERPLATE_TEXTE], M("ist_boilerplate")),
        ("ist_boilerplate_mit_laufend",
         lambda: laufend_setzt({"Fall 12 | Begleitskript", "Strafrecht BT V"}),
         [[t, y] for t, y in LAUFEND_TEXTE], M("ist_boilerplate")),
        ("fett_ausgleichen", None, [[t] for t in FETT_TEXTE],
         M("fett_ausgleichen")),
        ("kurze_zeilen", None, eins([KURZE_ZEILEN]), M("kurze_zeilen")),
        ("randlabel_vorziehen", None, eins([RANDLABEL_ZEILEN, 18]),
         M("randlabel_vorziehen")),
        ("fussnotennummern_anbinden", None, eins([FN_ANBINDEN_ZEILEN]),
         M("fussnotennummern_anbinden")),
        ("fussnoten_obsidian", None, eins([FN_OBSIDIAN_ABSAETZE]),
         M("fussnoten_obsidian")),
        ("gliederung_auszeichnen", None, eins([GLIEDERUNG_ABSAETZE]),
         M("gliederung_auszeichnen")),
        ("fragmente_verschmelzen", None, eins([FRAGMENT_ZEILEN, 595]),
         M("fragmente_verschmelzen")),
        ("zusammenfuegen_randmarke", None, eins([ZUSAMMENFUEGEN_MARKE]),
         M("zusammenfuegen")),
        ("zusammenfuegen_fussnote", None, eins([ZUSAMMENFUEGEN_FUSSNOTE]),
         M("zusammenfuegen")),
        ("als_callout", None, eins([CALLOUT_ABSAETZE, "Test-Titel"]),
         M("als_callout")),
        ("frage_antwort_raster", None,
         eins([RASTER_LINKS, RASTER_RECHTS]), M("frage_antwort_raster")),
        ("schleifenlaenge", None, [[t] for t in SCHLEIFE_TEXTE],
         M("schleifenlaenge")),
        ("entgleist", None, [list(f) for f in ENTGLEIST_FAELLE],
         M("entgleist")),
        ("schleife_kuerzen", None,
         eins([SCHLEIFE_KURZEN_ZEILEN
               + [z("Dieselbe Fussnote steht hier.")] * 100
               + [z(" ".join(["V."] * 40))]]),
         M("schleife_kuerzen")),
        ("ueberlappung_kuerzen_fortsetzung", None,
         eins([NAHT_OBEN, NAHT_UNTEN]), M("ueberlappung_kuerzen")),
        ("ueberlappung_kuerzen_duplette", None,
         eins([NAHT_DUPLETTE[:1], NAHT_DUPLETTE[1:]]),
         M("ueberlappung_kuerzen")),
        ("ueberlappung_kuerzen_lesefehler", None,
         eins([NAHT_VERSCHOBEN[:1], NAHT_VERSCHOBEN[1:]]),
         M("ueberlappung_kuerzen")),
        ("zusammenfuegen_verworfen", None, eins([None]),
         verworfen_lesen),
    ]


def jsonfaehig(x):
    if isinstance(x, tuple):
        return [jsonfaehig(e) for e in x]
    if isinstance(x, list):
        return [jsonfaehig(e) for e in x]
    if isinstance(x, set):
        return sorted(x)
    return x


def ergebnis(module_map):
    aus = []
    for name, setup, arg_menge, funktion in faelle(module_map):
        if setup:
            setup()
        try:
            werte = [funktion(*a) for a in arg_menge]
        except Exception as e:
            raise SystemExit(f"!! {name}: {e}")
        aus.append({"name": name, "eingaben": jsonfaehig(arg_menge),
                    "ausgabe": jsonfaehig(werte)})
    return aus


def vergleichen(neu, alt, pfad=""):
    fehler = []
    if isinstance(neu, list) and isinstance(alt, list) and len(neu) == len(alt) \
            and neu and isinstance(neu[0], dict):
        for n, a in zip(neu, alt):
            fehler += vergleichen(n, a, pfad + "/" + a.get("name", "?"))
        return fehler
    if neu != alt:
        fehler.append((pfad, neu, alt))
    return fehler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pruefen", action="store_true",
                    help="nur gegen snapshot.json pruefen, nicht schreiben")
    a = ap.parse_args()
    module_map = module()

    daten = ROOT / "test" / "daten" / "snapshot.json"
    if a.pruefen:
        if not daten.exists():
            raise SystemExit(f"!! {daten} fehlt — erst ohne --pruefen erzeugen")
        alt = json.loads(daten.read_text())
        neu = ergebnis(module_map)
        fehler = vergleichen(neu, alt)
        if fehler:
            for pfad, n, x in fehler:
                print(f"FEHL {pfad}\n"
                      f"  neu: {json.dumps(n, ensure_ascii=False)[:200]}\n"
                      f"  alt: {json.dumps(x, ensure_ascii=False)[:200]}")
            print(f"\n{len(fehler)} Abweichung(en)")
            sys.exit(1)
        print(f"snapshot.json unveraendert ({len(neu)} Faelle)")
        return

    daten.parent.mkdir(parents=True, exist_ok=True)
    daten.write_text(json.dumps(ergebnis(module_map), ensure_ascii=False,
                                indent=1) + "\n", encoding="utf-8")
    print(f"→ {daten}")


if __name__ == "__main__":
    main()
