#!/usr/bin/env python3
"""Pfad C, Ende-zu-Ende: PDF → Markdown.

  source .venv-mlxocr/bin/activate && python .ocr-bench/pdf2md.py <pdf> [--dpi 300]

Rendert jede Seite, kachelt bei hoher Textdichte, laesst PaddleOCR-VL laufen und
baut die Zeilen anhand ihrer <|LOC|>-Koordinaten zu Markdown zusammen.

Erste Fassung der Zusammenbau-Schicht. Schreibt nach .ocr-bench/out-C/,
fasst raw/ nicht an.
"""
import argparse, json, math, re, statistics, sys, time
from collections import Counter
from datetime import date
from pathlib import Path
import zusammenbau
from zusammenbau import *

BENCH = Path(__file__).resolve().parent
OUT = BENCH / "out-C"
# Zwischenbilder je Lauf in einen eigenen Ordner. Zwei gleichzeitig laufende
# pdf2md-Prozesse loeschten sich sonst gegenseitig die Kacheln weg — das
# Aufraeumen am Ende greift per Glob auf das ganze Verzeichnis zu.
TMP = OUT
MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PROMPT = "Parse this document page to Markdown."
KACHEL_AB = 3000      # Zeichen im vorhandenen Textlayer
OVERLAP = 0.02


def _steg(mit_box):
    """(Position des Spaltenstegs, vollbreite Zeilen) oder None.

    Geprueft wird JEDE Luecke im x-Start-Histogramm, nicht nur die groesste.
    Die groesste allein taugt nicht als Kandidat: ein halbes Dutzend
    eingerueckter oder zentrierter Einzelzeilen zwischen den Spalten zerlegt
    den echten Steg in mehrere kleine Spruenge, waehrend am rechten Rand eine
    breitere Luecke ohne jede Bedeutung stehenbleibt. Gemessen an
    `Klausur_2137_Strafrecht_Loesung` S. 7: der echte Steg lag bei 505 (Luecke
    103, ein Kreuzer), gewaehlt wurde 817 (Luecke 122, 49 Kreuzer) — die Seite
    lief danach zeilenweise verschraenkt durch die Absatzlogik.
    """
    if len(mit_box) < 8:
        return None
    # Kopf-/Fusszeilen zaehlen beim Steg nicht mit: sie gehoeren zu keiner
    # Spalte, fliegen ohnehin raus — und eine Seitenzahl mitten im Bund
    # zerlegt das Start-Histogramm genau dort, wo der Steg liegt.
    satz = [z for z in mit_box if not ist_boilerplate(z[0], z[1][1])] or mit_box
    starts = sorted(z[1][0] for z in satz)
    breite = max(z[1][2] for z in satz) - min(z[1][0] for z in satz)
    if breite <= 0:
        return None
    # Vollbreite Zeilen (Kopf/Fuss/Ueberschrift) bleiben an ihrer y-Position
    voll = [z for z in mit_box if z[1][2] - z[1][0] > breite * 0.6]
    bester = None
    for a, b in zip(starts, starts[1:]):
        if b <= a:
            continue
        luecke, pos = b - a, (a + b) / 2
        # Eine Luecke allein beweist keine zweite Spalte. Auf einer
        # einspaltigen Seite reichen drei eingerueckte oder zentrierte Zeilen
        # fuer eine Luecke von 30 % der Textbreite — und die Umsortierung zieht
        # dann Zeilenreste an den Anfang ("Rechtshaengigkeit.", "ZR 67/22" vor
        # ihrem eigenen Absatz). Also muss jede Seite des Stegs auch
        # nennenswert besetzt sein.
        n_links = sum(1 for z in mit_box if z[1][0] < pos)
        anteil = min(n_links, len(mit_box) - n_links) / len(mit_box)
        if anteil < 0.25:
            continue
        # Der harte Beweis ist nicht die Breite der Luecke, sondern dass keine
        # Zeile sie ueberquert. Eine eingerueckte Passage erzeugt eine Luecke
        # im Start-Histogramm, aber die Zeilen darueber und darunter laufen
        # quer hindurch. Ein echter Spaltensteg wird von niemandem
        # ueberschritten.
        kreuzer = sum(1 for z in mit_box
                      if z not in voll and z[1][0] < pos < z[1][2])
        # Breite Luecke wie bisher — oder schmalere Luecke, die dafuer sauber
        # ist. Nur ergaenzend, nie einschraenkend: was frueher gespalten wurde,
        # wird es weiter, sonst verlieren die zweispaltigen Loesungsboegen ihre
        # Trennung.
        sauber = luecke >= breite * 0.08 and kreuzer <= 0.02 * len(mit_box)
        if not (luecke >= breite * 0.25 or sauber):
            continue
        # Rang: erst wenige Kreuzer, dann breite Luecke. Der Kreuzer ist das
        # staerkere Signal — eine breite Luecke, durch die Zeilen laufen, ist
        # kein Steg, eine schmale ohne Kreuzer sehr wohl.
        rang = (-kreuzer, luecke)
        if bester is None or rang > bester[0]:
            bester = (rang, pos)
    if bester is None:
        return None
    return bester[1], voll


def spalten_trennen(zeilen, tiefe=0):
    """Einspaltig → nach y sortiert. Zweispaltig → linke Spalte, dann rechte.

    Rekursiv, weil ein Blatt mehr als zwei Spalten haben kann: die WuV-Boegen
    sind Doppelseiten (zwei logische Seiten nebeneinander) mit je einer Frage-
    und einer Antwortspalte — vier Spalten auf einem Blatt. Ein einzelner
    Schnitt liefert dort abwechselnd Text aus beiden logischen Seiten.
    """
    mit_box = [z for z in zeilen if z[1]]
    y = lambda z: z[1][1]
    if tiefe >= 2:
        return sorted(zeilen, key=lambda z: z[1][1] if z[1] else 0)
    treffer = _steg(mit_box)
    if treffer is None:
        return sorted(zeilen, key=lambda z: z[1][1] if z[1] else 0) \
            if tiefe or mit_box else zeilen
    pos, voll = treffer
    links = [z for z in mit_box if z not in voll and z[1][0] < pos]
    rechts = [z for z in mit_box if z not in voll and z[1][0] >= pos]
    kopf = [z for z in voll if y(z) < min([y(z) for z in links + rechts], default=0)]
    rest_voll = [z for z in voll if z not in kopf]

    # Erst wenn keine der beiden Spalten sich weiter teilt, stehen die echten
    # Spalten fest — vorher waere "links" beim WuV-Bogen noch eine ganze
    # logische Seite und die Rasterpruefung liefe auf dem falschen Paar.
    if _steg(links) is None and _steg(rechts) is None:
        raster = frage_antwort_raster(links, rechts)
        if raster is not None:
            return sorted(kopf, key=y) + raster + sorted(rest_voll, key=y)
    return (sorted(kopf, key=y) + spalten_trennen(links, tiefe + 1)
            + spalten_trennen(rechts, tiefe + 1) + sorted(rest_voll, key=y))


def _zeilenanfang(text):
    """Beginnt hier ein neuer Gedanke? Gliederungsmarker oder fette Zeile."""
    nackt = text.lstrip("*").lstrip()
    return bool(AUFZAEHLUNG.match(nackt)) or (
        text.startswith("**") and text.endswith("**")
        and text.count("**") == 2 and len(nackt) <= 90)


def _bloecke(spalte, faktor=0.8):
    """Zeilen einer Spalte an Leerraum in Bloecke schneiden."""
    spalte = sorted(spalte, key=lambda z: z[1][1])
    if not spalte:
        return []
    med = statistics.median([z[1][3] - z[1][1] for z in spalte]) or 10
    aus = [[spalte[0]]]
    for a, b in zip(spalte, spalte[1:]):
        (aus.append([b]) if b[1][1] - a[1][3] > faktor * med
         else aus[-1].append(b))
    return aus


