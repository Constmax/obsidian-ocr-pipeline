#!/usr/bin/env python3
"""PP-DocLayoutV3 gegen `layout_erkennen()` — Spaltenentscheidung und Laufzeit.

  source ~/.venvs/mlxocr/bin/activate
  pip install "transformers>=5.15" torch torchvision opencv-python-headless
  python bench/layoutmodell_test.py                    # Wahrheitssatz
  python bench/layoutmodell_test.py --stichprobe 60    # Bestandsabgleich
  python bench/layoutmodell_test.py --dump <pdf> 5     # rohe Modellausgabe

Warum es das gibt: die Spaltenentscheidung ist die riskanteste Einzelentscheidung
der Pipeline. Ein falscher Laengsschnitt zerlegt jede Zeile der Seite in zwei
Haelften (Nachtrag 12: 125 Seiten so zerschnitten), ein versaeumter kostet nur
Reihenfolge. Die heutige Entscheidung faellt ueber ein Tintenprofil und trifft
13 von 14 handgeprueften Seiten. Ob ein Layoutmodell das besser kann, ist nie
gemessen worden — Pfad A ist 2026-07 als *PaddlePaddle-Framework* ausgeschieden
(9.226 s/Seite, 5.679 MB RSS auf 8 GB), und der Layoutdetektor flog dabei
ungeprueft mit heraus. Inzwischen gibt es die Gewichte als safetensors und
`transformers` faehrt sie ohne PaddlePaddle.

Gemessen werden drei Dinge, weil sie drei verschiedene Entscheidungen tragen:

  1. **Spaltenentscheidung** gegen den Wahrheitssatz — getrennt nach den beiden
     Fehlerrichtungen, weil ihre Kosten asymmetrisch sind. Ein Modell, das
     insgesamt besser trifft, aber oefter falsch schneidet, ist ein Rueckschritt.
  2. **Laufzeit je Seite** auf der Zielmaschine. Die Rechnung geht nur auf, wenn
     der Detektor klein bleibt gegen die ~33 s/Seite Inferenz.
  3. **Welche Klassen er ueberhaupt findet** — die offenen Punkte (gescannte
     Diagramme, Tabellen und Kaesten im Rasterscan, Vollbreiten-Elemente am
     Kachelrand) sind allesamt Klassifikationsfragen, keine Stegfragen.

Die Seitenbilder sind urheberrechtlich geschuetzte Scans und liegen nicht im
Repo; das Skript laeuft im Vault gegen `raw/` und `pages.json` — wie
`regress_steg.py` und `bench_ocr.py` auch.
"""
import argparse
import json
import random
import time

from pfade import BENCH, WURZEL as VAULT     # legt pdf2md/ auf sys.path
import layout as L

MODELL = "PaddlePaddle/PP-DocLayoutV3_safetensors"

# Klassen, die Flaeche im Satzspiegel belegen und damit eine Spalte belegen
# koennen. Kopf-, Fuss- und Randklassen zaehlen nicht mit — genau wie
# `_steg()` Boilerplate ausnimmt, weil eine Seitenzahl im Bund das
# x-Histogramm dort zerlegt, wo der Steg liegt (Nachtrag 9).
#
# Gegen die echte id2label-Ausgabe des Modells geprueft (--dump): die Menge
# enthaelt 25 Eintraege mit Duplikaten (footer/header/formula/text je zweimal)
# und dazu reference_content + vision_footnote; die urspruenglich angenommenen
# figure/table_title/chart_title/page_number/header_image/footer_image gibt es
# nicht.
SATZ_KLASSEN = {"text", "paragraph_title", "doc_title", "footnote",
                "vision_footnote", "abstract", "content", "reference",
                "reference_content", "algorithm", "formula", "table",
                "chart", "image", "seal", "figure_title", "formula_number"}
RAND_KLASSEN = {"header", "footer", "number", "aside_text"}

