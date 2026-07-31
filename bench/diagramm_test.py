#!/usr/bin/env python3
"""Kästen und Diagrammseiten erkennen — ohne Inferenz.

  source .venv-mlxocr/bin/activate && python .ocr-bench/diagramm_test.py [pdf ...]
  source .venv-mlxocr/bin/activate && python .ocr-bench/diagramm_test.py --bestand

Der Unterschied, auf den alles ankommt:

  gestapelte Kästen  → Kasten = eigener Absatz, Text bleibt lesbar
  Flussdiagramm      → Leserichtung ist NICHT linear, Text linearisieren zerstört
                       die Aussage. Hier gehört ein Bild der Seite hin.

Unterscheidungsmerkmal ist die STREUUNG DER KASTENBREITEN — siehe ist_diagramm().
"""
import argparse, json, random
from pathlib import Path
import fitz
import numpy as np

VAULT = Path(__file__).resolve().parent.parent


# --- Kästen ---------------------------------------------------------------

def _entdoppeln(kaesten, nah=6):
    """Ein Rahmen wird oft doppelt gezeichnet (Füllung + Strich) oder aus vier
    Linien. Nahezu deckungsgleiche Rechtecke zu einem zusammenfassen."""
    aus = []
    for k in sorted(kaesten, key=lambda r: -(r[2] - r[0]) * (r[3] - r[1])):
        if not any(all(abs(k[i] - v[i]) <= nah for i in range(4)) for v in aus):
            aus.append(k)
    return aus


def kaesten_vektor(page, min_b=25, min_h=14):
    aus = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width >= min_b and r.height >= min_h and r.width * r.height > 0:
            aus.append((r.x0, r.y0, r.x1, r.y1))
    return _entdoppeln(aus)


def _laengster_lauf(maske):
    lauf = np.zeros(maske.shape[0], dtype=np.int32)
    akt = np.zeros(maske.shape[0], dtype=np.int32)
    for j in range(maske.shape[1]):
        akt = np.where(maske[:, j], akt + 1, 0)
        np.maximum(lauf, akt, out=lauf)
    return lauf


def kaesten_raster(page, dpi=110, mindest=0.12, dicke=4):
    """Kästen aus lückenlosen Geraden im Bild. `mindest` ist bewusst kleiner als
    bei der Tabellensuche: ein Kasten muss nicht die halbe Seite überspannen."""
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    a = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    dunkel = a < 170
    H, W = dunkel.shape

    def sammeln(lauf, laenge, rand):
        tr = np.where(lauf >= laenge * mindest)[0]
        tr = tr[(tr > rand) & (tr < len(lauf) - rand)]
        aus, block = [], []
        for i in tr:
            if block and i - block[-1] > dicke:
                aus.append(sum(block) / len(block)); block = []
            block.append(i)
        if block:
            aus.append(sum(block) / len(block))
        return aus

    ys = sammeln(_laengster_lauf(dunkel), W, int(H * 0.02))
    xs = sammeln(_laengster_lauf(dunkel.T), H, int(W * 0.04))
    # Rechtecke aus benachbarten Linienpaaren; Seitenmaße auf PDF-Punkte zurück
    sx, sy = page.rect.width / W, page.rect.height / H
    aus = []
    for y0, y1 in zip(ys, ys[1:]):
        if (y1 - y0) < H * 0.02:
            continue
        for x0, x1 in zip(xs, xs[1:]):
            if (x1 - x0) < W * 0.08:
                continue
            aus.append((x0 * sx, y0 * sy, x1 * sx, y1 * sy))
    return _entdoppeln(aus)


# --- Bewertung ------------------------------------------------------------

def textzeilen(page):
    aus = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 0:
            for ln in b["lines"]:
                if "".join(s["text"] for s in ln["spans"]).strip():
                    aus.append(ln["bbox"])
    return aus


def mit_text(kaesten, zeilen):
    aus = []
    for k in kaesten:
        n = sum(1 for z in zeilen
                if k[0] <= (z[0] + z[2]) / 2 <= k[2]
                and k[1] <= (z[1] + z[3]) / 2 <= k[3])
        if n:
            aus.append((k, n))
    return aus


def _cluster(werte, tol):
    werte = sorted(werte)
    n = 1
    for a, b in zip(werte, werte[1:]):
        if b - a > tol:
            n += 1
    return n


