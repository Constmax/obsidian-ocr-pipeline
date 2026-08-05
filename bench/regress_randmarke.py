#!/usr/bin/env python3
"""Regression der Randmarken-Ausnahme in `ist_ueberschrift`.

  source .venv-mlxocr/bin/activate && python .ocr-bench/regress_randmarke.py

Geprueft wird gegen die Fassung ohne Ausnahme: eine kurze, ganz fette Zeile war
dort IMMER eine Ueberschrift, auch wenn nur "**Beispiel:**" darauf steht.

Wie in regress_randlabel.py mit Vorfilter — die Regel kann nur auf Seiten
wirken, auf denen eine alleinstehende Marke steht. Ohne Filter waeren es 24
Minuten PyMuPDF-Tabellensuche fuer eine Frage, die damit nichts zu tun hat.

Erwartet wird: keine Seite verliert ein Zeichen. Die Aenderung fuegt Absaetze
zusammen, sie loescht nichts. Zeichenverlust hiesse, die Regel ist falsch.
"""
import json, sys
from pathlib import Path

from pfade import BENCH, WURZEL as VAULT   # legt pdf2md.py auf sys.path
import pdf2md as M
from regress_steg import buchstaben, seite_bauen


def alt_ueberschrift(text, nackt):
    """Die Fassung vor der Aenderung: Randmarke zaehlte als Ueberschrift."""
    return (text.startswith("**") and text.endswith("**")
            and text.count("**") == 2 and len(nackt) <= 90)


def main():
    import fitz
    seiten = [s for s in json.loads((BENCH / "pages.json").read_text())
              if not s["scanned"]]
    nach_datei = {}
    for s in seiten:
        nach_datei.setdefault(s["file"], []).append(s["page"])

    echt = M.ist_ueberschrift
    n, gleich, geaendert, verlust = 0, 0, [], []
    for datei in sorted(nach_datei):
        pfad = VAULT / datei
        if not pfad.exists():
            continue
        doc = fitz.open(pfad)
        for p in doc:
            if p.rotation:
                p.remove_rotation()
        M.LAUFEND = M.laufende_zeilen(doc)
        for nr in sorted(nach_datei[datei]):
            if nr > doc.page_count:
                continue
            page = doc[nr - 1]
            n += 1
            if not any(M.RANDLABEL.match(zeile.strip())
                       for zeile in page.get_text("text").splitlines()):
                gleich += 1
                continue
            try:
                M.ist_ueberschrift = alt_ueberschrift
                a = seite_bauen(page)
                M.ist_ueberschrift = echt
                b = seite_bauen(page)
            except Exception as e:
                M.ist_ueberschrift = echt
                print(f"  FEHLER {datei} S.{nr}: {e}")
                continue
            if a == b:
                gleich += 1
                continue
            zm = sum((buchstaben(a) - buchstaben(b)).values())
            (verlust if zm else geaendert).append((datei, nr, zm, a, b))
        doc.close()

    print(f"\n{n} vektorielle Seiten geprueft")
    print(f"  ohne Randmarke / unveraendert : {gleich}")
    print(f"  Absaetze zusammengezogen      : {len(geaendert)}")
    print(f"  Zeichen VERLOREN              : {len(verlust)}")
    for datei, nr, zm, _, _ in verlust[:10]:
        print(f"  VERLUST {Path(datei).name[:40]:40s} S.{nr:3d}  -{zm} Zeichen")
    print("\nZusammenziehungen:")
    for datei, nr, _, a, b in geaendert[:12]:
        unterschied = [(x, y) for x, y in zip(a, b) if x != y]
        print(f"  {Path(datei).name[:38]:38s} S.{nr:3d}")
        if unterschied:
            print(f"      vorher : {unterschied[0][0][:78]}")
            print(f"      nachher: {unterschied[0][1][:78]}")


if __name__ == "__main__":
    main()
