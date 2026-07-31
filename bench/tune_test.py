#!/usr/bin/env python3
"""Stellschrauben einzeln pruefen — Geschwindigkeit UND Qualitaet.

  source .venv-mlxocr/bin/activate && python .ocr-bench/tune_test.py <pdf> <seite>

Eine Beschleunigung, die Zeichen frisst, ist keine. Darum zu jeder Variante ein
Wortabgleich gegen den vorhandenen Textlayer: Multimengen-Schnitt, damit die
Spaltenreihenfolge das Ergebnis nicht verfaelscht.
"""
import sys, time, json, re
from collections import Counter
from pathlib import Path
import fitz
from PIL import Image

OUT = Path(__file__).parent / "speed"
OUT.mkdir(exist_ok=True)
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PROMPT = "Parse this document page to Markdown."
LOC = re.compile(r"<\|LOC_\d+\|>")


def worte(s):
    s = LOC.sub(" ", s)
    return Counter(w for w in re.findall(r"[A-Za-zÄÖÜäöüß§]{4,}", s.lower()))


def treffer(ist, soll):
    """Anteil der Baseline-Woerter, die in der Ausgabe wiederkommen."""
    ges = sum(soll.values()) or 1
    return sum(min(n, ist[w]) for w, n in soll.items()) / ges


def linke_haelfte(page, dpi):
    """Nur die linke Spalte — eine Kachel reicht fuer den Vergleich."""
    png = OUT / f"t_{dpi}.png"
    page.get_pixmap(dpi=dpi).save(png)
    im = Image.open(png)
    w, h = im.size
    p = png.with_name(f"t_{dpi}_L.png")
    im.crop((0, 0, int(w * 0.52), h)).save(p)
    return p


def main():
    pdf, nr = Path(sys.argv[1]), int(sys.argv[2])
    doc = fitz.open(pdf)
    page = doc[nr - 1]
    soll = worte(page.get_text("text"))
    print(f"{pdf.name} S.{nr} — Baseline {sum(soll.values())} Woerter (>=4 Zeichen)\n")

    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    model, processor = load(MODEL)
    config = load_config(MODEL)
    formatted = apply_chat_template(processor, config, PROMPT, num_images=1)

    def lauf(img, **kw):
        t = time.perf_counter()
        r = generate(model, processor, formatted, image=[str(img)],
                     max_tokens=8192, temperature=0.0, verbose=False, **kw)
        wand = time.perf_counter() - t
        txt = r.text if hasattr(r, "text") else str(r)
        pre = r.prompt_tokens / r.prompt_tps if r.prompt_tps else 0
        return dict(wand=round(wand, 1), prefill=round(pre, 1),
                    decode=round(wand - pre, 1), ptok=r.prompt_tokens,
                    gtok=r.generation_tokens, chars=len(txt),
                    loc=len(LOC.findall(txt)) // 8,
                    quote=round(treffer(worte(txt), soll), 4), txt=txt)

    zeilen = []

    def zeige(name, r, px=""):
        zeilen.append(dict(variante=name, px=px,
                           **{k: v for k, v in r.items() if k != "txt"}))
        print(f"  {name:26s} {px:>10s} | {r['ptok']:5d} ptok | "
              f"pre {r['prefill']:5.1f} | dec {r['decode']:5.1f} | "
              f"= {r['wand']:5.1f} s | {r['chars']:5d} Z | "
              f"{r['loc']:3d} Zeilen | Wortdeckung {r['quote']:.1%}")
        (OUT / f"var-{name.replace(' ','_').replace('/','')}.txt").write_text(r["txt"])

    print("A) Auflösung — wieviel Tempo kostet wieviel Genauigkeit?")
    for dpi in (150, 130, 110, 90, 72):
        img = linke_haelfte(page, dpi)
        px = "x".join(map(str, Image.open(img).size))
        zeige(f"dpi {dpi}", lauf(img), px)

    print("\nB) Laufzeit-Schalter bei unveränderter Auflösung (150 dpi)")
    img = linke_haelfte(page, 150)
    px = "x".join(map(str, Image.open(img).size))
    for name, kw in (("prefill_step 1024", dict(prefill_step_size=1024)),
                     ("prefill_step 2048", dict(prefill_step_size=2048)),
                     ("kv_bits 8", dict(kv_bits=8)),
                     ("kv_bits 4", dict(kv_bits=4))):
        try:
            zeige(name, lauf(img, **kw), px)
        except Exception as e:
            print(f"  {name:26s} — nicht nutzbar: {type(e).__name__}: {e}")

    (OUT / f"tune-s{nr}.json").write_text(json.dumps(zeilen, indent=1))
    doc.close()


if __name__ == "__main__":
    main()