def frage_antwort_raster(links, rechts, tol=3):
    """Zwei Spalten als Markdown-Tabelle, wenn rechts an links haengt.

    Die WuV-Boegen sind Frage-Antwort-Raster: man liest quer, nicht runter.
    Spaltenweise ausgegeben stehen erst alle Fragen, dann alle Antworten — die
    Zuordnung geht verloren. Rueckgabe None heisst "kein Raster", dann bleibt
    es bei der normalen Spaltenreihenfolge.

    Merkmal (gemessen, siehe ERGEBNIS.md): faengt jeder Absatz der rechten
    Spalte auf derselben Hoehe an wie ein Zeilenanfang der linken? Bei den
    WuV-Boegen 3/3 bis 13/13 (>= 80 %), bei zweispaltiger Prosa 0/5, 0/3, 1/8
    (<= 13 %) — dort laufen die beiden Spalten unabhaengig.
    """
    # Kopf- und Fusszeile fliegen VOR der Rasterpruefung raus. Sonst landet
    # "RA Dr. Michael Hein, M.A., LL.M. - 04/2026" mitten in der Antwortzelle:
    # zusammenfuegen() sieht die fertige Tabelle nur noch als einen Block und
    # kommt mit seinem Boilerplate-Test nicht mehr an die einzelne Zeile heran.
    links = [z for z in links if not ist_boilerplate(saeubern(z[0]), z[1][1])]
    rechts = [z for z in rechts if not ist_boilerplate(saeubern(z[0]), z[1][1])]
    if len(links) < 6 or len(rechts) < 6:
        return None
    anfaenge = sorted(z[1][1] for z in links if _zeilenanfang(z[0]))
    if len(anfaenge) < 4:
        return None
    rechts_bloecke = _bloecke(rechts)
    passend = sum(1 for b in rechts_bloecke
                  if any(abs(b[0][1][1] - a) <= tol for a in anfaenge))
    if passend < 3 or passend < 0.75 * len(rechts_bloecke):
        return None

    # Zeilen der linken Spalte in Zeilenbloecke schneiden, jeder Zeilenanfang
    # macht eine neue Tabellenzeile auf.
    sortiert = sorted(links, key=lambda z: z[1][1])
    reihen, vorspann = [], []
    for z in sortiert:
        if _zeilenanfang(z[0]):
            reihen.append([z])
        elif reihen:
            reihen[-1].append(z)
        else:
            vorspann.append(z)       # Titel oberhalb der ersten Frage

    def text(gruppe):
        s = ""
        for z in gruppe:
            t = saeubern(z[0])
            # Trennstrich aufloesen wie in zusammenfuegen() — in einer
            # Tabellenzelle laeuft die Absatzlogik nicht mehr, und "Behoerdenei-
            # genschaft" bliebe sonst stehen.
            if s.endswith("-") and not KEIN_JOIN.match(t) and t[:1].islower():
                s = s[:-1] + t
            else:
                s = (s + " " + t) if s else t
        s = re.sub(r"\*\*(\s*)\*\*", r"\1", s).strip()
        return re.sub(r"\s{2,}", " ", s).replace("|", r"\|")

    grenzen = [r[0][1][1] for r in reihen] + [10 ** 6]
    aus = [[saeubern(z[0]), z[1]] for z in vorspann]
    # Antwortzeilen oberhalb der ersten Frage gehoeren zu keiner Reihe
    for z in sorted(rechts, key=lambda q: q[1][1]):
        if z[1][1] < grenzen[0] - tol:
            aus.append([saeubern(z[0]), z[1]])

    # Spaltentitel nur setzen, wenn links wirklich Fragen stehen. Dasselbe
    # Raster traegt auch Begriff/Erlaeuterung ("Uebersicht zum
    # Schadensersatzrecht") — dort waere "Frage" schlicht falsch, und Markdown
    # kommt mit einer leeren Kopfzeile aus.
    fragen = sum(1 for r in reihen if r[-1][0].rstrip().endswith("?"))
    titel = ("| Frage | Antwort |" if reihen and fragen >= 0.5 * len(reihen)
             else "|  |  |")
    puffer = []                       # gesammelte Tabellenzeilen

    def tabelle_schliessen(box):
        if not puffer:
            return
        aus.append(["\n".join([titel, "| --- | --- |"] + puffer),
                    box, "tabelle"])
        puffer.clear()

    for i, reihe in enumerate(reihen):
        oben, unten = grenzen[i] - tol, grenzen[i + 1] - tol
        antwort = [z for z in sorted(rechts, key=lambda q: q[1][1])
                   if oben <= z[1][1] < unten]
        box = (min(z[1][0] for z in reihe), reihe[0][1][1],
               max(z[1][2] for z in reihe + antwort),
               max(z[1][3] for z in reihe + antwort))
        frage = text(reihe)
        if not antwort and _zeilenanfang(reihe[0][0]) and len(reihe) == 1 \
                and reihe[0][0].startswith("**"):
            # Abschnittsueberschrift ohne Antwort ("Fall 1", "Prozessuales").
            # Markdown kann keine Zeile ueber beide Spalten ziehen — also
            # Tabelle schliessen, Ueberschrift als Absatz, danach neue Tabelle.
            tabelle_schliessen(box)
            aus.append([frage, box])
            continue
        puffer.append(f"| {frage} | {text(antwort)} |")
    tabelle_schliessen((0, grenzen[-2] if len(grenzen) > 1 else 0, 1000, 1000))
    return aus


# --- Rendern & Inferenz ----------------------------------------------------

