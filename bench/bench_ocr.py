#!/usr/bin/env python3
"""Wie gut ist der OCR-Pfad? Gemessen an Seiten, deren Wahrheit bekannt ist.

  source .venv-mlxocr/bin/activate && python .ocr-bench/bench_ocr.py [--seiten 40]

Idee: vektorielle Seiten tragen ihren Text exakt im PDF. Dieselbe Seite laeuft
zweimal durch dieselbe Pipeline — einmal ueber den Textlayer (= Wahrheit),
einmal mit --nur-ocr erzwungen durchs Modell. Die Differenz ist der Fehler des
OCR-Pfades, und zwar einschliesslich Layout, Kachelung und Zusammenbau; ein
Vergleich gegen rohen PDF-Text wuerde das ausblenden.

Drei Zahlen, weil sie verschiedene Entscheidungen tragen:

  Wortgenauigkeit  Multimengen-Vergleich der Woerter, reihenfolgeunabhaengig.
                   Das ist der Anteil, den ein Reparaturlauf ueberhaupt
                   angehen koennte.
  Reihenfolge      Sequenzaehnlichkeit derselben Woerter. Faellt sie ab,
                   liegt es an Spalten und Lesereihenfolge — dagegen hilft
                   kein Sprachmodell, das muss stromaufwaerts repariert werden.
  Zitattreue       Anteil der Normzitate der Wahrheit, die unveraendert
                   wiederkommen. Die Zahl ist die Messlatte fuer jeden
                   spaeteren Reparaturlauf: er darf sie nicht senken.
"""
import argparse, difflib, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

from pfade import BENCH, PDF2MD_PY, WURZEL as VAULT

# Normzitat: "§ 823 I BGB", "§§ 946, 947 II BGB", "Art. 12 GG". Bewusst grob —
# gemessen wird die Uebereinstimmung, nicht die Grammatik des Zitats.
_NORM = (r"\d+[a-z]?(?:\s*[IVXlivx]+)?"
         r"(?:\s*(?:Abs|S|Satz|Alt|Nr|HS|Hs|Var|lit)\.?\s*\d+[a-z]?)*")
ZITAT = re.compile(r"(?:§{1,2}|Art\.)\s*" + _NORM
                   + r"(?:\s*,\s*" + _NORM + r")*"       # "§§ 946, 947 II BGB"
                   # Gesetzesname nur, wenn er wie einer aussieht: mindestens
                   # zwei Grossbuchstaben (BGB, StGB, VwGO, IFG, SPolG). Sonst
                   # zieht das Muster das naechste beliebige Wort mit hinein
                   # ("§ 12 Eser", "§ 123 I Der") und jede Abweichung dort
                   # zaehlt faelschlich als verlorenes Zitat.
                   + r"(?:\s+(?=[A-ZÄÖÜ\w]*[A-ZÄÖÜ][A-ZÄÖÜ\w]*[A-ZÄÖÜ])"
                     r"[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,8})?")


def nur_text(md):
    """Markdown-Auszeichnung heraus, Inhalt behalten."""
    md = re.sub(r"%%.*?%%", " ", md, flags=re.S)          # Seitenmarken
    md = re.sub(r"!\[\[.*?\]\]", " ", md)                 # Bildeinbettungen
    md = re.sub(r"^\s*>\s?", "", md, flags=re.M)          # Callout-Praefix
    md = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)        # Ueberschriftgrade
    md = re.sub(r"\[\^(\d+)\]:?", r" \1 ", md)            # Fussnoten
    md = md.replace("**", " ").replace("|", " ")
    return re.sub(r"\s+", " ", md).strip()


def woerter(s):
    return re.findall(r"[\wÄÖÜäöüß§€%]+", s)


def seiten_trennen(pfad):
    """{Seitennummer: Text} aus einer pdf2md-Ausgabe.

    Markersyntax aus docs/ocr-vorschau.md: `%% S. N | herkunft, modus %%` —
    die Vorgaenger-Variante ohne `| herkunft` wird auch noch verstanden.
    """
    roh = Path(pfad).read_text(encoding="utf-8")
    teile = re.split(r"%% S\. (\d+)(?: \|[^\n]*)?%%\n*", roh)
    return {int(teile[i]): teile[i + 1] for i in range(1, len(teile) - 1, 2)}


