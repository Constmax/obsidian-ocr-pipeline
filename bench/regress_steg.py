#!/usr/bin/env python3
"""Regression der Spaltenerkennung: alte gegen neue _steg-Auswahl.

  source .venv-mlxocr/bin/activate && python .ocr-bench/regress_steg.py

Laeuft ueber ALLE vektoriellen Seiten des Bestands — dort ist der Textlayer
die Wahrheit, es braucht keine Inferenz, und genau dort greift die Aenderung.
Verglichen wird die fertige Absatzausgabe, nicht der Zwischenstand: eine
Stegverschiebung, die im Ergebnis nichts aendert, ist keine Regression.

Ausgegeben wird je geaenderter Seite, ob Text hinzukam, verlorenging oder nur
die Reihenfolge wechselte. Textverlust ist der einzige harte Fehler — er waere
ohne das Original unauffindbar.
"""
import json, re, sys
from collections import Counter
from pathlib import Path

from pfade import BENCH, WURZEL as VAULT   # legt pdf2md/ auf sys.path
import pdf2md as M
import layout as L
import zusammenbau as Z


def alt_steg(mit_box):
    """Die Fassung vor der Aenderung: nur die groesste Luecke als Kandidat."""
    if len(mit_box) < 8:
        return None
    satz = [z for z in mit_box
            if not Z.ist_boilerplate(z[0], z[1][1])] or mit_box
    starts = sorted(z[1][0] for z in satz)
    luecke, pos = 0, None
    for a, b in zip(starts, starts[1:]):
        if b - a > luecke:
            luecke, pos = b - a, (a + b) / 2
    if pos is None:
        return None
    breite = max(z[1][2] for z in satz) - min(z[1][0] for z in satz)
    voll = [z for z in mit_box if z[1][2] - z[1][0] > breite * 0.6]
    n_links = sum(1 for z in mit_box if z[1][0] < pos)
    anteil = min(n_links, len(mit_box) - n_links) / len(mit_box)
    kreuzer = sum(1 for z in mit_box
                  if z not in voll and z[1][0] < pos < z[1][2])
    sauber = luecke >= breite * 0.08 and kreuzer <= 0.02 * len(mit_box)
    if anteil < 0.25 or not (luecke >= breite * 0.25 or sauber):
        return None
    return pos, voll


def woerter(absaetze):
    return Counter(re.findall(r"[\wÄÖÜäöüß§]+", " ".join(absaetze)))


def buchstaben(absaetze):
    """Zeichenmultimenge ohne Leerraum, Trennstriche und Auszeichnung.

    Der Wortvergleich allein taeuscht hier: sobald die Spalten richtig
    getrennt sind, werden die Trennstriche am Zeilenende aufgeloest, und
    "Allge" + "meininteresses" verschwinden zugunsten von
    "Allgemeininteresses". Wortweise sieht das nach zwei verlorenen und einem
    gewonnenen Wort aus, obwohl kein einziger Buchstabe fehlt. Erst auf
    Zeichenebene zeigt sich echter Verlust.

    Auszeichnung faellt mit heraus, nicht nur die Sternchen: aendert sich der
    Ueberschriftgrad eines Absatzes, verschwinden `#`-Zeichen, und das sah in
    einem ersten Lauf nach "6 Zeichen verloren" aus, obwohl kein Buchstabe
    fehlte.
    """
    return Counter(re.sub(r"[\s\-–*#>|\[\]^]+", "", " ".join(absaetze)))


def seite_bauen(page):
    kaesten, _ = L.kaesten_erkennen(page, False,
                                    [t[2] for t in L.tabellen_markdown(page)])
    zeilen = M.textlayer_zeilen(page)
    return Z.zusammenfuegen(L.spalten_trennen(L.kaesten_zuordnen(zeilen,
                                                                 kaesten)))


def main():
    import fitz
    seiten = [s for s in json.loads((BENCH / "pages.json").read_text())
              if not s["scanned"]]
    nach_datei = {}
    for s in seiten:
        nach_datei.setdefault(s["file"], []).append(s["page"])

    n, gleich, gewinn, verlust, umsortiert = 0, 0, [], [], []
    for datei in sorted(nach_datei):
        pfad = VAULT / datei
        if not pfad.exists():
            continue
        doc = fitz.open(pfad)
        for p in doc:
            if p.rotation:
                p.remove_rotation()
        Z.laufend_setzen(M.laufende_zeilen(doc))
        for nr in sorted(nach_datei[datei]):
            if nr > doc.page_count:
                continue
            page = doc[nr - 1]
            n += 1
            neu_f = L._steg
            try:
                L._steg = alt_steg
                a = seite_bauen(page)
                L._steg = neu_f
                b = seite_bauen(page)
            except Exception as e:                      # Seite ueberspringen
                L._steg = neu_f
                print(f"  FEHLER {datei} S.{nr}: {e}")
                continue
            if a == b:
                gleich += 1
                continue
            wa, wb = woerter(a), woerter(b)
            ba, bb = buchstaben(a), buchstaben(b)
            plus = sum((wb - wa).values())
            minus = sum((wa - wb).values())
            z_minus = sum((ba - bb).values())
            eintrag = (datei, nr, plus, minus, z_minus)
            if z_minus:
                verlust.append(eintrag)
            elif minus or plus:
                gewinn.append(eintrag)       # nur Trennstriche aufgeloest
            else:
                umsortiert.append(eintrag)
        doc.close()

    print(f"\n{n} vektorielle Seiten geprueft")
    print(f"  unveraendert            : {gleich}")
    print(f"  nur umsortiert          : {len(umsortiert)}")
    print(f"  Trennstriche aufgeloest : {len(gewinn)}")
    print(f"  Zeichen VERLOREN        : {len(verlust)}")
    for gruppe, titel in ((verlust, "Zeichenverlust"),
                          (gewinn, "Trennstriche aufgeloest")):
        if not gruppe:
            continue
        print(f"\n{titel} ({len(gruppe)}):")
        for datei, nr, plus, minus, zm in sorted(
                gruppe, key=lambda e: -(e[4] or e[3]))[:20]:
            print(f"  {Path(datei).name[:44]:44s} S.{nr:3d}  "
                  f"Woerter +{plus}/-{minus}  Zeichen -{zm}")
    print(f"\nnur umsortiert:")
    for datei, nr, _, _, _ in umsortiert[:15]:
        print(f"  {Path(datei).name[:44]:44s} S.{nr:3d}")


if __name__ == "__main__":
    main()
