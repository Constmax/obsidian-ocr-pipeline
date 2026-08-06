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
from layout import *
from layout import (_bloecke, _cluster, _entdoppeln, _laengster_lauf,
                    _steg, _tintensteg, _verschmelzen, _zeilenanfang, _zelle)

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
