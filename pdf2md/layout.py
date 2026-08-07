#!/usr/bin/env python3
"""Seitengeometrie: Spalten, Kaesten, Tabellen, Diagramme (Stufe 2, Issue #8).

Von pdf2md.py abgetrennt. Arbeitet auf fitz-Page-Objekten und ist damit nicht
headless testbar — die reinen Funktionen auf Zeilenlisten liegen in
zusammenbau.py. Importiert nur aus zusammenbau (eine Richtung, kein Zyklus).
"""
import re
import statistics

from zusammenbau import AUFZAEHLUNG, KEIN_JOIN, ist_boilerplate, saeubern

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


# --- Seitenprofil & Layout-Erkennung ----------------------------------------

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
