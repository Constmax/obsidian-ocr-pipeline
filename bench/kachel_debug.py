#!/usr/bin/env python3
"""Eine Seite kachelweise aufschluesseln: Tinte, Erwartung, Budget, Ausbeute.

  source .venv-mlxocr/bin/activate && python .ocr-bench/kachel_debug.py 35

Warum es das braucht: im 40-Seiten-Lauf verliert `ZR_LH_30_01` S. 9 ein Drittel
des Textes, ohne dass `entgleist()` anschlaegt. Auf Seitenebene ist das nicht
aufzuloesen — die Erwartung wird je Kachel gebildet, und ob der Fehler in der
Erwartung, im Budget oder erst beim Zusammenfuegen entsteht, sieht man nur, wenn
man beides nebeneinander legt.

Argument ist die Seitennummer im Sammel-PDF `bench-lauf/bench-seiten.pdf`.
"""
import sys

from pfade import BENCH                       # legt pdf2md/ auf sys.path
import pdf2md as M
import ocr as O


def main():
    nr = int(sys.argv[1]) if len(sys.argv) > 1 else 35
    pdf = BENCH / "bench-lauf" / "bench-seiten.pdf"
    arbeit = BENCH / "kachel-debug"
    arbeit.mkdir(exist_ok=True)

    import fitz
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    doc = fitz.open(pdf)
    seite = doc[nr - 1]
    if seite.rotation:
        seite.remove_rotation()
    chars = len(seite.get_text("text"))
    png = arbeit / f"s{nr:03d}.png"
    seite.get_pixmap(dpi=150).save(png)
    doc.close()

    model, processor = load(M.MODEL)
    formatted = apply_chat_template(processor, load_config(M.MODEL), M.PROMPT,
                                    num_images=1)

    def ocr(img, max_tokens=O.TOKEN_MAX):
        res = generate(model, processor, formatted, image=[str(img)],
                       max_tokens=max_tokens, temperature=0.0, verbose=False)
        return res if isinstance(res, str) else getattr(res, "text", str(res))

    tinte_seite = O._tintenmenge(png, 150)
    geeicht = chars >= 400 and tinte_seite > 0
    faktor = chars / tinte_seite if geeicht else O.ZEICHEN_JE_TINTE
    print(f"Seite {nr}: {chars} Zeichen Textlayer, Tinte {tinte_seite:.0f}, "
          f"Faktor {faktor:.5f} ({'geeicht' if geeicht else 'grob'})\n")

    kacheln = O.kacheln_waagerecht(png, 2)
    gesamt = 0
    for i, (teil, oben, unten) in enumerate(kacheln, start=1):
        tinte = O._tintenmenge(teil, 150)
        erwartet = tinte * faktor
        budget = O._tokenbudget(erwartet)
        roh = ocr(teil, budget)
        grund, kennzahl = O.entgleist(roh, erwartet, geeicht)
        gesamt += len(roh)
        print(f"Kachel {i} (y {oben:.2f}–{unten:.2f}): Tinte {tinte:.0f}, "
              f"erwartet {erwartet:.0f} Z., Budget {budget} Token")
        print(f"   geliefert {len(roh)} Z.  → Quotient "
              f"{len(roh)/max(erwartet,1):.2f}  → "
              f"{grund or 'in Ordnung'} {kennzahl:.2f}")
        print(f"   Anfang : {roh[:90]!r}")
        print(f"   Ende   : {roh[-90:]!r}\n")
    print(f"Summe roh {gesamt} Z. gegen {chars} Z. Textlayer "
          f"({gesamt/max(chars,1):.0%}) — die Ueberlappung ist hier doppelt "
          f"gezaehlt.")


if __name__ == "__main__":
    main()