# Wahrheitssatz. Die 14 handgeprueften Seiten aus Nachtrag 12 sind nirgends als
# Liste festgehalten worden — das ist selbst ein Befund. Was hier steht, ist aus
# `bench/ERGEBNIS.md` und `bench/BENCHMARK-SET.md` rekonstruiert; jede Zeile
# nennt ihren Beleg. Vor dem ersten Lauf durchsehen und ergaenzen: eine
# Wahrheit, die man nicht selbst gesehen hat, ist keine.
#
#   (Dateiname-Fragment, Seite, Erwartung, Beleg)
WAHRHEIT = [
    ("Verwaltungsprozessrecht",        10, "zweispaltig", "BENCHMARK-SET 01, dichter Kleindruck"),
    ("Allgemeines-Verwaltungsrecht",   15, "zweispaltig", "BENCHMARK-SET 02"),
    ("Strafrecht AT II",                8, "einspaltig",  "BENCHMARK-SET 03, Durchschlag"),
    ("skript-arbeitsrecht-xxl",        12, "einspaltig",  "BENCHMARK-SET 04, sauberer Blocksatz"),
    ("Verwaltungsprozessrecht",        54, "einspaltig",  "Nachtrag 12: Satz in 2/3 Blattbreite"),
    ("strafrecht-fall-02",             19, "zweispaltig", "Nachtrag 12: Steg 0,467-0,529, Rauschen im Tal"),
    ("Strafrecht AT VI",                3, "einspaltig",  "Nachtrag 12: war faelschlich laengs geschnitten"),
    ("Verwaltungsrecht AT Fall 8",     10, "zweispaltig", "Nachtrag 12: DER bekannte Fehlschlag, flacher Steg"),
    ("BGB AT Fall 3",                   2, "zweispaltig", "Nachtrag 1: 4 Seiten zweispaltig @50-53 %"),
    ("BGB AT Fall 3",                   3, "zweispaltig", "Nachtrag 1"),
    ("BGB AT Fall 3",                   4, "zweispaltig", "Nachtrag 1"),
    ("BGB-AT_10",                       3, "einspaltig",  "Nachtrag 6: dichte Prosa mit gestapelten Kaesten"),
    ("BGB-AT_10",                       4, "einspaltig",  "Nachtrag 6"),
    ("Strafrecht AT VI",                5, "einspaltig",  "Nachtrag 6: Baumdiagramm, 4 Signale gescheitert"),
    ("Strafrecht AT VI",                8, "einspaltig",  "Nachtrag 6: KEIN Diagramm, 4 Pfeilzeichen"),
]

# Seiten, bei denen nicht der Steg, sondern die Klassifikation die offene Frage
# ist. Fuer sie gibt das Skript die gefundenen Klassen aus, statt zu bewerten.
KLASSENFRAGE = {
    ("Strafrecht AT VI", 5): "gescanntes Baumdiagramm — wird figure/chart erkannt?",
    ("Strafrecht AT VI", 8): "Gegenprobe: hier darf KEIN Diagramm herauskommen",
    ("BGB-AT_10", 3): "gestapelte Kaesten — table oder text?",
    ("Verwaltungsprozessrecht", 10): "Fussnotenblock — eigene footnote-Region?",
}


# --- Modellgrenze -----------------------------------------------------------
# Alles, was das Modell betrifft, steht in diesen zwei Funktionen. Die API ist
# gegen den Quelltext von transformers 5.15.0 geprueft
# (models/pp_doclayout_v3/), aber nicht gegen echte Gewichte gefahren — beim
# ersten Lauf zuerst `--dump` benutzen und, falls noetig, nur hier nachziehen.

def modell_laden():
    """(modell, prozessor, id2label). Laedt einmal, ~124 MiB, CPU reicht."""
    import torch
    from transformers import AutoImageProcessor, AutoModelForObjectDetection

    torch.set_grad_enabled(False)
    prozessor = AutoImageProcessor.from_pretrained(MODELL)
    modell = AutoModelForObjectDetection.from_pretrained(MODELL).eval()
    return modell, prozessor, modell.config.id2label


def regionen(bild, modell, prozessor, id2label, schwelle=0.5):
    """[(klasse, x0, y0, x1, y1, score)] in Bildkoordinaten, in Leseordnung.

    `post_process_object_detection` liefert die Treffer bereits nach
    `order_seq` sortiert — das Modell sagt die Leseordnung im selben
    Forward-Pass voraus. Genau das rekonstruiert `spalten_trennen()` heute
    aus Geometrie.
    """
    import torch

    eingabe = prozessor(images=[bild], return_tensors="pt")
    ausgabe = modell(**eingabe)
    ziel = torch.tensor([bild.size[::-1]])           # (hoehe, breite)
    treffer = prozessor.post_process_object_detection(
        ausgabe, threshold=schwelle, target_sizes=ziel)[0]
    aus = []
    for score, kennung, kasten in zip(treffer["scores"], treffer["labels"],
                                      treffer["boxes"]):
        x0, y0, x1, y1 = (float(v) for v in kasten)
        aus.append((id2label[int(kennung)], x0, y0, x1, y1, float(score)))
    return aus


# --- Vergleichbar machen ----------------------------------------------------

