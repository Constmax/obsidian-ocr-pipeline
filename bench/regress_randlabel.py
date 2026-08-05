#!/usr/bin/env python3
"""Regression der Randlabel-Regel: mit gegen ohne randlabel_vorziehen().

  source .venv-mlxocr/bin/activate && python .ocr-bench/regress_randlabel.py

Dieselbe Anlage wie regress_steg.py, aber mit Vorfilter. Der Unterschied ist
der Punkt: die Regel kann sich nur auf Seiten auswirken, auf denen ueberhaupt
eine alleinstehende Randmarke steht — im ganzen Bestand sind das gut drei
Dutzend Zeilen. Alle 1426 Seiten zweimal komplett aufzubauen hiesse, 24 Minuten
in PyMuPDFs Tabellenerkennung zu stecken (~0,5 s je Seite und Aufbau), die mit
der Frage nichts zu tun hat.

Also erst billig scannen (nur textlayer_zeilen, keine Tabellensuche), dann nur
die Treffer teuer nachrechnen.

Erwartet wird, dass keine Seite einen einzigen Buchstaben verliert — die Regel
verschiebt eine Zeile, sie loescht nichts. Zeigt der Lauf Zeichenverlust, ist
die Regel falsch und nicht der Bestand.
"""
import json, sys
from pathlib import Path

from pfade import BENCH, WURZEL as VAULT   # legt pdf2md.py auf sys.path
import pdf2md as M
from regress_steg import buchstaben, seite_bauen, woerter


def main():
    import fitz
    seiten = [s for s in json.loads((BENCH / "pages.json").read_text())
              if not s["scanned"]]
    nach_datei = {}
    for s in seiten:
        nach_datei.setdefault(s["file"], []).append(s["page"])

    echt = M.randlabel_vorziehen
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
            # Vorfilter: ohne alleinstehende Randmarke kann die Regel nichts
            # tun. Bewusst direkt auf get_text() und nicht ueber
            # textlayer_zeilen() — das ruft selbst schon die Tabellensuche auf,
            # und genau die soll hier gespart werden.
            if not any(M.RANDLABEL.match(zeile.strip())
                       for zeile in page.get_text("text").splitlines()):
                gleich += 1
                continue
            try:
                M.randlabel_vorziehen = lambda z, *r, **k: z
                a = seite_bauen(page)
                M.randlabel_vorziehen = echt
                b = seite_bauen(page)
            except Exception as e:
                M.randlabel_vorziehen = echt
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
    print(f"  Marke verschoben : {len(geaendert)}")
    print(f"  Zeichen VERLOREN : {len(verlust)}")
    for datei, nr, zm, a, b in verlust[:10]:
        print(f"  VERLUST {Path(datei).name[:40]:40s} S.{nr:3d}  -{zm} Zeichen")
    print("\nVerschiebungen:")
    for datei, nr, _, a, b in geaendert[:12]:
        unterschied = [(x, y) for x, y in zip(a, b) if x != y]
        vorher = unterschied[0][0][:70] if unterschied else ""
        nachher = unterschied[0][1][:70] if unterschied else ""
        print(f"  {Path(datei).name[:38]:38s} S.{nr:3d}")
        print(f"      vorher : {vorher}")
        print(f"      nachher: {nachher}")


if __name__ == "__main__":
    main()
