#!/usr/bin/env python3
"""Wo gehen die 33 s/Seite hin? Prefill, Decode oder Python-Overhead?

  source .venv-mlxocr/bin/activate && python .ocr-bench/speed_test.py <pdf> <seite>

Misst pro Inferenzaufruf getrennt: Bildtokens (prompt_tokens), Prefill-Zeit,
Decode-Zeit, Ausgabezeichen. Dazu den Nicht-Inferenz-Anteil (Rendern, Kacheln,
Fetterkennung) — der taucht in der bisherigen Messung gar nicht auf.
"""
import sys, time, json
from pathlib import Path
import fitz
from PIL import Image

OUT = Path(__file__).parent / "speed"
OUT.mkdir(exist_ok=True)
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PROMPT = "Parse this document page to Markdown."
OVERLAP = 0.02


def render(page, dpi, tag):
    t = time.perf_counter()
    png = OUT / f"{tag}_{dpi}.png"
    page.get_pixmap(dpi=dpi).save(png)
    return png, time.perf_counter() - t


def senkrecht(png, steg=0.5):
    im = Image.open(png)
    w, h = im.size
    cut, ov = int(w * steg), int(w * OVERLAP)
    a, b = png.with_name(png.stem + "_L.png"), png.with_name(png.stem + "_R.png")
    im.crop((0, 0, min(cut + ov, w), h)).save(a)
    im.crop((max(cut - ov, 0), 0, w, h)).save(b)
    return [a, b]


def main():
    pdf, nr = Path(sys.argv[1]), int(sys.argv[2])
    doc = fitz.open(pdf)
    page = doc[nr - 1]
    basis = len(page.get_text("text").strip())
    print(f"{pdf.name} S.{nr} — Textlayer {basis} Zeichen\n")

    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    t = time.perf_counter()
    model, processor = load(MODEL)
    config = load_config(MODEL)
    print(f"Modell geladen in {time.perf_counter()-t:.1f} s")
    formatted = apply_chat_template(processor, config, PROMPT, num_images=1)

    def messe(img):
        px = Image.open(img).size
        t = time.perf_counter()
        r = generate(model, processor, formatted, image=[str(img)],
                     max_tokens=8192, temperature=0.0, verbose=False)
        wand = time.perf_counter() - t
        txt = r.text if hasattr(r, "text") else str(r)
        pre = r.prompt_tokens / r.prompt_tps if r.prompt_tps else 0
        dec = r.generation_tokens / r.generation_tps if r.generation_tps else 0
        return dict(px=f"{px[0]}x{px[1]}", mp=round(px[0]*px[1]/1e6, 2),
                    ptok=r.prompt_tokens, gtok=r.generation_tokens,
                    ptps=round(r.prompt_tps, 1), gtps=round(r.generation_tps, 1),
                    prefill=round(pre, 1), decode=round(dec, 1),
                    wand=round(wand, 1), chars=len(txt),
                    loc=txt.count("<|LOC_"), rss=round(r.peak_memory, 0))

    zeilen = []
    for dpi in (300, 200, 150, 110):
        png, t_render = render(page, dpi, f"s{nr}")
        t = time.perf_counter()
        teile = senkrecht(png)
        t_kachel = time.perf_counter() - t
        res = [messe(p) for p in teile]
        summe = dict(dpi=dpi, modus="2 Kacheln",
                     render=round(t_render, 1), kachel=round(t_kachel, 1),
                     prefill=round(sum(r["prefill"] for r in res), 1),
                     decode=round(sum(r["decode"] for r in res), 1),
                     wand=round(sum(r["wand"] for r in res), 1),
                     ptok=sum(r["ptok"] for r in res),
                     gtok=sum(r["gtok"] for r in res),
                     chars=sum(r["chars"] for r in res),
                     loc=sum(r["loc"] for r in res),
                     px=res[0]["px"], mp=res[0]["mp"], rss=max(r["rss"] for r in res))
        zeilen.append(summe)
        print(f"  dpi {dpi:3d} 2-Kachel: Kachel {summe['px']} = {summe['mp']} MP | "
              f"{summe['ptok']:5d} Bildtok | prefill {summe['prefill']:5.1f} s | "
              f"decode {summe['decode']:5.1f} s | {summe['gtok']:5d} gtok | "
              f"{summe['chars']:5d} Z | render {summe['render']:.1f} s")

    # Ganze Seite ohne Kachelung, zum Vergleich der effektiven Auflösung
    for dpi in (300, 150):
        png, t_render = render(page, dpi, f"s{nr}g")
        r = messe(png)
        r.update(dpi=dpi, modus="ganz", render=round(t_render, 1), kachel=0.0)
        zeilen.append(r)
        print(f"  dpi {dpi:3d} ganz     : Bild {r['px']} = {r['mp']} MP | "
              f"{r['ptok']:5d} Bildtok | prefill {r['prefill']:5.1f} s | "
              f"decode {r['decode']:5.1f} s | {r['gtok']:5d} gtok | "
              f"{r['chars']:5d} Z | render {r['render']:.1f} s")

    (OUT / f"speed-s{nr}.json").write_text(json.dumps(zeilen, indent=1))
    doc.close()


if __name__ == "__main__":
    main()
