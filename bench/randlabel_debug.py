#!/usr/bin/env python3
"""Zeigt die Zeilengeometrie um eine Randmarke — warum zieht die Regel nicht?

  source .venv-mlxocr/bin/activate && python .ocr-bench/randlabel_debug.py \
      "raw/StR/Strafrecht-AT/Strafrecht AT VI - Fahrlaessigkeit.pdf" 3

Gedruckt wird je Zeile x0, y0 und der Anfang des Textes, dazu der Rumpfeinzug,
den `randlabel_vorziehen` als Median der Nachbarschaft bildet. Damit ist auf
einen Blick zu sehen, ob die Marke geometrisch ueberhaupt im Rand steht.
"""
import sys
from pathlib import Path

from pfade import BENCH                       # legt pdf2md.py auf sys.path
import pdf2md as M


def main():
    pdf = Path(sys.argv[1])
    nr = int(sys.argv[2])
    arbeit = BENCH / "randlabel-debug"
    arbeit.mkdir(exist_ok=True)

    import fitz
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    doc = fitz.open(pdf)
    seite = doc[nr - 1]
    if seite.rotation:
        seite.remove_rotation()
    png = arbeit / f"s{nr:03d}.png"
    seite.get_pixmap(dpi=150).save(png)
    doc.close()

    model, processor = load(M.MODEL)
    formatted = apply_chat_template(processor, load_config(M.MODEL), M.PROMPT,
                                    num_images=1)
    roh = generate(model, processor, formatted, image=[str(png)],
                   max_tokens=M.TOKEN_MAX, temperature=0.0, verbose=False)
    roh = roh if isinstance(roh, str) else getattr(roh, "text", str(roh))

    zeilen = M.fett_markieren(M.parse_zeilen(roh), png)
    print(f"{len(zeilen)} Zeilen\n")
    print(f"{'#':>3s} {'x0':>5s} {'y0':>5s} {'x1':>5s}  Text")
    for i, z in enumerate(zeilen):
        b = z[1]
        marke = " ←RANDLABEL" if M.RANDLABEL.match(z[0].strip()) else ""
        print(f"{i:3d} {b[0] if b else -1:5d} {b[1] if b else -1:5d} "
              f"{b[2] if b else -1:5d}  {z[0][:70]!r}{marke}")

    import statistics
    for i, z in enumerate(zeilen):
        if not (z[1] and M.RANDLABEL.match(z[0].strip())):
            continue
        nah = [x for x in zeilen[max(0, i - 8):i + 9] if x[1] and x is not z]
        rumpf = statistics.median([x[1][0] for x in nah]) if nah else None
        print(f"\nMarke bei #{i}: x0={z[1][0]}, Rumpfmedian={rumpf}, "
              f"Schwelle={None if rumpf is None else rumpf - 25}")
        print("   → im Rand" if rumpf is not None and z[1][0] <= rumpf - 25
              else "   → NICHT im Rand, Regel greift nicht")


if __name__ == "__main__":
    main()
