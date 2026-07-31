#!/usr/bin/env python3
"""Spaltenerkennung isoliert testen — ohne Inferenz, Sekundenbruchteile.

  source .venv-mlxocr/bin/activate && python .ocr-bench/detect_test.py <pdf> [...]
"""
import sys
from pathlib import Path
import fitz
import numpy as np


def erkenne(page, dpi=72):
    """('einspaltig'|'zweispaltig', steg_relativ, diagnose)

    Senkrechtes Tintenprofil. Referenz ist der MEDIAN des Profils, nicht das
    Maximum: das Maximum ist bei gescannten Skripten der Ringbindungs-Schatten
    am Blattrand und macht jede relative Schwelle unbrauchbar.
    """
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    ink = (arr < 200).sum(axis=0).astype(float)
    W = pm.width

    innen = ink[int(W * 0.10):int(W * 0.90)]
    ref = float(np.median(innen))
    if ref <= 0:
        return "einspaltig", None, "leere Seite"

    lo, hi = int(W * 0.30), int(W * 0.70)
    schwelle = ref * 0.25
    beste, akt = (0, None), None
    for i in range(lo, hi):
        if ink[i] < schwelle:
            akt = i if akt is None else akt
        else:
            if akt is not None and i - akt > beste[0]:
                beste = (i - akt, (akt + i) // 2)
            akt = None
    if akt is not None and hi - akt > beste[0]:
        beste = (hi - akt, (akt + hi) // 2)

    breite, mitte = beste
    diag = (f"ref(median)={ref:.0f} schwelle={schwelle:.0f} "
            f"Stegbreite={breite/W:.1%}")
    if mitte is not None and breite >= W * 0.010:
        return "zweispaltig", mitte / W, diag
    return "einspaltig", None, diag


def main():
    for arg in sys.argv[1:]:
        p = Path(arg)
        d = fitz.open(p)
        print(f"\n{p.name}  ({d.page_count} Seiten)")
        for i in range(d.page_count):
            art, steg, diag = erkenne(d[i])
            s = f"@{steg:.0%}" if steg else "  — "
            print(f"  S.{i+1:2d}  {art:12s} {s}   {diag}")
        d.close()


if __name__ == "__main__":
    main()