def ist_diagramm(kaesten, W):
    """Entscheidet allein über die Streuung der Kastenbreiten.

    Ein umrandeter Kasten füllt die Textspalte — alle Kästen einer Seite sind
    darum praktisch gleich breit (Streuung 0,000–0,028 auf sieben geprüften
    Seiten). Ein Diagrammkasten ist auf seinen Inhalt zugeschnitten, die Breiten
    streuen (0,15–0,22 auf drei geprüften Seiten).

    Bewusst OHNE Spaltensteg und Nachbarschaftsprüfung: die Stegerkennung ist
    auf Prosa abgestimmt und lag auf beiden Seiten falsch — sie hielt einen
    Zweispalter mit gestapelten Kästen für ein Diagramm und ein einspaltiges
    Diagramm für einen Zweispalter.
    """
    if len(kaesten) < 3:
        return False
    breiten = [(k[2] - k[0]) / W for k in kaesten]
    if _cluster(breiten, 0.05) < 3:
        return False
    mittel = sum(breiten) / len(breiten)
    streu = (sum((b - mittel) ** 2 for b in breiten) / len(breiten)) ** 0.5
    return streu >= 0.05


def bewerte(page, scan=None):
    if scan is None:
        scan = sum(abs((b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]))
                   for b in page.get_text("dict")["blocks"] if b.get("type") == 1) \
               >= 0.5 * abs(page.rect.width * page.rect.height)
    rohe = kaesten_raster(page) if scan else kaesten_vektor(page)
    zeilen = textzeilen(page)
    ks = mit_text(rohe, zeilen)
    # Ganzseitenrahmen streichen: der ist kein Kasten, sondern der Seitenrand
    fl = abs(page.rect.width * page.rect.height) or 1
    ks = [(k, n) for k, n in ks
          if (k[2] - k[0]) * (k[3] - k[1]) < 0.75 * fl]
    boxen = [k for k, _ in ks]
    W = page.rect.width or 1
    breiten = sorted(round((k[2] - k[0]) / W, 2) for k in boxen)
    return dict(kaesten=len(ks), scan=scan, breiten=breiten,
                diagramm=ist_diagramm(boxen, W), zeilen=len(zeilen),
                in_kaesten=sum(n for _, n in ks))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*")
    ap.add_argument("--bestand", action="store_true")
    ap.add_argument("--stichprobe", type=int, default=0)
    a = ap.parse_args()

    if a.bestand or a.stichprobe:
        seiten = json.loads((VAULT / "pages.json").read_text())
        if a.stichprobe:
            random.seed(7); seiten = random.sample(seiten, a.stichprobe)
        n_d, n_k, akt, doc = 0, 0, None, None
        treffer = []
        for s in sorted(seiten, key=lambda x: (x["file"], x["page"])):
            if s["file"] != akt:
                if doc:
                    doc.close()
                try:
                    doc = fitz.open(VAULT / s["file"])
                except Exception:
                    doc = None
                akt = s["file"]
            if not doc:
                continue
            try:
                r = bewerte(doc[s["page"] - 1], scan=s["scanned"])
            except Exception:
                continue
            if r["diagramm"]:
                n_d += 1; treffer.append((s["file"], s["page"], r))
            elif r["kaesten"]:
                n_k += 1
        if doc:
            doc.close()
        print(f"\n{len(seiten)} Seiten: {n_d} Diagrammseiten, "
              f"{n_k} Seiten mit gestapelten Kaesten")
        for f, p, r in treffer[:25]:
            print(f"   {r['kaesten']:2d} Kaesten, {'Scan' if r['scan'] else 'Vekt'}"
                  f"   {f} S.{p}")
        return

    for f in a.pdfs:
        d = fitz.open(f)
        print(f"\n{Path(f).name}")
        for i in range(d.page_count):
            r = bewerte(d[i])
            print(f"  S.{i+1:2d} {'DIAGRAMM' if r['diagramm'] else '        '} "
                  f"{r['kaesten']:2d} Kaesten ({r['in_kaesten']:3d}/{r['zeilen']:3d} Zeilen drin)"
                  f"  Breiten={r['breiten']}")
        d.close()


if __name__ == "__main__":
    main()
