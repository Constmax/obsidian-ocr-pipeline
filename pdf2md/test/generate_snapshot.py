#!/usr/bin/env python3
"""Golden snapshot of pure assembly functions.

  python3 pdf2md/test/generate_snapshot.py            # rewrite snapshot.json
  python3 pdf2md/test/generate_snapshot.py --check    # check against snapshot.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def modules():
    """Load target modules."""
    import layout
    import ocr
    import zusammenbau
    return zusammenbau, layout, ocr


def get_func(name, module_map):
    for mod in module_map:
        if hasattr(mod, name):
            return getattr(mod, name)
    raise SystemExit(f"!! Function {name} not found anywhere")


# --- Fixed Inputs ---------------------------------------------------------

LOC_TEXT = ("<|LOC_100_210_880_225|><|LOC_100_210_880_225|>Er ist somit "
            "Besitzdiener iSd Paragraf 855 BGB.\n"
            "<|LOC_90_245_900_260|><|LOC_90_245_900_260|>Infolgedessen ist L "
            "kein Besitzer und nur M ist Besitzer des Mehls.\n"
            "<|LOC_120_290_300_305|><|LOC_120_290_300_305|>1.\n"
            "Und eine Zeile ganz ohne Koordinaten.")

CLEAN_TEXTS = [
    r"$\rightarrow$ der Pfeil und \text{Fett}",
    r"\underline{unterstrichen} und \( \alpha \)",
    r"$ 5 BGB und $\^{12} als Fussnotenzeichen",
    "§ § 929 und § 854 | BGB und | BGB am Satzanfang",
    "**gesetzliches** **Schuldverhaeltnis**",
    r"\leftarrow \Rightarrow \uparrow \to",
]

STRIP_PUA_TEXTS = [
    "\uf0f0 \uf0e0 \uf0d8 \uf0fc \uf0b7 \uf020 \uf001",
    "ganz normaler Text ohne Sonderzeichen",
]

LEVEL_TEXTS = [
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

HEADING_CASES = [
    ("**Beispiel:**", "Beispiel:"),
    ("**A. Grundsaetzliches zum Unterlassen**",
     "A. Grundsaetzliches zum Unterlassen"),
    ("**Merke**", "Merke"),
    ("**Bargeldes.**", "Bargeldes."),
    ("**IV. Exkurs: Uebersicht – V**", "IV. Exkurs: Uebersicht – V"),
]

BOILERPLATE_TEXTS = [
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

RUNNING_TEXTS = [
    ("Fall 12  |  Begleitskript", None),
    ("Strafrecht BT V", None),
    ("Fall 12 | Begleitskript", None),
    ("Fall 12  |  Begleitskript", 300),
    ("Schuldrecht AT – Fall 12 | Begleitskript", None),
]

BOLD_TEXTS = [
    "Text **fett",
    "**fett** Text **fett**",
    "ganz ohne Auszeichnung",
]

SHORT_LINES = [
    ["Die erste Zeile eines Absatzes im Blocksatz.", (100, 100, 900, 115)],
    ["Die zweite Zeile laeuft voll bis an den Rand.", (100, 120, 900, 135)],
    ["Kurz, mit kleinem Nachsatz", (100, 140, 600, 155)],
    ["der hier direkt anschliesst.", (100, 155, 900, 170)],
    ["Die vierte Zeile ist wieder voll.", (100, 175, 900, 190)],
    ["Und die letzte Zeile endet frueh.", (100, 195, 700, 210)],
]

MARGIN_LABEL_LINES = [
    ["Der Garant ist zur Abwendung des Erfolges verpflichtet, so dass sein",
     (200, 100, 900, 114)],
    ["**Merke:**", (150, 118, 210, 132)],
    ["Unterlassen der Begehung durch aktives Tun entspricht, § 13 StGB.",
     (200, 118, 900, 132)],
    ["Daran schliesst sich die Frage der Garantenstellung an.",
     (200, 136, 900, 150)],
    ["Sie ist der Kern jeder Unterlassungspruefung.", (200, 154, 900, 168)],
]

ATTACH_FN_LINES = [
    ["7", (8, 915, 20, 925)],
    ["Vgl. BGH, NJW 2014, 1524.", (50, 915, 400, 925)],
    ["Der Anspruch ist begruendet.", (90, 200, 800, 215)],
]

FN_OBSIDIAN_PARAGRAPHS = [
    "Er ist ein Besitzdiener.1",
    "1 Vgl. BGH, NJW 2014, 1524.",
    "2 So auch MüKo-BGB, § 855 Rn. 14.",
    "| a | b |",
]

FORMAT_HEADINGS_PARAGRAPHS = [
    "1. Anspruch aus § 816 I S. 2 BGB",
    "cc) Diese Ansicht ist mit der h.M. abzulehnen.",
    "Fliesstext ohne Marker",
    "**1. Anspruch aus § 985 BGB auf Rueckgabe des Bargeldes.**",
    "3. Anspruch aus § 433 II BGB",
]

FRAGMENT_LINES = [
    ["1.", (90, 210, 100, 225)],
    ["Die Abtretung als Verfuegung", (110, 210, 500, 225)],
    ["weiterer Text auf anderer Zeile", (90, 240, 400, 255)],
]

ASSEMBLE_MARGIN_LABEL = [
    ["**Beispiel:**", (166, 98, 237, 112)],
    ["Mutter M putzt gerade die Fenster ihrer Terrasse und stoesst dabei aus "
     "Unachtsamkeit einen", (166, 97, 899, 111)],
    ["Blumentopf herunter, welcher sodann den Dieb D trifft.",
     (166, 116, 899, 130)],
]

ASSEMBLE_FOOTNOTE = [
    ["Juristisches Repetitorium", (0, 50, 1000, 65)],
    ["Der Anspruch ist begruendet.1", (90, 100, 800, 115)],
    ["1 Vgl. BGH, NJW 2014, 1524.", (8, 915, 500, 930)],
]

CALLOUT_PARAGRAPHS = ["Absatz eins.", "Absatz zwei\nmit Umbruch",
                      "Letzter Absatz."]

GRID_LEFT = [
    ["1. Was versteht man unter Geldwertvindikation?", (100, 100, 500, 115)],
    ["2. Welche Rechtsfolgen hat die Anfechtung?", (100, 130, 500, 145)],
    ["3. Was ist ein Anwartschaftsrecht?", (100, 160, 500, 175)],
    ["4. Wie unterscheiden sich Besitz und Eigentum?", (100, 190, 500, 205)],
    ["5. Was regelt § 985 BGB?", (100, 220, 500, 235)],
    ["6. Wer ist Besitzer?", (100, 250, 500, 265)],
]

GRID_RIGHT = [
    ["Besitzdiener iSd Paragraf 855 BGB.", (520, 100, 900, 115)],
    ["Die Anfechtung macht den Vertrag von", (520, 130, 900, 145)],
    ["Anfang an nichtig.", (520, 160, 900, 175)],
    ["Ein Anwartschaftsrecht ist ein", (520, 190, 900, 205)],
    ["Recht auf den Erwerb einer Sache.", (520, 220, 900, 235)],
    ["Der Besitzer hat die Sachherrschaft.", (520, 250, 900, 265)],
]

LOOP_TEXTS = [
    " ".join(["a b c d e"] * 30),
    " ".join(f"({j})" for j in range(1982, 2260)),
    ("Der Anspruch des K gegen B ergibt sich aus dem Kaufvertrag nach "
     "Paragraf 433 Absatz 2 BGB."),
]

DERAILED_CASES = [
    (" ".join(["a b c d e"] * 30), None, False),
    ("Der Anspruch ist begruendet und faellig, weil der Kaufvertrag "
     "wirksam zustande gekommen ist.", 500, False),
    ("Kurz abgebrochen.", 500, False),
    ("Satz." + " Und noch ein Satz mit ausreichend Substanz." * 100,
     500, False),
]

TRIM_LOOP_LINES = [
    ["Dieselbe Fussnote steht hier.", (0, 0, 1000, 10)],
    ["Ansprueche aus Paragraf 823, Paragraf 826, Paragraf 831, Paragraf 840.",
     (0, 0, 1000, 10)],
]

SEAM_TOP = [
    ["Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen ist "
     "L kein Besitzer und nur M ist Besitzer des Mehls. Ob es",
     (0, 0, 1000, 10)],
]

SEAM_BOTTOM = [
    ["Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
     "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
     "Paragraf 935 BGB.",
     (0, 0, 1000, 10)],
    ["Fussnote 13: BGH NJW 2014, 1524.", (0, 0, 1000, 10)],
]

SEAM_SHIFTED = [
    ["Anknuepfungspunkt waere also, dass H trotz des aus der "
     "Besitzverschaiung folgenden Anscheins nicht nachgeforscht hat.",
     (0, 0, 1000, 10)],
    ["Anknuepfungspunkt waere also, dass H trotz des aus der "
     "Besitzverschaffung folgenden Anscheins nicht nachgeforscht hat. "
     "Fuer eine Nachforschungsobliegenheit spricht wenig.", (0, 0, 1000, 10)],
]

SEAM_DUPLICATE = [
    ["Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.",
     (0, 0, 1000, 10)],
    ["Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.",
     (0, 0, 1000, 10)],
    ["Ein Schaden ist entstanden.", (0, 0, 1000, 10)],
]


def cases(module_map):
    zusammenbau, layout, ocr = module_map

    def set_running_wrapper(values):
        get_func("set_running", module_map)(values)

    def read_discarded(_):
        return getattr(get_func("assemble_paragraphs", module_map), "discarded", [])

    z = lambda t: [t, (0, 0, 1000, 10)]
    M = lambda name: get_func(name, module_map)
    one = lambda args: [args]

    return [
        ("parse_zeilen", None, one([LOC_TEXT]), M("parse_lines")),
        ("saeubern", None, [[t] for t in CLEAN_TEXTS], M("clean_text")),
        ("entpua", None, [[t] for t in STRIP_PUA_TEXTS], M("strip_pua")),
        ("ebene", None, [[t] for t in LEVEL_TEXTS], M("level")),
        ("ist_ueberschrift", None,
         [[t, n] for t, n in HEADING_CASES], M("is_heading")),
        ("ist_boilerplate_ohne_laufend", None,
         [[t, y] for t, y in BOILERPLATE_TEXTS], M("is_boilerplate")),
        ("ist_boilerplate_mit_laufend",
         lambda: set_running_wrapper({"Fall 12 | Begleitskript", "Strafrecht BT V"}),
         [[t, y] for t, y in RUNNING_TEXTS], M("is_boilerplate")),
        ("fett_ausgleichen", None, [[t] for t in BOLD_TEXTS],
         M("balance_bold")),
        ("kurze_zeilen", None, one([SHORT_LINES]), M("short_lines")),
        ("randlabel_vorziehen", None, one([MARGIN_LABEL_LINES, 18]),
         M("promote_margin_labels")),
        ("fussnotennummern_anbinden", None, one([ATTACH_FN_LINES]),
         M("attach_footnote_numbers")),
        ("fussnoten_obsidian", None, one([FN_OBSIDIAN_PARAGRAPHS]),
         M("footnotes_obsidian")),
        ("gliederung_auszeichnen", None, one([FORMAT_HEADINGS_PARAGRAPHS]),
         M("format_headings")),
        ("fragmente_verschmelzen", None, one([FRAGMENT_LINES, 595]),
         M("merge_fragments")),
        ("zusammenfuegen_randmarke", None, one([ASSEMBLE_MARGIN_LABEL]),
         M("assemble_paragraphs")),
        ("zusammenfuegen_fussnote", None, one([ASSEMBLE_FOOTNOTE]),
         M("assemble_paragraphs")),
        ("als_callout", None, one([CALLOUT_PARAGRAPHS, "Test-Titel"]),
         M("as_callout")),
        ("frage_antwort_raster", None,
         one([GRID_LEFT, GRID_RIGHT]), M("question_answer_grid")),
        ("schleifenlaenge", None, [[t] for t in LOOP_TEXTS],
         M("loop_length")),
        ("entgleist", None, [list(f) for f in DERAILED_CASES],
         M("is_derailed")),
        ("schleife_kuerzen", None,
         one([TRIM_LOOP_LINES
               + [z("Dieselbe Fussnote steht hier.")] * 100
               + [z(" ".join(["V."] * 40))]]),
         M("trim_loop")),
        ("ueberlappung_kuerzen_fortsetzung", None,
         one([SEAM_TOP, SEAM_BOTTOM]), M("trim_overlap")),
        ("ueberlappung_kuerzen_duplette", None,
         one([SEAM_DUPLICATE[:1], SEAM_DUPLICATE[1:]]),
         M("trim_overlap")),
        ("ueberlappung_kuerzen_lesefehler", None,
         one([SEAM_SHIFTED[:1], SEAM_SHIFTED[1:]]),
         M("trim_overlap")),
        ("zusammenfuegen_verworfen", None, one([None]),
         read_discarded),
    ]


def to_jsonable(x):
    if isinstance(x, tuple):
        return [to_jsonable(e) for e in x]
    if isinstance(x, list):
        return [to_jsonable(e) for e in x]
    if isinstance(x, set):
        return sorted(x)
    return x


def compute_result(module_map):
    out = []
    for name, setup, arg_set, func in cases(module_map):
        if setup:
            setup()
        try:
            values = [func(*a) for a in arg_set]
        except Exception as e:
            raise SystemExit(f"!! {name}: {e}")
        out.append({"name": name, "eingaben": to_jsonable(arg_set),
                    "ausgabe": to_jsonable(values)})
    return out


def compare(new_val, old_val, path=""):
    errors = []
    if isinstance(new_val, list) and isinstance(old_val, list) and len(new_val) == len(old_val) \
            and new_val and isinstance(new_val[0], dict):
        for n, a in zip(new_val, old_val):
            errors += compare(n, a, path + "/" + a.get("name", "?"))
        return errors
    if new_val != old_val:
        errors.append((path, new_val, old_val))
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", "--pruefen", dest="check", action="store_true",
                    help="Only check against snapshot.json, do not write")
    a = ap.parse_args()
    module_map = modules()

    data_file = ROOT / "test" / "daten" / "snapshot.json"
    if a.check:
        if not data_file.exists():
            raise SystemExit(f"!! {data_file} missing — run without --check first")
        old_data = json.loads(data_file.read_text())
        new_data = compute_result(module_map)
        errors = compare(new_data, old_data)
        if errors:
            for path, n, x in errors:
                print(f"MISMATCH {path}\n"
                      f"  new: {json.dumps(n, ensure_ascii=False)[:200]}\n"
                      f"  old: {json.dumps(x, ensure_ascii=False)[:200]}")
            print(f"\n{len(errors)} mismatch(es)")
            sys.exit(1)
        print(f"snapshot.json unchanged ({len(new_data)} cases)")
        return

    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(compute_result(module_map), ensure_ascii=False,
                                    indent=1) + "\n", encoding="utf-8")
    print(f"→ {data_file}")


if __name__ == "__main__":
    main()