def stichprobe(n):
    """Je Datei hoechstens eine Seite, mittlere Textdichte, kein Diagramm."""
    pfad = BENCH / "pages.json" if (BENCH / "pages.json").exists() else VAULT / "pages.json"
    seiten = json.loads(pfad.read_text())
    kandidaten = {}
    for s in seiten:
        if s["scanned"] or s.get("diagram") or not 1500 <= s["chars"] <= 6000:
            continue
        kandidaten.setdefault(s["file"], []).append(s)
    aus = []
    for datei in sorted(kandidaten):
        gruppe = sorted(kandidaten[datei], key=lambda s: s["page"])
        aus.append(gruppe[len(gruppe) // 2])       # mittlere Seite der Datei
    schritt = max(1, len(aus) // n)
    return aus[::schritt][:n]


def sammel_pdf(proben, ziel):
    import fitz
    neu = fitz.open()
    herkunft = []
    for p in proben:
        quelle = VAULT / p["file"]
        if not quelle.exists():
            continue
        d = fitz.open(quelle)
        neu.insert_pdf(d, from_page=p["page"] - 1, to_page=p["page"] - 1)
        herkunft.append({"seite": len(herkunft) + 1, "datei": p["file"],
                         "quellseite": p["page"], "chars": p["chars"]})
        d.close()
    neu.save(ziel)
    neu.close()
    return herkunft


def vergleiche(wahr, ocr):
    w_wahr, w_ocr = woerter(nur_text(wahr)), woerter(nur_text(ocr))
    if not w_wahr:
        return None
    gemeinsam = sum((Counter(w_wahr) & Counter(w_ocr)).values())
    reihenfolge = difflib.SequenceMatcher(None, w_wahr, w_ocr,
                                          autojunk=False).ratio()
    z_wahr = [re.sub(r"\s+", " ", z).strip() for z in ZITAT.findall(nur_text(wahr))]
    z_ocr = Counter(re.sub(r"\s+", " ", z).strip()
                    for z in ZITAT.findall(nur_text(ocr)))
    getroffen = sum((Counter(z_wahr) & z_ocr).values())
    return {
        "woerter": len(w_wahr),
        "wortgenauigkeit": gemeinsam / len(w_wahr),
        "reihenfolge": reihenfolge,
        "zitate": len(z_wahr),
        "zitattreue": getroffen / len(z_wahr) if z_wahr else None,
        "fehlende_zitate": sorted((Counter(z_wahr) - z_ocr).elements()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seiten", type=int, default=40)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--nur-messen", action="store_true",
                    help="vorhandene Ausgaben vergleichen, nicht neu rechnen")
    a = ap.parse_args()

    arbeit = BENCH / "bench-lauf"
    arbeit.mkdir(exist_ok=True)
    pdf = arbeit / "bench-seiten.pdf"
    manifest = arbeit / "herkunft.json"

    if not a.nur_messen:
        proben = stichprobe(a.seiten)
        herkunft = sammel_pdf(proben, pdf)
        manifest.write_text(json.dumps(herkunft, ensure_ascii=False, indent=1))
        print(f"{len(herkunft)} Seiten aus {len(set(h['datei'] for h in herkunft))} "
              f"Dateien → {pdf.name}\n")
        for modus, ordner in (([], "wahr"), (["--nur-ocr"], "ocr")):
            ziel = arbeit / ordner
            print(f"— Lauf '{ordner}' …", flush=True)
            subprocess.run([sys.executable, str(PDF2MD_PY), str(pdf),
                            "--out", str(ziel), "--bild-dir", str(ziel / "assets"),
                            "--dpi", str(a.dpi)] + modus,
                           check=True, stdout=subprocess.DEVNULL)

    herkunft = json.loads(manifest.read_text())
    wahr = seiten_trennen(arbeit / "wahr" / f"{pdf.stem}.md")
    ocr = seiten_trennen(arbeit / "ocr" / f"{pdf.stem}.md")

    zeilen, summe = [], Counter()
    for h in herkunft:
        n = h["seite"]
        if n not in wahr or n not in ocr:
            continue
        e = vergleiche(wahr[n], ocr[n])
        if not e:
            continue
        e.update(datei=Path(h["datei"]).name, quellseite=h["quellseite"])
        zeilen.append(e)
        summe["woerter"] += e["woerter"]
        summe["getroffen"] += e["wortgenauigkeit"] * e["woerter"]
        summe["zitate"] += e["zitate"]
        summe["zitat_treffer"] += (e["zitattreue"] or 0) * e["zitate"]

    zeilen.sort(key=lambda z: z["wortgenauigkeit"])
    print(f"\n{len(zeilen)} Seiten, {summe['woerter']} Woerter, "
          f"{summe['zitate']} Normzitate\n")
    print(f"  Wortgenauigkeit gesamt : {summe['getroffen']/summe['woerter']:.1%}")
    print(f"  Zitattreue gesamt      : "
          f"{summe['zitat_treffer']/summe['zitate']:.1%}"
          if summe["zitate"] else "  Zitattreue: keine Zitate")
    med = sorted(z["reihenfolge"] for z in zeilen)[len(zeilen) // 2]
    print(f"  Reihenfolge (Median)   : {med:.1%}")

    print("\nSchwaechste zehn Seiten:")
    print(f"  {'Datei':32s} {'S.':>4s} {'Wort':>6s} {'Reihe':>6s} {'Zitat':>6s}")
    for z in zeilen[:10]:
        zt = f"{z['zitattreue']:.0%}" if z["zitattreue"] is not None else "—"
        print(f"  {z['datei'][:32]:32s} {z['quellseite']:4d} "
              f"{z['wortgenauigkeit']:6.1%} {z['reihenfolge']:6.1%} {zt:>6s}")

    verloren = Counter()
    for z in zeilen:
        verloren.update(z["fehlende_zitate"])
    print(f"\nNormzitate, die der OCR-Pfad nicht wiedergibt: "
          f"{sum(verloren.values())} in {len(verloren)} Formen")
    for z, c in verloren.most_common(12):
        print(f"  {c}×  {z}")

    (arbeit / "ergebnis.json").write_text(
        json.dumps(zeilen, ensure_ascii=False, indent=1))
    print(f"\n→ {arbeit / 'ergebnis.json'}")


if __name__ == "__main__":
    main()
