#!/usr/bin/env python3
"""Hypothesen-Test: kollabiert Pfad B an der Textdichte, nicht am Layout?

Seite 02 (7.040 Zeichen, Wiederholungsschleife) wird in linke und rechte
Spaltenhaelfte geschnitten — je ~3.500 Zeichen, also in den Bereich, in dem
Seite 04 (2.707 Z.) sauber lief. Haelt die Hypothese, ist die bestehende
column_tools.py-Infrastruktur die Loesung fuer das Dichte-Limit.

  source .venv-mlxocr/bin/activate && python .ocr-bench/tile_test.py
"""
import time
from pathlib import Path

import sys

BENCH = Path(__file__).resolve().parent
PAGE = sys.argv[1] if len(sys.argv) > 1 else "02-zweispalter-dicht"
SRC = BENCH / f"{PAGE}.png"
PROMPT = "Parse this document page to Markdown."
# Leichter Ueberlappungsrand, damit kein Wort auf der Schnittkante verlorengeht
OVERLAP = 0.02


def make_tiles():
    from PIL import Image
    im = Image.open(SRC)
    w, h = im.size
    mid = w // 2
    ov = int(w * OVERLAP)
    short = PAGE.split("-")[0]
    tiles = {
        f"{short}-tile-links": im.crop((0, 0, mid + ov, h)),
        f"{short}-tile-rechts": im.crop((mid - ov, 0, w, h)),
    }
    for name, img in tiles.items():
        p = BENCH / f"{name}.png"
        img.save(p)
        print(f"   {name}.png  {img.size[0]}x{img.size[1]}")
    return list(tiles)


def main():
    print(f"Schneide {PAGE} in Spaltenhaelften ...")
    names = make_tiles()

    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    mid = "mlx-community/PaddleOCR-VL-1.5-4bit"
    model, processor = load(mid)
    config = load_config(mid)
    print("Modell geladen.\n")

    out = BENCH / "out-B"
    for name in names:
        img = BENCH / f"{name}.png"
        formatted = apply_chat_template(processor, config, PROMPT, num_images=1)
        t = time.perf_counter()
        res = generate(model, processor, formatted, image=[str(img)],
                       max_tokens=8192, temperature=0.0, verbose=False)
        dt = time.perf_counter() - t
        text = res if isinstance(res, str) else getattr(res, "text", str(res))
        (out / f"{name}.md").write_text(text, encoding="utf-8")

        # Degenerations-Detektor: wiederholt sich eine Zeile auffaellig oft?
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        worst = 0
        if lines:
            from collections import Counter
            worst = Counter(lines).most_common(1)[0][1]
        flag = "  ⚠ WIEDERHOLUNGSSCHLEIFE" if worst > 5 else "  ✓ keine Schleife"
        print(f"→ {name}: {dt:.1f} s | {len(text.strip())} Z. | "
              f"haeufigste Zeile {worst}x{flag}")


if __name__ == "__main__":
    main()
