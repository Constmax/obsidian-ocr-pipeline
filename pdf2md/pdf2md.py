#!/usr/bin/env python3
"""Pfad C, Ende-zu-Ende: PDF → Markdown (CLI + Seitenlauf).

  source .venv-mlxocr/bin/activate && python .ocr-bench/pdf2md.py <pdf> [--dpi 300]

Rendert jede Seite, kachelt bei hoher Textdichte, laesst PaddleOCR-VL laufen und
baut die Zeilen anhand ihrer <|LOC|>-Koordinaten zu Markdown zusammen.

Seit Issue #8 ist diese Datei nur noch CLI und Seitenlauf: die Geometrie
(Spalten, Kaesten, Diagramme) liegt in layout.py, Kachelung und Modell in
ocr.py, der Markdown-Zusammenbau in zusammenbau.py. Der Zusammenbau ist die
testbare Schicht — pdf2md/test laeuft ohne MLX, fitz und Vault-Bestand.

Schreibt nach .ocr-bench/out-C/, fasst raw/ nicht an.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import abbruch
import woerterbuch
import zusammenbau
from zusammenbau import (als_callout, dokument_bauen, entpua, fragmente_verschmelzen,
                        frontmatter_bauen, seitenmarker, zusammenfuegen)
from layout import (bildanteil, kaesten_erkennen, kaesten_zuordnen,
                   layout_erkennen, spalten_trennen, tabellen_markdown)
from ocr import (OVERLAP, TOKEN_MAX, ZEICHEN_JE_TINTE, _tintenmenge,
               kachel_zeilen, kacheln_senkrecht, kacheln_waagerecht,
               ueberlappung_kuerzen)

BENCH = Path(__file__).resolve().parent
OUT = BENCH / "out-C"
# Zwischenbilder je Lauf in einen eigenen Ordner. Zwei gleichzeitig laufende
# pdf2md-Prozesse loeschten sich sonst gegenseitig die Kacheln weg — das
# Aufraeumen am Ende greift per Glob auf das ganze Verzeichnis zu.
TMP = OUT
MODEL = os.environ.get("MLX_OCR_MODEL", "mlx-community/PaddleOCR-VL-1.5-4bit")
PROMPT = "Parse this document page to Markdown."
KACHEL_AB = 3000      # Zeichen im vorhandenen Textlayer


def seiten_parsen(s):
    """'1,3-5,8' in eine Menge von int (1-basiert) umwandeln.

    Leerstring ergibt None (alle Seiten). Ungueltige Eingaben oder
    Seitenzahlen ausserhalb des gueltigen Bereichs fuehren zu einem
    Fehler auf stderr und Exit-Code 1.
    """
    s = s.strip() if s else ""
    if not s:
        return None
    auswahl = set()
    for teil in s.split(","):
        teil = teil.strip()
        if not teil:
            continue
        if "-" in teil:
            lo, hi = teil.split("-", 1)
            try:
                lo, hi = int(lo), int(hi)
            except ValueError:
                sys.exit(f"ungueltige Seitenangabe: {teil!r}")
            if lo < 1 or hi < 1 or lo > hi:
                sys.exit(f"ungueltige Seitenzahl: {teil}")
            auswahl.update(range(lo, hi + 1))
        else:
            try:
                n = int(teil)
            except ValueError:
                sys.exit(f"ungueltige Seitenangabe: {teil!r}")
            if n < 1:
                sys.exit(f"ungueltige Seitenzahl: {n}")
            auswahl.add(n)
    return auswahl


def laufende_zeilen(doc, kopf=0.09, fuss=0.93, min_seiten=2):
    """Texte, die auf mehreren Seiten in der Kopf- oder Fusszone gleich lauten.

    Das ist die allgemeine Form der Boilerplate-Erkennung: eine laufende
    Kopfzeile beweist sich dadurch, dass sie sich wiederholt — nicht dadurch,
    dass sie in einer Stichwortliste steht. Damit fliegt auch
    "Schuldrecht AT – Fall 12 | Begleitskript" raus, das in keiner Hemmer-Liste
    steht und sonst an eine Fussnotendefinition geklebt wird.
    """
    from collections import Counter
    zaehler = Counter()
    for i in range(doc.page_count):
        p = doc[i]
        H = p.rect.height or 1
        gesehen = set()
        for b in p.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                rel = ((ln["bbox"][1] + ln["bbox"][3]) / 2) / H
                if not (rel <= kopf or rel >= fuss):
                    continue
                t = re.sub(r"\s+", " ",
                           "".join(s["text"] for s in ln["spans"])).strip()
                # Reine Seitenzahlen sind pro Seite verschieden und werden
                # ohnehin von ist_boilerplate() gefasst.
                if len(t) < 6 or re.fullmatch(r"[\d\s\-–—.]+", t):
                    continue
                gesehen.add(t)
        zaehler.update(gesehen)
    return {t for t, n in zaehler.items() if n >= min_seiten}


def textlayer_zeilen(page):
    """Wie parse_zeilen, aber aus dem vorhandenen Textlayer.

    Fuer vektorielle PDFs ist das dem OCR in jeder Hinsicht ueberlegen: der Text
    ist exakt statt erkannt, die Koordinaten kommen aus dem Satz statt aus einer
    Schaetzung, und **fett** steht als Font-Flag drin — das Modell liefert
    ueberhaupt keine Formatierung. Kostet keine Inferenz.
    """
    W = page.rect.width or 1
    H = page.rect.height or 1
    tabellen = tabellen_markdown(page)
    rahmen = [t[2] for t in tabellen]

    def in_tabelle(bbox):
        mx, my = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        return any(x0 <= mx <= x1 and y0 <= my <= y1
                   for x0, y0, x1, y1 in rahmen)

    zeilen, prosa, quer = [], [], []
    for y, md, bbox in tabellen:
        # Die Tabelle wandert als EIN Block durch die Absatzlogik. Drittes
        # Element markiert sie als unantastbar: saeubern() wuerde die Pipes in
        # roemische I verwandeln ("§ 275 BGB | ja" → "§ 275 BGB I ja").
        zeilen.append([md, (int(bbox[0] / W * 1000), int(bbox[1] / H * 1000),
                            int(bbox[2] / W * 1000), int(bbox[3] / H * 1000)),
                       "tabelle"])
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if in_tabelle(ln["bbox"]):
                continue                 # steckt schon in der Markdown-Tabelle
            teile = []
            for sp in ln["spans"]:
                t = entpua(sp["text"])
                if not t.strip():
                    teile.append(t)
                    continue
                # Word setzt die zweite Aufzaehlungsebene als Courier-"o". Als
                # Buchstabe gelesen landet es mitten im Fliesstext ("keine
                # ueberzogenen o Anforderungen") — es ist ein Listenpunkt.
                if t.strip() in ("o", "O") and "courier" in sp.get("font", "").lower():
                    teile.append("-")
                    continue
                fett = bool(sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower()
                # Randleerzeichen ausserhalb der Marker halten, sonst frisst
                # Obsidian die Auszeichnung ("** wort **" rendert nicht).
                vor = t[:len(t) - len(t.lstrip())]
                nach = t[len(t.rstrip()):]
                teile.append(f"{vor}**{t.strip()}**{nach}" if fett else t)
            text = "".join(teile).strip()
            if not text:
                continue
            # Gedrehter Satz (Querseiten im Klausur-Zusatzmaterial): dort ist
            # "dieselbe Grundlinie" eine x-Beziehung, die Reihenbildung nach y
            # wuerfe die ganze Seite in eine Reihe. Solche Zeilen bleiben roh.
            (prosa if tuple(ln.get("dir", (1, 0))) == (1, 0) else quer) \
                .append([text, tuple(ln["bbox"])])
    # Erst verschmelzen, dann normieren: die Luecken-Schwelle rechnet in
    # PDF-Punkten, und Tabellen bleiben aussen vor.
    for text, box in fragmente_verschmelzen(prosa, W) + quer:
        zeilen.append([text, (int(box[0] / W * 1000), int(box[1] / H * 1000),
                              int(box[2] / W * 1000), int(box[3] / H * 1000))])
    return zeilen


def seiten_analysieren(pdf, dpi, nur_ocr=False, auswahl=None):
    """Je Seite entscheiden: Textlayer genuegt, oder muss das Modell ran?

    Nur Seiten, die wirklich OCR brauchen, werden gerendert — das Rendern und
    Kacheln einer Seite, die man gar nicht durchs Modell schickt, ist verlorene
    Zeit.
    """
    import fitz
    doc = fitz.open(pdf)
    # /Rotate 90 oder 270: page.rect zeigt die gedrehte Ansicht, get_text() und
    # get_drawings() liefern aber ungedrehte Koordinaten — Zeilen laufen dann
    # senkrecht (dir=(0,1)) und jede Annahme dieser Pipeline bricht. Einmal
    # geradeziehen bringt Text, Zeichnungen, Tabellen und Rendering in dasselbe
    # System. Betrifft nur das Objekt im Speicher, die Datei bleibt unberuehrt.
    for p in doc:
        if p.rotation:
            p.remove_rotation()
    if auswahl:
        ungültig = [n for n in auswahl if n < 1 or n > doc.page_count]
        if ungültig:
            sys.exit(f"Seitenzahlen {sorted(ungültig)} existieren nicht "
                     f"(PDF hat {doc.page_count} Seiten)")
        auswahl = {n for n in auswahl if 1 <= n <= doc.page_count}
    zusammenbau.laufend_setzen(laufende_zeilen(doc))
    seiten = []
    for i in range(doc.page_count):
        if auswahl is not None and (i + 1) not in auswahl:
            continue
        p = doc[i]
        chars = len(p.get_text("text").strip())
        scan = bildanteil(p) >= 0.5 or chars < 100
        tab_rahmen = [] if scan else [t[2] for t in tabellen_markdown(p)]
        kaesten, diagramm = kaesten_erkennen(p, scan, tab_rahmen)
        if not scan and not nur_ocr:
            seiten.append((i + 1, None, chars, "vektoriell", None,
                           textlayer_zeilen(p), kaesten, diagramm))
            continue
        png = TMP / f"_seite{i+1:03d}.png"
        p.get_pixmap(dpi=dpi).save(png)
        art, steg = layout_erkennen(p)
        seiten.append((i + 1, png, chars, art, steg, None, kaesten, diagramm))
    doc.close()
    return seiten


def diagramm_bild(pdf, nr, bild_dir, max_kante=1800):
    """Seite als PNG ablegen und den Obsidian-Einbettungslink zurueckgeben.

    Begrenzt wird die PIXELKANTE, nicht die dpi. Ueber dpi zu skalieren geht
    schief, weil Seitenrechtecke stark schwanken: dieselben 150 dpi ergaben bei
    einem Vektorskript 200 kB und bei einem gross eingebetteten Scan 6,6 MB.
    """
    import fitz
    bild_dir.mkdir(parents=True, exist_ok=True)
    name = f"{pdf.stem}-s{nr:03d}.png".replace(" ", "-")
    doc = fitz.open(pdf)
    seite = doc[nr - 1]
    lang = max(seite.rect.width, seite.rect.height) or 1
    z = min(max_kante / lang, 4.0)        # nie hochskalieren ueber 4x
    seite.get_pixmap(matrix=fitz.Matrix(z, z)).save(bild_dir / name)
    doc.close()
    return name, bild_dir / name


def _fortschritt(ereignis: dict):
    """JSON-Formatierten Fortschritt nach stderr schreiben."""
    sys.stderr.write(json.dumps(ereignis, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    # 150 dpi statt 300: der Bildvorverarbeiter des Modells deckelt bei
    # max_pixels = 1.003.520 (~1 MP). Eine Spaltenkachel ueberschreitet den
    # Deckel ab ~141 dpi, darueber wird alles wieder heruntergerechnet —
    # gemessen identische Bildtokenzahl (1257) und identische Laufzeit bei 150,
    # 200 und 300 dpi. Unter 141 dpi sinkt die Kosten, aber auch die Genauigkeit:
    # bei 110 dpi zerfaellt auf schlechten Scans "Rechtsbehelfs" zu
    # "Rechtsbehels" und "VwVfG" zu "VwVFG". Darum ist 150 die Untergrenze.
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--kachel-ab", type=int, default=KACHEL_AB)
    ap.add_argument("--kein-fett", action="store_true",
                    help="Fetterkennung ueber Tintendichte abschalten")
    ap.add_argument("--nur-ocr", action="store_true",
                    help="Textlayer ignorieren, alles durchs Modell (Vergleich)")
    ap.add_argument("--neuversuche", type=int, default=1,
                    help="Wie oft eine entgleiste Kachel feiner geschnitten "
                         "neu gerechnet wird (0 schaltet die Reparatur ab, "
                         "die Erkennung bleibt und wird protokolliert)")
    ap.add_argument("--zeilen-dump", type=Path, default=None,
                    help="Zeilen mit Boxen je Seite als JSON ablegen. Nur damit "
                         "laesst sich die Absatzlogik auf Scanseiten aendern, "
                         "ohne jedes Mal den OCR-Lauf zu wiederholen.")
    ap.add_argument("--kein-woerterbuch", action="store_true",
                    help="Woerterbuchabgleich ganz abschalten")
    ap.add_argument("--woerterbuch", action="append", default=[], type=Path,
                    metavar="DATEI",
                    help="zusaetzliche Wortliste oder .dic (mehrfach moeglich). "
                         "Ohne Angabe: PDF2MD_WOERTERBUCH, sonst das erste "
                         "gefundene Systemwoerterbuch")
    ap.add_argument("--woerterbuch-korrigieren", action="store_true",
                    help="eindeutige Faelle ersetzen statt nur melden. "
                         "Eindeutig heisst: genau eine Verwechslungsvariante "
                         "steht im Woerterbuch")
    ap.add_argument("--woerterbuch-bericht", type=Path, default=None,
                    metavar="DATEI",
                    help="alle Befunde mit Seitenzahl als JSON ablegen")
    ap.add_argument("--out", type=Path, default=OUT,
                    help="Zielordner der .md (Standard: .ocr-bench/out-C)")
    ap.add_argument("--bild-dir", type=Path, default=None,
                    help="Ablage der Diagrammbilder (Standard: <out>/assets)")
    ap.add_argument("--bild-max-kante", type=int, default=1800,
                    help="laengste Pixelkante der Diagrammbilder")
    ap.add_argument("--diagramm-seiten", default="",
                    help="Seiten, die IMMER als Diagramm gelten (z.B. 5,7-9). "
                         "Notwendig, weil die automatische Erkennung auf "
                         "hellen Scans mit gleich breiten Kaesten versagt.")
    ap.add_argument("--diagramm-nur-bild", action="store_true",
                    help="Diagrammseiten ohne Text-Callout (nicht durchsuchbar)")
    ap.add_argument("--seiten", default="",
                    help="nur diese Seiten konvertieren (z.B. 1,3-5,8). "
                         "Leer = alle Seiten.")
    ap.add_argument("--fortschritt", action="store_true",
                    help="maschinenlesbaren Fortschritt als JSON-Zeilen auf stderr ausgeben")
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.exists():
        sys.exit(f"nicht gefunden: {pdf}")
    if a.bild_dir is None:
        a.bild_dir = a.out / "assets"
    erzwungen = set()
    for teil in filter(None, (x.strip() for x in a.diagramm_seiten.split(","))):
        if "-" in teil:
            von, bis = (int(x) for x in teil.split("-", 1))
            erzwungen.update(range(von, bis + 1))
        else:
            erzwungen.add(int(teil))
    auswahl = seiten_parsen(a.seiten)
    # Erster SIGINT/SIGTERM: Flag setzen, die laufende Seite zu Ende rechnen,
    # dann Teildatei schreiben und mit Code 6 beenden (vor der ersten Seite
    # gibt es nichts zu schreiben — dann Code 7). Zweites Signal: sofort
    # beenden (raeumt trotzdem auf, siehe finally).
    abbruch.installieren()
    # Zwischenbilder je Lauf in ein eigenes TemporaryDirectory: es raeumt bei
    # normalem Ende, SystemExit und KeyboardInterrupt gleichermassen auf und
    # loest zugleich das Aufraeum-Problem paralleler Lauefe (zwei Prozesse
    # fassten sich vorher per Glob gegenseitig an).
    global TMP
    OUT.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.TemporaryDirectory(prefix=f"_tmp-{pdf.stem}-", dir=OUT)
    TMP = Path(tmp_dir.name)
    try:
        a.out.mkdir(parents=True, exist_ok=True)

        auswahl_text = f" (Seiten {sorted(auswahl)})" if auswahl else ""
        print(f"Analysiere {pdf.name} (Scanseiten @ {a.dpi} dpi){auswahl_text} ...")
        seiten = seiten_analysieren(pdf, a.dpi, a.nur_ocr, auswahl)
        n_ocr = sum(1 for s in seiten if s[1] is not None)
        print(f"   {len(seiten)} Seiten — {len(seiten)-n_ocr} aus dem Textlayer, "
              f"{n_ocr} durch das Modell\n")

        if a.fortschritt:
            _fortschritt({"typ": "start", "datei": pdf.name, "seiten": len(seiten), "dpi": a.dpi})

        # Woerterbuch nur fuer die OCR-Seiten. Der Textlayer ist exakt — dort
        # gemeldete Woerter waeren ausnahmslos Fehlalarme (Eigennamen, Fachbegriffe)
        # und wuerden die echten Befunde zudecken.
        wb = None
        if n_ocr and not a.kein_woerterbuch:
            wb = woerterbuch.lade(a.woerterbuch)
            print("Woerterbuch: " + (wb.quelle if wb else
                  "keins gefunden — Abgleich uebersprungen. Wortliste angeben: "
                  "--woerterbuch <datei> oder PDF2MD_WOERTERBUCH"))
            if wb and not a.woerterbuch_korrigieren:
                print("   nur melden — Ersetzen mit --woerterbuch-korrigieren\n")
            elif wb:
                print("   eindeutige Faelle werden ersetzt\n")

        ocr = None
        if n_ocr:                                  # Modell nur laden, wenn gebraucht
            from mlx_vlm import load, generate
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config
            model, processor = load(MODEL)
            config = load_config(MODEL)
            formatted = apply_chat_template(processor, config, PROMPT, num_images=1)

            def ocr(img, max_tokens=TOKEN_MAX):
                res = generate(model, processor, formatted, image=[str(img)],
                               max_tokens=max_tokens, temperature=0.0,
                               verbose=False)
                return res if isinstance(res, str) else getattr(res, "text", str(res))

        md, t_ges, n_diag, n_entgleist = [], time.perf_counter(), 0, 0
        n_verdaechtig, n_korrigiert, bericht = 0, 0, []
        # Letzte fertig geschriebene Seite — fuer den abgebrochen-Vermerk.
        letzte_seite = 0

        def ablegen(nr, absaetze, diagramm, dt, chars, quelle, weg, spur=(),
                    marker_zusatz=None, befunde=()):
            nonlocal n_diag, letzte_seite
            letzte_seite = nr
            # %% %% ist Obsidians eigene Kommentarsyntax und bleibt auch in der
            # Live-Vorschau unsichtbar; <!-- --> wird dort angezeigt.
            # marker_zusatz ist Teil der Marker-Grammatik (docs/ocr-vorschau.md):
            # die Review-Ansicht haengt Herkunfts-Badges und Layout-Info daran.
            # Diagramm sticht: eine Seite, die als Bild eingebettet wird, ist
            # keine Textseite, egal woher ihr Text stammt.
            if diagramm:
                marker_zusatz = "diagramm"
            kopf = seitenmarker(nr, marker_zusatz)
            zusatz = ""
            if diagramm:
                n_diag += 1
                name, pfad = diagramm_bild(pdf, nr, a.bild_dir, a.bild_max_kante)
                teile = [f"![[{name}]]"]
                if not a.diagramm_nur_bild and absaetze:
                    teile.append(als_callout(
                        absaetze, "Text der Seite (Reihenfolge nicht verlässlich)"))
                md.append(kopf + "\n\n".join(teile))
                zusatz = f" | → {pfad.name} ({pfad.stat().st_size // 1024} kB)"
            else:
                md.append(kopf + "\n\n".join(absaetze))
            print(f"→ S.{nr}: {dt:5.1f} s | {chars:5d} Z. Textlayer → "
                  f"{sum(len(p) for p in absaetze):5d} Z. | "
                  f"{len(absaetze):3d} Absaetze | "
                  f"{'DIAGRAMM als Bild' if diagramm else quelle}{zusatz}")
            if weg:
                print(f"     verworfen ({len(weg)}): "
                      + " ¦ ".join(w[:34] for w in weg[:6])
                      + (" …" if len(weg) > 6 else ""))
            if befunde:
                # Der Befund gehoert ins Protokoll, nicht nur in die Zaehlung:
                # ohne das Wort im Klartext weiss der Begutachtungsdurchgang
                # nicht, wonach er auf der Seite suchen soll. Ersetztes traegt ein
                # ✓ — auch eine ausgefuehrte Korrektur bleibt eine Aenderung am
                # Text und wird nicht stillschweigend vorgenommen.
                print(f"     ⌕ {len(befunde)} Woerter: " + " ¦ ".join(
                    (f"{b.wort} → {b.vorschlag}" if b.vorschlag else f"{b.wort} ?")
                    + (" ✓" if b.korrigiert else "")
                    + ("" if b.anzahl == 1 else f" ({b.anzahl}x)")
                    for b in befunde[:6]) + (" …" if len(befunde) > 6 else ""))
            for zeile in spur:
                print(f"     ⚠ {zeile}")

            if a.fortschritt:
                if diagramm:
                    herkunft = "diagramm"
                elif marker_zusatz == "textlayer":
                    herkunft = "textlayer"
                else:
                    herkunft = "ocr"
                entgleist = bool(spur)
                if entgleist:
                    _fortschritt({
                        "typ": "seite",
                        "nr": nr,
                        "von": len(seiten),
                        "sekunden": round(dt, 1),
                        "herkunft": herkunft,
                        "entgleist": True,
                        "grund": spur[0]
                    })
                else:
                    _fortschritt({
                        "typ": "seite",
                        "nr": nr,
                        "von": len(seiten),
                        "sekunden": round(dt, 1),
                        "herkunft": herkunft,
                        "entgleist": False
                    })

        dump = []

        for nr, png, chars, art, steg, textlayer, kaesten, diagramm in seiten:
            # Geordneter Abbruch: die laufende Seite ist fertig, vor der
            # naechsten wird angehalten (Flag abbruch.py).
            if abbruch.angefordert():
                break
            t = time.perf_counter()
            diagramm = diagramm or nr in erzwungen
            if textlayer is not None:
                # Exakter Text, exakte Koordinaten, exaktes Fett — keine Inferenz.
                zeilen = spalten_trennen(kaesten_zuordnen(textlayer, kaesten))
                dump.append({"seite": nr, "quelle": "textlayer", "zeilen": zeilen})
                absaetze = zusammenfuegen(zeilen)
                # marker_zusatz benannt uebergeben: als achtes Argument landete
                # "textlayer" bisher in `spur`, wurde Zeichen fuer Zeichen als
                # ⚠-Zeile gedruckt, und der Seitenmarker trug `None` statt der
                # Herkunft — gegen die Marker-Grammatik in docs/ocr-vorschau.md.
                ablegen(nr, absaetze, diagramm, time.perf_counter() - t, chars,
                        "Textlayer, ohne Modell",
                        getattr(zusammenfuegen, "verworfen", []),
                        marker_zusatz="textlayer")
                continue
            # Kachelung ist eine LAYOUT-Entscheidung. Ein Laengsschnitt darf nur auf
            # echten Zweispaltern fallen; eine dichte einspaltige Seite wird
            # oben/unten getrennt, sonst zerschneidet man jede Zeile.
            if diagramm and a.diagramm_nur_bild:
                ablegen(nr, [], True, time.perf_counter() - t, chars, "", [],
                        marker_zusatz="ocr")
                continue                      # kein Text gewuenscht, keine Inferenz

            # Zu jeder Kachel ihr x-Fenster in Blattkoordinaten (0–1000). Nur damit
            # laesst sich ein Kasten der richtigen Kachel zuordnen: das Modell
            # rechnet kachelrelativ.
            if art == "zweispaltig":
                modus = f"senkrecht @{steg:.0%}"
                ov = int(OVERLAP * 1000)
                g = int(steg * 1000)
                teile = list(zip(kacheln_senkrecht(png, steg),
                                 [(0, min(g + ov, 1000)), (max(g - ov, 0), 1000)]))
            elif chars >= a.kachel_ab:
                modus = "waagerecht"
                # Waagerechter Schnitt verschiebt y — Kastenzuordnung waere falsch.
                teile = [(p, None) for p, _, _ in kacheln_waagerecht(png)]
            else:
                modus = "ganz"
                teile = [(png, (0, 1000))]

            # Massstab fuer die Laengenpruefung. Traegt die Seite einen Textlayer
            # (Vergleichslauf mit --nur-ocr, oder ein Scan mit Rest-Textlayer),
            # wird der Faktor daraus bestimmt und ist dann exakt fuer diese Seite.
            # Sonst bleibt das Korpusmittel — grob, aber besser als kein Massstab:
            # es faengt die groben Faelle, und die sind es, um die es geht.
            tinte = _tintenmenge(png, a.dpi)
            geeicht = chars >= 400 and tinte > 0
            faktor = chars / tinte if geeicht else ZEICHEN_JE_TINTE

            # Wichtig: in einer bereits geschnittenen Kachel darf die
            # Spaltentrennung NICHT erneut laufen. Die Kachel ist eine einzelne
            # Spalte; der Algorithmus wuerde dort die Gliederungs-Einrueckung
            # (Marker links, Fliesstext eingerueckt) als zweite Spalte deuten und
            # die Absatz-Schlusszeilen nach vorne ziehen.
            zeilen, spur = [], []
            for teil, fenster in teile:
                geparst, s = kachel_zeilen(teil, ocr, not a.kein_fett, faktor,
                                           a.dpi, geeicht,
                                           max_tiefe=a.neuversuche)
                spur += s
                if fenster:
                    geparst = kaesten_zuordnen(geparst, kaesten, fenster)
                geordnet = (spalten_trennen(geparst) if len(teile) == 1
                            else sorted(geparst,
                                        key=lambda z: z[1][1] if z[1] else 0))
                zeilen += ueberlappung_kuerzen(zeilen, geordnet)
            if spur:
                n_entgleist += 1
            dump.append({"seite": nr, "quelle": f"{art}, {modus}", "zeilen": zeilen})
            absaetze = zusammenfuegen(zeilen)
            verworfen = getattr(zusammenfuegen, "verworfen", [])
            absaetze, befunde = woerterbuch.pruefen(absaetze, wb,
                                                    a.woerterbuch_korrigieren)
            # Gezaehlt werden Fundstellen, nicht Woerter: derselbe Lesefehler
            # dreimal auf einer Seite ist dreimal zu pruefen.
            n_korrigiert += sum(b.anzahl for b in befunde if b.korrigiert)
            n_verdaechtig += sum(b.anzahl for b in befunde if not b.korrigiert)
            bericht += [{"seite": nr, "wort": b.wort, "anzahl": b.anzahl,
                         "vorschlag": b.vorschlag, "korrigiert": b.korrigiert}
                        for b in befunde]
            ablegen(nr, absaetze, diagramm, time.perf_counter() - t, chars,
                    f"{art}, {modus}", verworfen, spur, f"ocr | {art}, {modus}",
                    befunde)

        if abbruch.angefordert() and seiten and letzte_seite < seiten[-1][0]:
            # Teildatei: das bis dahin Erzeugte schreiben statt verwerfen.
            # Der Vermerk „abgebrochen: seite n von m" kennzeichnet sie als
            # unvollstaendig; die Zaehlfelder beziehen sich nur auf die
            # tatsaechlich geschriebenen Seiten. Sind alle Seiten fertig, ist
            # der Abbruch erst nach der letzten Seite eingetroffen: die Datei
            # ist vollstaendig und laeuft durch den normalen Abschluss statt
            # als Teildatei gebrandmarkt zu werden.
            geschrieben = [s for s in seiten if s[0] <= letzte_seite]
            if not geschrieben:
                # Vor der ersten Seite gibt es nichts zu retten. Code 7
                # unterscheidet den Fall ohne Datei vom Teildatei-Fall
                # (Code 6) — der Aufrufer (Plugin-Anzeige) behauptet sonst
                # eine Teildatei, die es nicht gibt.
                print("Abbruch vor der ersten Seite — keine Teildatei geschrieben.")
                sys.exit(7)
            kopf = frontmatter_bauen(
                titel=pdf.stem,
                quelle_pdf_pfad=pdf,
                seiten=len(geschrieben),
                seiten_textlayer=sum(1 for s in geschrieben
                                     if s[5] is not None),
                seiten_ocr=sum(1 for s in geschrieben if s[5] is None),
                seiten_diagramm=n_diag,
                seiten_entgleist=n_entgleist,
                woerter_verdaechtig=n_verdaechtig,
                woerter_korrigiert=n_korrigiert,
                ocr_modell=MODEL if n_ocr else None,
                ocr_datum=date.today().isoformat(),
                ocr_zeitpunkt=datetime.now().isoformat(timespec="seconds"),
                abgebrochen=f"seite {letzte_seite} von {len(seiten)}",
            )
            quelle = f"Quelle: [[{pdf.as_posix()}]]\n"
            ziel = a.out / f"{pdf.stem}.md"
            ziel.write_text(dokument_bauen(kopf, quelle, md),
                            encoding="utf-8")
            print(f"\nAbbruch: Teildatei geschrieben "
                  f"({letzte_seite} von {len(seiten)} Seiten) → {ziel}")
            sys.exit(6)

        ges = time.perf_counter() - t_ges
        # Welche Seiten exakt sind und welche erkannt, muss in der Datei stehen:
        # nur bei den OCR-Seiten ist ein Rueckgriff aufs Original noetig.
        # Der Zusammenbau selbst liegt in zusammenbau.frontmatter_bauen() — die
        # reine Funktion, die auch die Fixture nutzt (docs/vorschau-format.md).
        kopf = frontmatter_bauen(
            titel=pdf.stem,
            quelle_pdf_pfad=pdf,
            seiten=len(seiten),
            seiten_textlayer=len(seiten) - n_ocr,
            seiten_ocr=n_ocr,
            seiten_diagramm=n_diag,
            # Auffaellig gewordene Seiten benennen, nicht verschweigen: auf
            # ihnen ist die Ausgabe auch nach dem Neuversuch unsicher.
            seiten_entgleist=n_entgleist,
            # Fundstellen des Woerterbuchabgleichs, nicht Woerter: die Zahl
            # sagt der Begutachtung, wieviel auf sie zukommt.
            woerter_verdaechtig=n_verdaechtig,
            woerter_korrigiert=n_korrigiert,
            ocr_modell=MODEL if n_ocr else None,
            ocr_datum=date.today().isoformat(),
            # Feingranularer Erzeugungszeitpunkt: die Review-Ansicht erkennt an
            # ihm Neukonvertierungen am selben Tag, die `ocr-datum` nicht sieht.
            ocr_zeitpunkt=datetime.now().isoformat(timespec="seconds"),
        )
        # Anklickbarer Rueckgriff aufs Original. Bei OCR-Seiten ist er Pflicht, nicht
        # Bequemlichkeit: Wortfehler sind nicht mechanisch korrigierbar, ohne die
        # Quelle also unauffindbar.
        quelle = f"Quelle: [[{pdf.as_posix()}]]\n"
        ziel = a.out / f"{pdf.stem}.md"
        ziel.write_text(dokument_bauen(kopf, quelle, md),
                        encoding="utf-8")
        if a.zeilen_dump:
            a.zeilen_dump.write_text(json.dumps(dump, ensure_ascii=False),
                                     encoding="utf-8")
            print(f"→ {a.zeilen_dump} ({len(dump)} Seiten)")
        if a.woerterbuch_bericht:
            a.woerterbuch_bericht.write_text(
                json.dumps(bericht, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ {a.woerterbuch_bericht} ({len(bericht)} Befunde)")
        if a.fortschritt:
            _fortschritt({
                "typ": "fertig",
                "ziel": str(a.out / f"{pdf.stem}.md"),
                "sekunden": round(ges, 1),
                "entgleist": n_entgleist
            })
        print(f"\n{ges:.1f} s gesamt ({ges/len(seiten):.1f} s/Seite)\n→ {ziel}")
    finally:
        # Zwischenbilder nicht liegenlassen — auch nicht bei Abbruch.
        tmp_dir.cleanup()

if __name__ == "__main__":
    main()