def fett_markieren(zeilen, bildpfad, faktor=1.45, max_breite=0.75):
    """Zeilenweise Fettschrift ueber Tintendichte erkennen und `**` setzen.

    Das Modell gibt keine Formatierung aus (verifiziert: null `**` in allen
    Ausgaben, auch in der vollen PaddlePaddle-Pipeline und bei explizitem
    Prompt). Bleibt die Messung im Bild: Fettschrift hat bei gleicher
    Schriftgroesse einen hoeheren Anteil dunkler Pixel.

    Verglichen wird nur innerhalb aehnlicher Zeilenhoehen — sonst gelten
    Ueberschriften allein wegen ihrer Groesse als fett.

    GRENZE: Das arbeitet zeilenweise. Ein einzelnes fettes Wort mitten im Satz
    ist so nicht erfassbar, weil das Modell nur Zeilen-, keine Wortboxen liefert.
    """
    import numpy as np
    from PIL import Image
    mit_box = [z for z in zeilen if z[1]]
    if len(mit_box) < 5:
        return zeilen
    g = np.asarray(Image.open(bildpfad).convert("L"))
    H, W = g.shape

    werte = []
    for z in mit_box:
        x0, y0, x1, y1 = z[1]
        px0, py0 = int(x0 / 1000 * W), int(y0 / 1000 * H)
        px1, py1 = int(x1 / 1000 * W), int(y1 / 1000 * H)
        if px1 - px0 < 5 or py1 - py0 < 3:
            werte.append(None)
            continue
        feld = g[py0:py1, px0:px1]
        werte.append(((feld < 128).mean(), py1 - py0))

    gueltig = [w for w in werte if w]
    if len(gueltig) < 5:
        return zeilen
    # Hoehenklassen bilden (5-px-Raster), Median je Klasse
    from collections import defaultdict
    klassen = defaultdict(list)
    for dichte, hoehe in gueltig:
        klassen[hoehe // 5].append(dichte)
    global_med = statistics.median(d for d, _ in gueltig)

    # Volle Zeilenbreite als Referenz: ausgeschriebener Fliesstext im Blocksatz
    # laeuft ueber die ganze Spalte, Ueberschriften sind kuerzer. Ohne diese
    # Zusatzbedingung werden dunklere Fliesstextzeilen fett markiert.
    spaltenbreite = max((z[1][2] - z[1][0]) for z in mit_box) or 1

    for z, w in zip(mit_box, werte):
        if not w:
            continue
        dichte, hoehe = w
        gruppe = klassen.get(hoehe // 5, [])
        med = statistics.median(gruppe) if len(gruppe) >= 3 else global_med
        breit = (z[1][2] - z[1][0]) / spaltenbreite
        if med > 0 and dichte > med * faktor and breit <= max_breite:
            t = z[0].strip()
            if t and not t.startswith("**"):
                z[0] = f"**{t}**"
    return zeilen


def _tintensteg(page, dpi=72, quantil=60, tal=0.35, flanke=0.35,
                min_breite=0.015, luecke=0.01):
    """(steg_relativ, stegbreite_relativ) aus dem senkrechten Tintenprofil.

    Referenz ist ein QUANTIL des Profils, nicht das Maximum: bei gescannten
    Skripten ist das Maximum der Ringbindungs-Schatten am Blattrand und macht
    jede auf max normierte Schwelle unbrauchbar. Der Median wiederum wird von
    breiten Raendern nach unten gezogen — bei "Verwaltungsprozessrecht" S. 54
    steht der Satz in zwei Dritteln der Blattbreite, der Median beschreibt
    dort den leeren Rand und der Satzspiegel ragt als 6-faches heraus. Auf
    solch kleiner Referenz wird jedes Rauschen im Rand zum Tal.

    Ein Steg ist ein Tal MIT TINTE AUF BEIDEN SEITEN. Ohne diese Bedingung
    zaehlt der rechte Rand als Steg: die Hemmer-Skripte setzen den Satz in
    etwa zwei Drittel der Blattbreite, das Profil faellt bei 0,47 ab und
    kommt nicht wieder ("Verwaltungsprozessrecht" S. 54, einspaltig).

    Und er hat BREITE. Auf schmutzigen Scans sinkt das Profil vereinzelt fuer
    ein einziges Pixel ab ("Strafrecht AT VI" S. 3: drei solcher Dips bei 0,60,
    0,65 und 0,70) — das ist Rauschen, kein Steg.

    Umgekehrt zerteilt ein einzelnes Pixel Rauschen MITTEN im Steg das Tal in
    zwei Haelften, von denen jede die andere als schlechte Flanke sieht
    (`strafrecht-fall-02` S. 19, echter Zweispalter). Deshalb werden nahe
    Taeler vorher verschmolzen.
    """
    import fitz
    import numpy as np
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    ink = (arr < 200).sum(axis=0).astype(float)
    W = pm.width
    ref = float(np.percentile(ink[int(W * 0.10):int(W * 0.90)], quantil))
    if ref <= 0:
        return None, 0.0
    lo, hi, schwelle = int(W * 0.30), int(W * 0.70), ref * tal
    fenster = max(1, int(W * 0.05))        # Fenster fuer die Flankenpruefung

    taeler, akt = [], None
    for i in range(lo, hi + 1):
        if i < hi and ink[i] < schwelle:
            akt = i if akt is None else akt
        elif akt is not None:
            taeler.append([akt, i])
            akt = None
    verschmolzen = []
    for t in taeler:
        if verschmolzen and t[0] - verschmolzen[-1][1] <= W * luecke:
            verschmolzen[-1][1] = t[1]
        else:
            verschmolzen.append(t)

    beste = (0, None)
    for a, b in verschmolzen:
        links, rechts = ink[max(0, a - fenster):a], ink[b:b + fenster]
        if (b - a) / W < min_breite or not len(links) or not len(rechts):
            continue
        if (float(np.median(links)) < ref * flanke
                or float(np.median(rechts)) < ref * flanke):
            continue
        if b - a > beste[0]:
            beste = (b - a, (a + b) // 2)
    breite, mitte = beste
    return (mitte / W if mitte is not None else None), breite / W


def _kantenanteil(page, steg):
    """Anteil der Textzeilen, deren rechte Kante am Steg endet.

    Das staerkste Einzelsignal: auf echten Zweispaltern enden 40–50 % aller
    Zeilen am Steg, auf einspaltigen Seiten nur 15–22 % (kurze Absatzenden,
    Tabellenzellen). Braucht einen Textlayer; ohne ihn None.
    """
    W = page.rect.width or 1
    kanten = [ln["bbox"][2] / W for b in page.get_text("dict")["blocks"]
              if b.get("type") == 0 for ln in b["lines"]]
    if len(kanten) < 20:
        return None
    lo, hi = steg - 0.15, steg + 0.10
    return sum(1 for x in kanten if lo <= x <= hi) / len(kanten)


def layout_erkennen(page):
    """('einspaltig'|'zweispaltig', steg_relativ).

    Zwei unabhaengige Signale, weil jedes allein versagt:
      - Tintenprofil liefert die Stegposition, wird aber durch
        Rueckseiten-Durchschlag im Steg verwaessert.
      - Rechte-Kanten-Anteil entscheidet zuverlaessig, braucht aber Textlayer.
    """
    steg, breite = _tintensteg(page)
    if steg is None:
        # Kein Steg im Bild, also kein Laengsschnitt. Der Kantenanteil darf
        # eine Stegposition BESTAETIGEN, nie eine erfinden: gemessen an 0,5
        # erreichen auch einspaltige Seiten mit breitem rechten Rand 0,31–0,77,
        # und die Seite wird dann mitten im Satz zerschnitten.
        return "einspaltig", None
    anteil = _kantenanteil(page, steg)
    zwei = anteil >= 0.30 if anteil is not None else breite >= 0.010
    return ("zweispaltig", steg) if zwei else ("einspaltig", None)


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


def bildanteil(page):
    """Flaechenanteil eingebetteter Rasterbilder. >= 0.5 heisst: Scan."""
    import fitz
    ganz = abs(page.rect.width * page.rect.height) or 1
    flaeche = 0.0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 1:                      # Bildblock
            x0, y0, x1, y1 = b["bbox"]
            flaeche += abs((x1 - x0) * (y1 - y0))
    return min(flaeche / ganz, 1.0)


# --- Kaesten & Diagramme ---------------------------------------------------

def _entdoppeln(kaesten, nah=6):
    """Ein Rahmen wird oft doppelt gezeichnet (Fuellung + Strich) oder aus vier
    Einzellinien. Nahezu deckungsgleiche Rechtecke zusammenfassen."""
    aus = []
    for k in sorted(kaesten, key=lambda r: -(r[2] - r[0]) * (r[3] - r[1])):
        if not any(all(abs(k[i] - v[i]) <= nah for i in range(4)) for v in aus):
            aus.append(k)
    return aus


def _laengster_lauf(maske):
    """Je Zeile die Laenge des laengsten zusammenhaengenden True-Laufs.

    Das ist das entscheidende Merkmal fuer Rasterscans. Eine Summe ueber die
    Zeile zaehlt auch Textzeilen mit — eine Textzeile im Zweispalter deckt
    genauso viel Blattbreite ab wie ein Rahmenstrich. Der Unterschied ist die
    Lueckenlosigkeit.
    """
    import numpy as np
    lauf = np.zeros(maske.shape[0], dtype=np.int32)
    akt = np.zeros(maske.shape[0], dtype=np.int32)
    for j in range(maske.shape[1]):
        akt = np.where(maske[:, j], akt + 1, 0)
        np.maximum(lauf, akt, out=lauf)
    return lauf


def _verschmelzen(kaesten, x_tol=4, y_luecke=5):
    """Senkrecht anschliessende Rechtecke gleicher Breite zu einem zusammenfassen.

    Notwendig, weil PDFs eine schattierte Textpassage haeufig ZEILENWEISE
    hinterlegen: vier gleich breite Streifen von je ~17 pt statt eines Kastens.
    Ungefasst gilt dann jede Zeile als eigener Kasten — und weil ein
    Kastenwechsel einen Absatz erzwingt, zerfaellt der Kasten in Einzelzeilen.
    """
    offen = sorted(kaesten, key=lambda k: (round(k[0]), round(k[2]), k[1]))
    aus = []
    for k in offen:
        if aus and abs(aus[-1][0] - k[0]) <= x_tol and abs(aus[-1][2] - k[2]) <= x_tol \
                and k[1] - aus[-1][3] <= y_luecke:
            v = aus[-1]
            aus[-1] = (v[0], min(v[1], k[1]), v[2], max(v[3], k[3]))
        else:
            aus.append(k)
    return aus


def kaesten_vektor(page, min_b=25, min_h=14):
    return _verschmelzen(_entdoppeln(
        [(d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)
         for d in page.get_drawings()
         if d["rect"].width >= min_b and d["rect"].height >= min_h]))


def kaesten_raster(page, dpi=110, quer=0.12, laengs=0.12, dicke=4):
    """Kaesten aus lueckenlosen Geraden im Bild.

    Getrennte Schwellen je Richtung, und das ist der Punkt: eine Querlinie
    ueberspannt die Kastenbreite, eine Laengslinie nur die Kastenhoehe. Ein
    Diagrammkasten ist rund 2,5 % der Seitenhoehe hoch — mit einer gemeinsamen
    Schwelle von 12 % sind seine senkrechten Kanten unauffindbar, und ohne sie
    entsteht gar kein Rechteck. Genau daran ist die Diagrammseite
    "Unterlassungsdelikte" durchgefallen.

    2 % der Hoehe entsprechen etwa zwei Textzeilen. Kuerzer darf eine Laengslinie
    nicht sein, sonst liefern Buchstaben selbst Treffer.
    """
    import fitz
    import numpy as np
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    a = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    dunkel = a < 170
    H, W = dunkel.shape

    def sammeln(lauf, laenge, rand, mindest):
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

    ys = sammeln(_laengster_lauf(dunkel), W, int(H * 0.02), quer)
    xs = sammeln(_laengster_lauf(dunkel.T), H, int(W * 0.04), laengs)

    sx, sy = page.rect.width / W, page.rect.height / H
    aus = [(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
           for y0, y1 in zip(ys, ys[1:]) if (y1 - y0) >= H * 0.02
           for x0, x1 in zip(xs, xs[1:]) if (x1 - x0) >= W * 0.08]
    return _entdoppeln(aus)


def _cluster(werte, tol):
    werte = sorted(werte)
    n = 1
    for a, b in zip(werte, werte[1:]):
        if b - a > tol:
            n += 1
    return n


def schraege(page, min_len=10):
    """Zeichenbefehle mit mindestens einer echten Schraeglinie — also Pfeile.

    Zweites, unabhaengiges Diagramm-Merkmal. Es fasst die Faelle, die das
    Breiten-Kriterium nicht sieht: rahmenlose Pfeilbaeume, bei denen nur Text und
    Pfeile auf dem Blatt stehen.

    Kurven bleiben bewusst aussen vor. Zaehlt man sie mit, kommt eine 36x5-Tabelle
    auf 58 Treffer und wuerde durch ein Bild ersetzt statt tabelliert. Gemessen
    mit dieser Einschraenkung: Prosa und Tabellen 0, Diagramme 1–6.
    """
    n = 0
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                if (abs(it[2].x - it[1].x) > min_len
                        and abs(it[2].y - it[1].y) > min_len):
                    n += 1
                    break
    return n


def ist_diagramm(kaesten):
    """Entscheidet allein ueber die Streuung der Kastenbreiten (0–1000).

    Ein umrandeter Kasten fuellt die Textspalte — alle Kaesten einer Seite sind
    darum praktisch gleich breit (gemessene Streuung 0,000–0,028 auf sieben
    Seiten). Ein Diagrammkasten ist auf seinen Inhalt zugeschnitten, die Breiten
    streuen (0,15–0,22 auf vier Seiten).

    Bewusst OHNE Spaltensteg und Nachbarschaftspruefung: layout_erkennen() ist
    auf Prosa abgestimmt und lag hier in BEIDE Richtungen falsch — es hielt einen
    Zweispalter mit gestapelten Kaesten fuer ein Diagramm und ein einspaltiges
    Diagramm fuer einen Zweispalter.
    """
    if len(kaesten) < 3:
        return False
    breiten = [(k[2] - k[0]) / 1000 for k in kaesten]
    if _cluster(breiten, 0.05) < 3:
        return False
    m = sum(breiten) / len(breiten)
    return (sum((b - m) ** 2 for b in breiten) / len(breiten)) ** 0.5 >= 0.05


def nebeneinander(kaesten, rand=8):
    """Gibt es zwei Kaesten auf gleicher Hoehe ohne x-Ueberschneidung?

    Dann ist die Leserichtung nicht mehr bestimmbar — ein Baum, kein Stapel.
    Taugt nur unter zwei Bedingungen: die Seite muss einspaltig sein (sonst sind
    linke und rechte Textspalte selbst "nebeneinander"), und es braucht eine
    verlaessliche Spaltenerkennung. Auf Rasterscans ist layout_erkennen()
    geprueft; auf vektoriellen Diagrammseiten irrt es, dort traegt statt dessen
    das Schraeglinien-Merkmal.
    """
    for i, a in enumerate(kaesten):
        for b in kaesten[i + 1:]:
            hoch = min(a[3], b[3]) - max(a[1], b[1])
            if hoch <= 0.4 * min(a[3] - a[1], b[3] - b[1]):
                continue
            if a[2] <= b[0] + rand or b[2] <= a[0] + rand:
                return True
    return False


def kaesten_erkennen(page, scan, tab_rahmen=()):
    """(Kaesten in 0–1000, ist_diagramm). Nur Kaesten MIT Text zaehlen."""
    W = page.rect.width or 1
    H = page.rect.height or 1
    zeilen = [ln["bbox"] for b in page.get_text("dict")["blocks"]
              if b.get("type") == 0 for ln in b["lines"]
              if "".join(s["text"] for s in ln["spans"]).strip()]
    fl = abs(W * H) or 1
    mit_text = []
    for k in (kaesten_raster(page) if scan else kaesten_vektor(page)):
        if (k[2] - k[0]) * (k[3] - k[1]) >= 0.75 * fl:
            continue                      # Ganzseitenrahmen ist der Blattrand
        if (k[3] - k[1]) >= 0.85 * H or (k[2] - k[0]) >= 0.92 * W:
            # Fast blatthoher Rahmen ist eine Umrandung, kein Inhaltskasten.
            # Doppelseiten-Layouts (zwei logische Seiten auf einem Querblatt)
            # liefern zwei Rahmen von 0,50 x 0,99 — die Flaeche bleibt unter der
            # 75-%-Grenze, und als "nebeneinander" machten sie dichte Prosa zum
            # Diagramm. Echte Diagrammkaesten sind hoechstens 0,47 hoch.
            continue
        mx, my = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
        if any(x0 <= mx <= x1 and y0 <= my <= y1 for x0, y0, x1, y1 in tab_rahmen):
            continue                      # Tabellenzellen sind keine Kaesten —
                                          # sonst gilt eine 36x5-Tabelle als
                                          # Diagramm und wird durch ein Bild
                                          # ersetzt statt tabelliert
        n = sum(1 for z in zeilen
                if k[0] <= (z[0] + z[2]) / 2 <= k[2]
                and k[1] <= (z[1] + z[3]) / 2 <= k[3])
        if n:
            mit_text.append((k, n))
    norm = lambda k: (int(k[0] / W * 1000), int(k[1] / H * 1000),
                      int(k[2] / W * 1000), int(k[3] / H * 1000))
    # Zwei unabhaengige Merkmale, verknuepft mit ODER: Kastenbreiten fassen
    # gerahmte Diagramme, Schraeglinien die rahmenlosen Pfeilbaeume. Fuer
    # Rasterscans gibt es keine Zeichenbefehle — dort traegt nur das erste.
    alle = [norm(k) for k, _ in mit_text]
    # Drei Merkmale, mit ODER verknuepft. Die beiden letzten gelten nur
    # vektoriell, weil sie exakte Koordinaten bzw. Zeichenbefehle brauchen;
    # aus Rasterlinien rekonstruiert liefern sie zu viele Fehlalarme.
    #   1. Streuung der Kastenbreiten — gerahmte Diagramme mit ungleichen Kaesten
    #   2. Schraeglinien — rahmenlose Pfeilbaeume
    #   3. Kaesten nebeneinander — Raster gleich breiter Kaesten ("Uebersicht zur
    #      Verspaetung der Leistung": drei Spalten a 0,20 Breite, Streuung null,
    #      von Merkmal 1 nicht zu sehen)
    mehrzeilig = [norm(k) for k, n in mit_text if n >= 2]
    diag = (ist_diagramm(alle)
            or (not scan and schraege(page) >= 2)
            # Merkmal 3 nur mit mehrzeiligen Kaesten: hinterlegte Kopfstreifen
            # stehen ebenfalls paarweise nebeneinander ("WuV_Verwaltungsrecht"
            # S. 4), tragen aber je eine Zeile Ueberschrift und keinen Aufbau.
            or (not scan and nebeneinander(mehrzeilig)))
    # Fuer die Absatztrennung nur Kaesten mit mindestens zwei Zeilen. Ein
    # einzeiliger Rahmen (Diagrammbeschriftung, hinterlegte Ueberschrift) wuerde
    # sonst mitten im Fliesstext einen Absatz erzwingen. Fuer die
    # Diagrammentscheidung zaehlen sie weiter mit — dort tragen ihre Breiten.
    return [norm(k) for k, n in mit_text if n >= 2], diag


def kaesten_zuordnen(zeilen, kaesten, x_bereich=None):
    """Jeder Zeile den Kasten anhaengen, in dem sie liegt (als Marke in z[2]).

    Ohne `x_bereich` (Textlayer) wird der Zeilenmittelpunkt geprueft. Mit
    `x_bereich` (OCR-Kachel) nur die Hoehe: die Modellkoordinaten sind
    kachelrelativ in x, aber der senkrechte Kachelschnitt laesst die volle
    Blatthoehe stehen — y ist also direkt vergleichbar, x nicht.
    """
    if not kaesten:
        return zeilen
    for z in zeilen:
        if not z[1] or (len(z) > 2 and z[2] == "tabelle"):
            continue
        mx, my = (z[1][0] + z[1][2]) / 2, (z[1][1] + z[1][3]) / 2
        for i, k in enumerate(kaesten):
            if x_bereich is not None:
                if not (k[0] < x_bereich[1] and k[2] > x_bereich[0]):
                    continue
                treffer = k[1] <= my <= k[3]
            else:
                treffer = k[0] <= mx <= k[2] and k[1] <= my <= k[3]
            if treffer:
                while len(z) < 3:
                    z.append(None)
                z[2] = f"kasten{i}"
                break
    return zeilen


def _zelle(t):
    """Zellinhalt fuer eine Markdown-Tabelle entschaerfen."""
    t = (t or "").strip()
    # Zellen brechen um; ein Trennstrich am Umbruch gehoert zusammengefuegt
    # ("Gefaelligkeitsver-\nhaeltnis"), aber nur vor Kleinbuchstaben — sonst
    # wird aus "Verfahrens-\nund Formfehler" ein Wort.
    t = re.sub(r"(?<=[a-zäöüß])-\s*\n\s*(?=[a-zäöüß])", "", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = t.replace("|", "\\|")            # sonst reisst die Zelle die Spalte auf
    return re.sub(r"\s+", " ", t)


def tabellen_markdown(page):
    """[(y_oben, markdown, bbox)] fuer jede linierte Tabelle der Seite.

    `strategy="lines_strict"` verlangt echte Trennlinien. Die Alternative
    ("text", ueber Wortabstaende) hat auf einer Hemmer-Seite eine 74x6-Tabelle
    aus reinem Fliesstext erfunden — Fliesstext faelschlich zu tabellieren ist
    schlimmer, als eine Tabelle als Fliesstext stehen zu lassen.
    """
    import fitz
    H = page.rect.height or 1
    aus = []
    try:
        tabs = page.find_tables(strategy="lines_strict").tables
    except Exception:
        return aus
    for t in tabs:
        if t.row_count < 2 or t.col_count < 2:
            continue                      # 1xN ist ein umrandeter Kasten
        reihen = [[_zelle(c) for c in r] for r in t.extract()]
        # Gitterform allein genuegt nicht. Ein einzelner Kasten in einer
        # Uebersicht kommt als 3x2 mit einer gefuellten Zelle heraus — als
        # Tabelle formatiert waere das eine Erfindung. Darum leere Zeilen und
        # Spalten streichen und danach echte Zweidimensionalitaet verlangen:
        # mindestens zwei Zeilen mit je mindestens zwei gefuellten Zellen.
        if not reihen:
            continue
        spalten = [j for j in range(max(len(r) for r in reihen))
                   if any(j < len(r) and r[j] for r in reihen)]
        zeilen_nr = [i for i, r in enumerate(reihen) if any(r)]
        reihen = [[reihen[i][j] if j < len(reihen[i]) else "" for j in spalten]
                  for i in zeilen_nr]
        if (len(reihen) < 2 or len(reihen[0]) < 2
                or sum(1 for r in reihen if sum(1 for c in r if c) >= 2) < 2):
            continue

        # Fett je Zelle: der Text bleibt aus extract() (autoritativ), fett wird
        # nur als Ja/Nein aus den Span-Flags nachgetragen. Den Zelltext selbst
        # aus dem Clip zusammenzusetzen waere riskanter — Spans der Nachbarzelle
        # ragen in den Clip hinein und wuerden Inhalt verfaelschen.
        # Die Indizes zeigen auf die UNGEKUERZTE Tabelle: zeilen_nr/spalten
        # bilden die beschnittene Ansicht zurueck auf t.rows.
        for zi, i in enumerate(zeilen_nr):
            if i >= len(t.rows):
                break
            zellen = t.rows[i].cells or []
            for sj, j in enumerate(spalten):
                if j >= len(zellen) or not zellen[j] or not reihen[zi][sj]:
                    continue
                spans = [s for b in page.get_text("dict",
                                                  clip=fitz.Rect(zellen[j]))["blocks"]
                         if b.get("type") == 0
                         for ln in b["lines"] for s in ln["spans"] if s["text"].strip()]
                if spans and all(s.get("flags", 0) & 16
                                 or "bold" in s.get("font", "").lower() for s in spans):
                    reihen[zi][sj] = f"**{reihen[zi][sj]}**"
        breite = max(len(r) for r in reihen)
        reihen = [r + [""] * (breite - len(r)) for r in reihen]
        kopf = reihen[0]
        if not any(kopf):                 # Markdown braucht eine Kopfzeile
            kopf = [""] * breite
            koerper = reihen
        else:
            koerper = reihen[1:]
        md = ["| " + " | ".join(kopf) + " |",
              "|" + "|".join([" --- "] * breite) + "|"]
        md += ["| " + " | ".join(r) + " |" for r in koerper]
        aus.append((t.bbox[1] / H, "\n".join(md), t.bbox))
    return aus


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


def seiten_analysieren(pdf, dpi, nur_ocr=False):
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
    zusammenbau.laufend_setzen(laufende_zeilen(doc))
    seiten = []
    for i in range(doc.page_count):
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


def kacheln_senkrecht(png, steg):
    """Zweispalter am erkannten Steg trennen (nicht blind in der Mitte)."""
    from PIL import Image
    im = Image.open(png)
    w, h = im.size
    cut, ov = int(w * steg), int(w * OVERLAP)
    a = png.with_name(png.stem + "_L.png")
    b = png.with_name(png.stem + "_R.png")
    im.crop((0, 0, min(cut + ov, w), h)).save(a)
    im.crop((max(cut - ov, 0), 0, w, h)).save(b)
    return [a, b]


def kacheln_waagerecht(png, teile=2):
    """Dichte EINSPALTIGE Seite oben/unten trennen — ein Mittelschnitt laengs
    wuerde hier jede Zeile zerschneiden.

    Liefert (Pfad, y_oben, y_unten) mit den Schnittgrenzen als Anteil der
    Elternhoehe. Die Grenzen braucht der Aufrufer, um die kachelrelativen
    Modellkoordinaten zurueck ins Elternbild zu rechnen — ohne das laesst sich
    eine Kachel nicht noch einmal teilen, weil die Teilstuecke sonst alle bei
    y = 0 anfangen.
    """
    from PIL import Image
    im = Image.open(png)
    w, h = im.size
    ov = int(h * OVERLAP)
    aus = []
    for i in range(teile):
        y0 = max(int(h * i / teile) - ov, 0)
        y1 = min(int(h * (i + 1) / teile) + ov, h)
        p = png.with_name(f"{png.stem}_T{i+1}.png")
        im.crop((0, y0, w, y1)).save(p)
        aus.append((p, y0 / h, y1 / h))
    return aus


# --- Entgleiste Generierung -------------------------------------------------

# Ab wie vielen Wiederholungen desselben 5-Gramms die Ausgabe als Schleife
# gilt. Gemessen im 40-Seiten-Benchmark ziffernblind (siehe schleifenlaenge):
# gesunde Seiten kommen auf hoechstens 6, die entgleisten auf 64, 106, 275 und
# 1920. Dazwischen liegt so viel Luft, dass der genaue Wert kaum zaehlt.
SCHLEIFE_AB = 8
# Wieviele Wiederholungen es beim ZIFFERNBLINDEN Zusammenstreichen braucht.
# Deutlich hoeher als SCHLEIFE_AB, weil ziffernblind auch echte Aufzaehlungen
# ("§§ 823, 826, 831, 840", Fussnotennummern, Tabellenspalten) wie eine
# Wiederholung aussehen. Ein Zaehler laeuft in die Hunderte, eine Normenkette
# nicht ueber ein Dutzend.
ZAEHLER_AB = 20
# Zeichen je 1000 Tintenpixel bei 150 dpi, Median ueber dieselben 40 Seiten
# (Spanne 10,5–29,6). Grobe Schaetzung — aber die einzige, die ohne Textlayer
# auskommt, und damit die einzige, die auf echten Scans ueberhaupt greift.
# Liegt ein Textlayer vor, wird der Faktor daraus je Seite neu bestimmt und
# die Streuung faellt weg.
ZEICHEN_JE_TINTE = 18.8 / 1000
# Erlaubter Korridor um die erwartete Zeichenzahl — zwei Fassungen, weil der
# Massstab zwei sehr verschiedene Guetegrade hat.
#
#   GEEICHT   Der Faktor stammt aus dem Textlayer DERSELBEN Seite. Gemessen
#             ueber die 40 Benchmarkseiten: gesunde Ausgaben liegen bei
#             0,87–1,18 der Textlayerlaenge, die beiden Abbrueche bei 0,23 und
#             0,75, die vier Schleifen bei 1,98–5,93. 0,80 trennt knapp, aber
#             sauber; ein Fehlalarm kostet ohnehin nur Rechenzeit, weil
#             `_guete` den Neuversuch verwirft, wenn er nichts bringt.
#   GROB      Der Faktor ist das Korpusmittel. Dessen eigene Streuung betraegt
#             0,56–1,58 (10,5–29,6 Zeichen je 1000 Tintenpixel), der Korridor
#             muss sie enthalten — sonst schlaegt er auf jeder zweiten
#             Scanseite an. Er faengt damit nur die groben Faelle. Das ist der
#             Preis dafuer, auf einem echten Scan ueberhaupt etwas zu messen.
KORRIDOR_GEEICHT = (0.80, 2.2)
KORRIDOR_GROB = (0.45, 2.6)


# Token-Budget je Kachel. Ein fester Deckel von 8192 ist auf gesunden Seiten
# nie noetig — eine volle A4-Seite Gutachten hat rund 5000 Zeichen, also etwa
# 1700 Token — kostet aber auf jeder entgleisten Seite die volle Rechenzeit:
# eine Schleife hoert von selbst nicht auf, sie laeuft bis zum Deckel. Gemessen
# auf dem M1: ~8 min fuer eine einzige Kachel.
#
# Die Seite verraet dagegen, wie viel Text auf ihr steht — dieselbe
# Tintenschaetzung, die schon die Entgleisung erkennt. Der Zuschlag ist
# absichtlich grosszuegig: wird das Budget doch zu knapp, sieht `entgleist`
# einen Abbruch und die Kachel wird feiner geschnitten neu gerechnet, wobei
# jedes Teilstueck sein eigenes Budget bekommt. Der Fehler heilt sich also,
# waehrend ein zu hohes Budget nur Zeit verbrennt.
TOKEN_JE_ZEICHEN = 1 / 2.2        # deutsche Prosa, mit Reserve
TOKEN_MIN, TOKEN_MAX = 1024, 8192


def _tokenbudget(erwartet, grosszuegig=1.8):
    if not erwartet or erwartet < 300:
        return TOKEN_MAX
    return int(min(max(erwartet * TOKEN_JE_ZEICHEN * grosszuegig, TOKEN_MIN),
                   TOKEN_MAX))


def _tintenmenge(png, dpi=150):
    """Tintenpixel des Bildes, auf 150 dpi normiert.

    Die Normierung ist noetig, weil die Pixelzahl mit dem Quadrat der
    Aufloesung waechst — ohne sie haengt jede Schwelle an --dpi.
    """
    import numpy as np
    from PIL import Image
    g = np.asarray(Image.open(png).convert("L"))
    return float((g < 160).sum()) * (150.0 / max(dpi, 1)) ** 2


def schleifenlaenge(text, n=5):
    """Wie oft kommt das haeufigste n-Gramm vor?

    Das Modell entgleist auf zwei Weisen, und das ist die eine: es wiederholt
    eine Wortfolge, bis das Token-Budget erschoepft ist. Wortweise gezaehlt
    statt zeilenweise, weil die Wiederholung nicht an Zeilengrenzen haengt.

    Ziffern werden vorher zu `#` eingeebnet. Sonst entgeht die haeufigste
    Bauform ueberhaupt — der aufsteigende Zaehler. `UNIREP_KK_ZR_LH_07_11`
    S. 8 lieferte "(1982) (1983) (1984) …" ueber 2000 Zeichen; woertlich
    gezaehlt ist dort jedes 5-Gramm einmalig (Wert 1), ziffernblind kommt
    dasselbe 275-mal. Gegenprobe ueber alle 40 Benchmark-Seiten: keine zweite
    Seite kommt ziffernblind ueber 6, die Schwelle liegt bei 8.
    """
    w = [re.sub(r"\d+", "#", x) for x in re.findall(r"\S+", text)]
    if len(w) < 2 * n:
        return 0
    haeufig = Counter(tuple(w[i:i + n]) for i in range(len(w) - n + 1))
    return haeufig.most_common(1)[0][1]


def entgleist(text, erwartet=None, geeicht=False):
    """(Grund, Kennzahl) — oder (None, 0.0), wenn die Ausgabe plausibel ist.

    Zwei Signale, beide ohne zweites Modell:

      Schleife   wiederholtes n-Gramm. Faengt die drei Faelle, in denen
                 dieselbe Zeile hundertfach kommt.
      Laenge     Abstand zur erwarteten Zeichenzahl. Faengt den vierten
                 Schleifenfall — einen aufsteigenden Zaehler, dessen n-Gramme
                 alle verschieden sind — und beide Abbrueche.
    """
    s = schleifenlaenge(text)
    if s >= SCHLEIFE_AB:
        return "Schleife", float(s)
    if erwartet and erwartet >= 300:
        unten, oben = KORRIDOR_GEEICHT if geeicht else KORRIDOR_GROB
        q = len(text) / erwartet
        if q > oben:
            return "zu lang", q
        if q < unten:
            return "Abbruch", q
    return None, 0.0


def _guete(text, erwartet):
    """Wie glaubwuerdig ist diese Ausgabe? Kleiner ist besser.

    Entscheidet zwischen erstem Versuch und Neuversuch. Eine Schleife ist ein
    harter Malus — ein schleifenfreier Lauf gewinnt immer, egal wie kurz. Sonst
    zaehlt der Abstand zur erwarteten Menge, und ohne Massstab die Menge selbst:
    beim Abbruch ist mehr Text immer der bessere Text.
    """
    strafe = 10.0 if schleifenlaenge(text) >= SCHLEIFE_AB else 0.0
    if erwartet and erwartet >= 300:
        return strafe + abs(math.log(max(len(text), 1) / erwartet))
    return strafe - min(len(text), 20000) / 20000.0


def _lauf_kuerzen(stuecke, mindest, behalten=2, schluessel=None):
    """Aufeinanderfolgende Wiederholungen einer Periode zusammenstreichen.

    `schluessel` ist eine gleich lange Liste von Vergleichswerten. Damit laesst
    sich unscharf vergleichen (z.B. ziffernblind) und trotzdem das Original
    ausgeben: stehen bleiben die ersten `behalten` ECHTEN Vorkommen, nicht
    `behalten` Kopien des ersten — bei einem Zaehler ist "(1982) (1983) …" die
    aussagekraeftige Spur, "(1982) (1982)" waere eine erfundene.
    """
    k = stuecke if schluessel is None else schluessel
    aus, i = [], 0
    while i < len(stuecke):
        for p in range(1, 5):                     # Periodenlaenge 1–4
            if i + p > len(stuecke):
                continue
            n = 1
            while k[i + n * p:i + (n + 1) * p] == k[i:i + p]:
                n += 1
            if n >= mindest:
                aus += stuecke[i:i + behalten * p]
                i += n * p
                break
        else:
            aus.append(stuecke[i])
            i += 1
    return aus


def schleife_kuerzen(zeilen, mindest=3):
    """Stehengebliebene Wiederholungen zusammenstreichen.

    Greift nur, wenn der Neuversuch die Schleife NICHT beseitigt hat. Dann ist
    die Seite ohnehin unvollstaendig — aber ein Block, in dem dieselbe
    Wortfolge sechzig- oder zweitausendfach steht, macht sie zusaetzlich
    unlesbar und ueberschwemmt jede Volltextsuche. Zwei Durchlaeufe: erst
    innerhalb der Zeile ("V. V. V. V. …"), dann ueber Zeilen hinweg (dieselbe
    Fussnote hundertfach).

    Stehen bleiben zwei Vorkommen. Das ist Absicht: die Stelle soll im Text
    sichtbar bleiben, damit klar ist, dass hier etwas schiefging — stilles
    Glaetten waere derselbe Fehler wie stilles Loeschen.

    Der Zaehler ("(1982) (1983) (1984) …") braucht einen eigenen, ziffernblinden
    Durchgang, weil dort kein Wort dem anderen gleicht. Er laeuft mit deutlich
    hoeherer Schwelle (ZAEHLER_AB statt `mindest`), denn ziffernblind sieht
    "§§ 823, 826, 831, 840" wie eine Wiederholung aus — eine Aufzaehlung von
    vier Normen soll keine werden.
    """
    gekuerzt = []
    for z in zeilen:
        w = z[0].split(" ")
        if len(w) >= 3 * mindest:
            neu = _lauf_kuerzen(w, mindest)
            if len(neu) >= 3 * ZAEHLER_AB:
                neu = _lauf_kuerzen(
                    neu, ZAEHLER_AB,
                    schluessel=[re.sub(r"\d+", "#", x) for x in neu])
            neu = " ".join(neu)
            if neu != z[0]:
                z = [neu] + list(z[1:])
        gekuerzt.append(z)
    schluessel = [re.sub(r"[^0-9a-zäöüß]+", "", z[0].lower()) for z in gekuerzt]
    behalten, i = [], 0
    while i < len(gekuerzt):
        n = 1
        while (i + n < len(gekuerzt) and schluessel[i + n] == schluessel[i]
               and len(schluessel[i]) >= 8):
            n += 1
        behalten += gekuerzt[i:i + (2 if n >= mindest else n)]
        i += n
    return behalten


def _nahtworte(zeilen):
    """(normalisiertes Wort, Zeilenindex, Wortindex) fuer den Nahtvergleich."""
    aus = []
    for i, z in enumerate(zeilen):
        for j, w in enumerate(z[0].split()):
            k = re.sub(r"[^0-9a-zäöüß]+", "", w.lower())
            if k:
                aus.append((k, i, j))
    return aus


def ueberlappung_kuerzen(vorhanden, neu, fenster=150, mindest=6):
    """Doppelten Text an der Kachelnaht abschneiden — wortweise.

    Kacheln ueberlappen um OVERLAP, damit der Schnitt keine Zeile zerreisst.
    Der Preis ist, dass das Ueberlappungsband zweimal erkannt wird.

    Zeilenweise zu vergleichen war falsch und hat im 40-Seiten-Lauf echten Text
    geloescht: `parse_zeilen` fasst zu Absaetzen zusammen, und der erste Absatz
    der unteren Kachel FAENGT mit dem Ueberlappungsband an, traegt aber den
    ganzen Rest der Seite mit sich. Auf `UNIREP_KK_ZR_LH_30_01` S. 9 wurde so
    ein Drittel der Seite still entfernt (98,6 % → 58,0 % Wortgenauigkeit) —
    genau die Sorte Fehler, die ohne Original niemandem auffaellt.

    Gesucht wird darum das laengste Wortstueck, das zugleich Ende des
    Vorhandenen und Anfang des Neuen ist; abgeschnitten wird nur dieses Stueck.
    Findet sich keins — die beiden Kacheln lesen dieselbe Zeile selten
    wortgleich —, bleibt alles stehen. Das ist Absicht: eine sichtbare
    Dopplung ist der bessere Fehler als ein unsichtbarer Verlust.
    """
    if not vorhanden or not neu:
        return neu
    schwanz = [w for w, _, _ in _nahtworte(vorhanden)][-fenster:]
    kopf = _nahtworte(neu)[:fenster]
    kopfworte = [w for w, _, _ in kopf]
    for k in range(min(len(schwanz), len(kopfworte)), mindest - 1, -1):
        if schwanz[-k:] != kopfworte[:k]:
            continue
        if sum(len(w) for w in kopfworte[:k]) < 30:
            continue                  # zu wenig Substanz, z.B. "aa) bb) cc)"
        _, zeile, wort = kopf[k - 1]
        aus = []
        for i, z in enumerate(neu):
            if i < zeile:
                continue
            if i == zeile:
                rest = " ".join(z[0].split()[wort + 1:])
                if not rest:
                    continue
                z = [rest] + list(z[1:])
            aus.append(z)
        return aus
    return neu


def kachel_zeilen(png, ocr, mit_fett, faktor, dpi, geeicht=False,
                  tiefe=0, max_tiefe=1):
    """Eine Kachel erkennen — und bei entgleister Generierung neu rechnen.

    Der Benchmark zeigt: auf 85 % der Seiten liegt der OCR-Pfad bei 99 %
    Wortgenauigkeit, die schlechten Gesamtzahlen kommen von 15 % Seiten, auf
    denen das Modell in eine Schleife laeuft oder mitten im Satz abbricht. Das
    ist kein Lesefehler, sondern ein Fehler der Generierung — dieselbe Kachel
    kleiner geschnitten braucht kuerzere Laeufe und kommt dann meist durch.

    Die Auswahl zwischen erstem Versuch und Neuversuch faellt an `_guete`,
    nicht am Gefuehl. Damit kann der Neuversuch nichts verschlimmern: liefert
    er weniger als der erste, wird er verworfen.

    `faktor` ist Zeichen je Tintenpixel. Er kommt entweder aus dem Textlayer
    derselben Seite (dann ist er exakt) oder aus dem Korpusmittel (dann grob).
    Die Erwartung wird je Kachel aus deren eigener Tinte berechnet, nicht durch
    Teilen der Seitenerwartung — Kacheln tragen unterschiedlich viel Text.
    """
    erwartet = _tintenmenge(png, dpi) * faktor if faktor else None
    zeilen = parse_zeilen(ocr(png, _tokenbudget(erwartet)))
    if mit_fett:
        zeilen = fett_markieren(zeilen, png)
    text = "\n".join(z[0] for z in zeilen)
    grund, kennzahl = entgleist(text, erwartet, geeicht)
    if grund is None:
        return zeilen, []
    marke = (f"{grund} {kennzahl:.0f}×" if grund == "Schleife"
             else f"{grund} {kennzahl:.0%}")
    if tiefe >= max_tiefe:
        if grund == "Schleife":
            vorher = len(zeilen)
            zeilen = schleife_kuerzen(zeilen)
            return zeilen, [f"{png.stem}: {marke}, nicht behoben — "
                            f"Wiederholung gekuerzt ({vorher} → "
                            f"{len(zeilen)} Zeilen)"]
        return zeilen, [f"{png.stem}: {marke}, nicht behoben"]
    neu, spur = [], []
    for teil, oben, unten in kacheln_waagerecht(png, 2):
        z, s = kachel_zeilen(teil, ocr, mit_fett, faktor, dpi, geeicht,
                             tiefe + 1, max_tiefe)
        spur += s
        hoch = unten - oben
        for e in z:            # kachelrelatives y zurueck ins Elternbild
            if e[1]:
                e[1] = (e[1][0], int((oben + e[1][1] / 1000 * hoch) * 1000),
                        e[1][2], int((oben + e[1][3] / 1000 * hoch) * 1000))
        neu += ueberlappung_kuerzen(neu, z)
    neu_text = "\n".join(e[0] for e in neu)
    if _guete(neu_text, erwartet) < _guete(text, erwartet):
        gewaehlt, notiz = neu, (f"{marke} → neu gekachelt, "
                                f"{len(text)} → {len(neu_text)} Z.")
    else:
        gewaehlt, notiz = zeilen, (f"{marke} → Neuversuch verworfen "
                                   f"({len(neu_text)} Z. waren nicht besser)")
    # Auch der gewaehlte Lauf kann noch eine Schleife enthalten — dann ist die
    # Seite nicht zu retten, aber sie muss wenigstens lesbar bleiben.
    if schleifenlaenge("\n".join(e[0] for e in gewaehlt)) >= SCHLEIFE_AB:
        vorher = len(gewaehlt)
        gewaehlt = schleife_kuerzen(gewaehlt)
        notiz += f"; Wiederholung gekuerzt ({vorher} → {len(gewaehlt)} Zeilen)"
    return gewaehlt, spur + [f"{png.stem}: {notiz}"]


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
    global TMP
    TMP = OUT / f"_tmp-{pdf.stem}"
    TMP.mkdir(parents=True, exist_ok=True)   # Zwischenbilder bleiben im Bench
    a.out.mkdir(parents=True, exist_ok=True)

    print(f"Analysiere {pdf.name} (Scanseiten @ {a.dpi} dpi) ...")
    seiten = seiten_analysieren(pdf, a.dpi, a.nur_ocr)
    n_ocr = sum(1 for s in seiten if s[1] is not None)
    print(f"   {len(seiten)} Seiten — {len(seiten)-n_ocr} aus dem Textlayer, "
          f"{n_ocr} durch das Modell\n")

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

    def ablegen(nr, absaetze, diagramm, dt, chars, quelle, weg, spur=(),
                marker_zusatz=None):
        nonlocal n_diag
        # %% %% ist Obsidians eigene Kommentarsyntax und bleibt auch in der
        # Live-Vorschau unsichtbar; <!-- --> wird dort angezeigt.
        # marker_zusatz ist Teil der Marker-Grammatik (docs/ocr-vorschau.md):
        # die Review-Ansicht haengt Herkunfts-Badges und Layout-Info daran.
        # Diagramm sticht: eine Seite, die als Bild eingebettet wird, ist
        # keine Textseite, egal woher ihr Text stammt.
        if diagramm:
            marker_zusatz = "diagramm"
        kopf = f"%% S. {nr} | {marker_zusatz} %%\n\n"
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
        for zeile in spur:
            print(f"     ⚠ {zeile}")

    dump = []

    for nr, png, chars, art, steg, textlayer, kaesten, diagramm in seiten:
        t = time.perf_counter()
        diagramm = diagramm or nr in erzwungen
        if textlayer is not None:
            # Exakter Text, exakte Koordinaten, exaktes Fett — keine Inferenz.
            zeilen = spalten_trennen(kaesten_zuordnen(textlayer, kaesten))
            dump.append({"seite": nr, "quelle": "textlayer", "zeilen": zeilen})
            absaetze = zusammenfuegen(zeilen)
            ablegen(nr, absaetze, diagramm, time.perf_counter() - t, chars,
                    "Textlayer, ohne Modell",
                    getattr(zusammenfuegen, "verworfen", []),
                    "textlayer")
            continue
        # Kachelung ist eine LAYOUT-Entscheidung. Ein Laengsschnitt darf nur auf
        # echten Zweispaltern fallen; eine dichte einspaltige Seite wird
        # oben/unten getrennt, sonst zerschneidet man jede Zeile.
        if diagramm and a.diagramm_nur_bild:
            ablegen(nr, [], True, time.perf_counter() - t, chars, "", [],
                    "ocr")
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
        ablegen(nr, absaetze, diagramm, time.perf_counter() - t, chars,
                f"{art}, {modus}", getattr(zusammenfuegen, "verworfen", []),
                spur, f"ocr | {art}, {modus}")

    ges = time.perf_counter() - t_ges
    # Welche Seiten exakt sind und welche erkannt, muss in der Datei stehen:
    # nur bei den OCR-Seiten ist ein Rueckgriff aufs Original noetig.
    kopf = (f"---\ntitel: {pdf.stem}\nquelle-pdf: {pdf}\n"
            f"seiten: {len(seiten)}\n"
            f"seiten-textlayer: {len(seiten) - n_ocr}\nseiten-ocr: {n_ocr}\n"
            + (f"seiten-diagramm: {n_diag}\n" if n_diag else "")
            # Auffaellig gewordene Seiten benennen, nicht verschweigen: auf
            # ihnen ist die Ausgabe auch nach dem Neuversuch unsicher.
            + (f"seiten-entgleist: {n_entgleist}\n" if n_entgleist else "")
            + (f"ocr-modell: {MODEL}\n" if n_ocr else "")
            + f"ocr-datum: {date.today().isoformat()}\n---\n")
    # Anklickbarer Rueckgriff aufs Original. Bei OCR-Seiten ist er Pflicht, nicht
    # Bequemlichkeit: Wortfehler sind nicht mechanisch korrigierbar, ohne die
    # Quelle also unauffindbar.
    quelle = f"Quelle: [[{pdf.as_posix()}]]\n"
    ziel = a.out / f"{pdf.stem}.md"
    ziel.write_text(kopf + "\n" + quelle + "\n" + "\n\n".join(md) + "\n",
                    encoding="utf-8")
    if a.zeilen_dump:
        a.zeilen_dump.write_text(json.dumps(dump, ensure_ascii=False),
                                 encoding="utf-8")
        print(f"→ {a.zeilen_dump} ({len(dump)} Seiten)")
    for tmp in TMP.glob("_seite*.png"):     # Zwischenbilder nicht liegenlassen
        tmp.unlink()
    TMP.rmdir()
    print(f"\n{ges:.1f} s gesamt ({ges/len(seiten):.1f} s/Seite)\n→ {ziel}")


if __name__ == "__main__":
    main()