def spalten_aus_regionen(regs, breite, mindest_anteil=0.25):
    """('einspaltig'|'zweispaltig', steg_relativ) aus Layoutregionen.

    Damit die Zahlen neben `layout_erkennen()` stehen koennen, muss aus den
    Regionen dieselbe Auskunft werden: Spaltigkeit plus Stegposition. Der Test
    ist derselbe wie in `_steg()`, nur auf Regionen statt auf Tinte — und
    dadurch mit einer Handvoll Kaesten statt hunderten Zeilen belegt:

      * Rand- und Vollbreitenregionen zaehlen nicht mit (eine laufende
        Kopfzeile ueberspannt jeden Steg),
      * die Luecke darf von keiner Satzregion ueberquert werden,
      * beide Seiten muessen nennenswert besetzt sein.
    """
    satz = [r for r in regs
            if r[0] in SATZ_KLASSEN and r[0] not in RAND_KLASSEN]
    if len(satz) < 2:
        return "einspaltig", None
    # Vollbreite Regionen (Ueberschrift ueber beide Spalten, Tabelle) sind
    # keine Spaltenbelegung, sondern ueberspannen sie.
    spanne = max(r[3] for r in satz) - min(r[1] for r in satz)
    if spanne <= 0:
        return "einspaltig", None
    schmal = [r for r in satz if (r[3] - r[1]) <= spanne * 0.6]
    if len(schmal) < 2:
        return "einspaltig", None

    # Belegung ueber die Blattbreite, 1 Promille Aufloesung.
    n = 1000
    belegt = [False] * n
    for _, x0, _, x1, _, _ in schmal:
        a = max(0, min(n - 1, int(x0 / breite * n)))
        b = max(0, min(n, int(x1 / breite * n)))
        for i in range(a, b):
            belegt[i] = True

    # Groesster freier Lauf im mittleren Drittel — dort und nur dort liegt ein
    # Spaltensteg. Am Rand ist jede Luecke bedeutungslos (Nachtrag 12).
    lo, hi = int(n * 0.30), int(n * 0.70)
    beste, akt = (0, None), None
    for i in range(lo, hi):
        if not belegt[i]:
            akt = i if akt is None else akt
        else:
            if akt is not None and i - akt > beste[0]:
                beste = (i - akt, (akt + i) // 2)
            akt = None
    if akt is not None and hi - akt > beste[0]:
        beste = (hi - akt, (akt + hi) // 2)

    luecke, mitte = beste
    if mitte is None or luecke < n * 0.015:          # Mindestbreite wie L._tintensteg
        return "einspaltig", None
    pos = mitte / n * breite
    links = sum(1 for r in schmal if r[3] <= pos)
    rechts = sum(1 for r in schmal if r[1] >= pos)
    if min(links, rechts) / len(schmal) < mindest_anteil:
        return "einspaltig", None
    return "zweispaltig", mitte / n


def seite_bild(page, dpi):
    """PIL-Bild der Seite.

    Der Bildvorverarbeiter skaliert hart auf 800x800 (`size` im
    ImageProcessor, image_mean=[0,0,0]/std=[1,1,1]) und haelt das
    Seitenverhaeltnis nicht. Sobald BEIDE Kanten ueber 800 px liegen, bringt
    hoeher rendern dem Detektor nichts mehr — dieselbe Deckel-Logik wie beim
    VLM mit seinen 1.003.520 Pixeln, die 150 dpi zur Voreinstellung gemacht hat.
    """
    from io import BytesIO
    from PIL import Image

    pm = page.get_pixmap(dpi=dpi)
    return Image.open(BytesIO(pm.tobytes("png"))).convert("RGB")


def entdrehen(doc):
    """Rotation einmal beim Oeffnen herausnehmen — vor JEDER Messung.

    Sonst laeuft die Heuristik auf `page.rect` (gedreht) und das Modell auf dem
    gerenderten Bild, und die beiden Zahlen sind nicht vergleichbar. `/Rotate
    270` kommt im Bestand vor (Nachtrag 9, WuV-Doppelbogen); betroffen ist nur
    das Objekt im Speicher, `raw/` bleibt unberuehrt.
    """
    for p in doc:
        if p.rotation:
            p.remove_rotation()
    return doc


# --- Bestand finden ---------------------------------------------------------

def pdf_suchen(fragment):
    """Erste PDF unter `raw/`, deren Name das Fragment enthaelt."""
    treffer = sorted(p for p in (VAULT / "raw").rglob("*.pdf")
                     if fragment.lower() in p.name.lower())
    return treffer[0] if treffer else None


def scanseiten(n, saat=20260813):
    """Zufallsstichprobe aus den Scanseiten des Bestands."""
    quelle = BENCH / "pages.json"
    if not quelle.exists():
        quelle = VAULT / "pages.json"
    if not quelle.exists():
        raise SystemExit("!! pages.json nicht gefunden — im Vault laufen lassen")
    alle = [s for s in json.loads(quelle.read_text()) if s.get("scanned")]
    random.Random(saat).shuffle(alle)
    return alle[:n]


# --- Laeufe -----------------------------------------------------------------

def lauf_wahrheit(a):
    import fitz

    modell, prozessor, id2label = modell_laden()
    print(f"Modell: {MODELL}")
    print(f"Klassen ({len(id2label)}): {', '.join(sorted(id2label.values()))}\n")

    kopf = (f"{'Datei':34s} {'S.':>3s}  {'erwartet':12s} "
            f"{'Heuristik':12s} {'Modell':12s}  {'s':>5s}")
    print(kopf)
    print("-" * len(kopf))

    tr_h = tr_m = n = 0
    falsch_geschnitten = {"heuristik": [], "modell": []}   # teuer
    verpasst = {"heuristik": [], "modell": []}             # billig
    zeiten = []
    klassen_notiz = []

    for fragment, nr, erwartet, beleg in WAHRHEIT:
        pfad = pdf_suchen(fragment)
        if pfad is None:
            print(f"{fragment[:34]:34s} {nr:3d}  -- nicht gefunden --")
            continue
        doc = entdrehen(fitz.open(pfad))
        if nr > doc.page_count:
            print(f"{pfad.name[:34]:34s} {nr:3d}  -- Seite fehlt --")
            doc.close()
            continue
        page = doc[nr - 1]
        art_h, _ = L.layout_erkennen(page)

        bild = seite_bild(page, a.dpi)
        t0 = time.perf_counter()
        regs = regionen(bild, modell, prozessor, id2label, a.schwelle)
        dt = time.perf_counter() - t0
        zeiten.append(dt)
        art_m, _ = spalten_aus_regionen(regs, bild.width)

        n += 1
        for name, art, teuer, billig in (
                ("heuristik", art_h, falsch_geschnitten["heuristik"], verpasst["heuristik"]),
                ("modell", art_m, falsch_geschnitten["modell"], verpasst["modell"])):
            if art == erwartet:
                continue
            (teuer if art == "zweispaltig" else billig).append(
                (pfad.name, nr, beleg))
        tr_h += art_h == erwartet
        tr_m += art_m == erwartet

        marke = lambda art: " " if art == erwartet else "!"
        print(f"{pfad.name[:34]:34s} {nr:3d}  {erwartet:12s} "
              f"{art_h:11s}{marke(art_h)} {art_m:11s}{marke(art_m)}  {dt:5.2f}")

        if (fragment, nr) in KLASSENFRAGE:
            gezaehlt = {}
            for k, *_ in regs:
                gezaehlt[k] = gezaehlt.get(k, 0) + 1
            klassen_notiz.append((pfad.name, nr, KLASSENFRAGE[(fragment, nr)],
                                  gezaehlt))
        doc.close()

    if not n:
        raise SystemExit("!! keine Seite geprueft — laeuft das hier im Vault?")

    print(f"\n{n} Seiten geprueft")
    print(f"  Heuristik richtig : {tr_h}/{n}")
    print(f"  Modell richtig    : {tr_m}/{n}")
    print(f"\nFehlerrichtungen (die teure zuerst):")
    for name in ("heuristik", "modell"):
        print(f"  {name:10s} falsch geschnitten: {len(falsch_geschnitten[name])}"
              f"   versaeumt: {len(verpasst[name])}")
        for datei, nr, beleg in falsch_geschnitten[name]:
            print(f"    !! {datei[:40]:40s} S.{nr:3d}  {beleg}")
    if zeiten:
        zeiten.sort()
        print(f"\nLaufzeit je Seite: Median {zeiten[len(zeiten)//2]:.2f} s, "
              f"min {zeiten[0]:.2f} s, max {zeiten[-1]:.2f} s "
              f"(ohne Laden, {a.dpi} dpi)")

    if klassen_notiz:
        print("\nKlassen auf den offenen Seiten:")
        for datei, nr, frage, gezaehlt in klassen_notiz:
            verteilung = ", ".join(f"{k}x{v}" for k, v in
                                   sorted(gezaehlt.items(), key=lambda e: -e[1]))
            print(f"  {datei[:36]:36s} S.{nr:3d}  {frage}\n"
                  f"      -> {verteilung or 'nichts erkannt'}")


def lauf_stichprobe(a):
    """Bestandsabgleich ohne Wahrheit: wo widersprechen sich die beiden?

    Das ist die Zahl, die ueber Aufwand entscheidet. Widersprechen sie sich
    selten, ist das Modell die Muehe nicht wert; widersprechen sie sich oft,
    muessen die Streitfaelle von Hand angesehen werden — und *dann* lohnt der
    Wahrheitssatz seine Erweiterung.
    """
    import fitz

    modell, prozessor, id2label = modell_laden()
    seiten = scanseiten(a.stichprobe)
    streit, n, zeiten = [], 0, []
    for s in seiten:
        pfad = VAULT / s["file"]
        if not pfad.exists():
            continue
        doc = entdrehen(fitz.open(pfad))
        nr = s["page"]
        if nr <= doc.page_count:
            page = doc[nr - 1]
            art_h, _ = L.layout_erkennen(page)
            bild = seite_bild(page, a.dpi)
            t0 = time.perf_counter()
            regs = regionen(bild, modell, prozessor, id2label, a.schwelle)
            zeiten.append(time.perf_counter() - t0)
            art_m, steg = spalten_aus_regionen(regs, bild.width)
            n += 1
            if art_h != art_m:
                streit.append((pfad.name, nr, art_h, art_m, steg))
        doc.close()

    print(f"\n{n} Scanseiten, {len(streit)} Widersprueche "
          f"({len(streit)/max(n,1):.0%})")
    for datei, nr, h, m, steg in streit:
        s = f"@{steg:.0%}" if steg else "—"
        print(f"  {datei[:44]:44s} S.{nr:3d}  Heuristik {h:12s} "
              f"Modell {m:12s} {s}")
    if zeiten:
        zeiten.sort()
        print(f"\nLaufzeit je Seite: Median {zeiten[len(zeiten)//2]:.2f} s")
    print("\nDie Widersprueche im Bild ansehen, bevor irgendetwas umgestellt "
          "wird. Ein Widerspruch ist kein Fehler des einen oder anderen.")


def lauf_dump(a):
    """Rohe Modellausgabe einer Seite — zum Pruefen der API und der Klassen."""
    import fitz

    modell, prozessor, id2label = modell_laden()
    print(f"id2label ({len(id2label)}): {id2label}\n")
    doc = entdrehen(fitz.open(a.dump[0]))
    page = doc[int(a.dump[1]) - 1]
    bild = seite_bild(page, a.dpi)
    t0 = time.perf_counter()
    regs = regionen(bild, modell, prozessor, id2label, a.schwelle)
    dt = time.perf_counter() - t0
    print(f"Bild {bild.width}x{bild.height} @ {a.dpi} dpi, "
          f"{len(regs)} Regionen in {dt:.2f} s (Leseordnung von oben nach unten)\n")
    for i, (klasse, x0, y0, x1, y1, score) in enumerate(regs, 1):
        print(f"  {i:2d}. {klasse:18s} {score:.2f}  "
              f"x {x0:6.0f}-{x1:6.0f}  y {y0:6.0f}-{y1:6.0f}  "
              f"({(x1-x0)/bild.width:.0%} breit)")
    art, steg = spalten_aus_regionen(regs, bild.width)
    art_h, steg_h = L.layout_erkennen(page)
    print(f"\n  Modell    : {art}  {f'@{steg:.0%}' if steg else ''}")
    print(f"  Heuristik : {art_h}  {f'@{steg_h:.0%}' if steg_h else ''}")
    doc.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dpi", type=int, default=110,
                    help="Renderaufloesung. Das Modell skaliert hart auf "
                         "800x800; bei 110 dpi liegt die KURZE Kante einer "
                         "A4-Seite bei ~910 px, hoeher bringt nichts (Standard).")
    ap.add_argument("--schwelle", type=float, default=0.5,
                    help="Mindest-Score je Region (Standard 0.5)")
    ap.add_argument("--stichprobe", type=int, default=0,
                    help="statt Wahrheitssatz: N zufaellige Scanseiten, nur "
                         "Widersprueche")
    ap.add_argument("--dump", nargs=2, metavar=("PDF", "SEITE"),
                    help="rohe Regionen einer Seite ausgeben")
    a = ap.parse_args()

    try:
        if a.dump:
            lauf_dump(a)
        elif a.stichprobe:
            lauf_stichprobe(a)
        else:
            lauf_wahrheit(a)
    except ImportError as e:
        raise SystemExit(
            f"!! {e}\n"
            "   pip install 'transformers>=5.15' torch torchvision "
            "opencv-python-headless\n"
            "   (torchvision braucht der ImageProcessor beim Import, opencv "
            "fuer die Polygonpunkte)")


if __name__ == "__main__":
    main()
