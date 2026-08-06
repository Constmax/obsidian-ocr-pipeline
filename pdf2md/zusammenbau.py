#!/usr/bin/env python3
"""Markdown-Zusammenbau: reine Funktionen auf Zeilenlisten (Stufe 2, Issue #8).

Von pdf2md.py abgetrennt, damit die Schicht, die sich am haeufigsten aendert,
ohne MLX, fitz und Vault-Bestand testbar ist (pdf2md/test). Importiert nichts
aus den Geschwistermodulen — die Abhaengigkeit laeuft nur in diese Richtung:

    pdf2md.py (CLI)  →  layout.py, ocr.py  →  zusammenbau.py

Die schweren Importe (fitz, numpy, PIL) liegen in allen Modulen funktionslokal.
"""
import re
import statistics

LOC = re.compile(r"<\|LOC_(\d+)\|>")
# --- Nachbearbeitung -------------------------------------------------------

# Woerter, die nach einem Zeilenendbindestrich NICHT angeklebt werden duerfen
# ("Verfahrens- und Formfehler" ist kein getrenntes Wort).
KEIN_JOIN = re.compile(r"^(und|oder|bzw|sowie|als|wie|bis|von|zu|im|in)\b", re.I)
# --- Hemmer-Boilerplate ----------------------------------------------------

STAEDTE = ("Augsburg Bayreuth Berlin Potsdam Bielefeld Bochum Bonn Bremen "
           "Düsseldorf Erlangen Frankfurt Freiburg Göttingen Greifswald Halle "
           "Hamburg Hannover Heidelberg Jena Kiel Köln Konstanz Leipzig "
           "Lüneburg Mainz Mannheim Marburg Gießen München Münster Nürnberg "
           "Osnabrück Passau Regensburg Saarbrücken Trier Tübingen Stuttgart "
           "Wiesbaden Würzburg Rostock Dresden").split()

STADT_FRAGMENT = re.compile(
    r"^(?:" + "|".join(STAEDTE) + r")\s*[-–]\s*"
    r"(?:[A-ZÄÖÜ][A-Za-zäöüß]{0,12}\.?)?$")

BOILERPLATE = [
    re.compile(r"^Juristisches\s+Repetitorium"),
    re.compile(r"^hemmer\s*$", re.I),
    re.compile(r"^Hauptkurs\s*/"),
    re.compile(r"^h\s*/\s*w\s*/\s*t\b"),                  # Fusszeile
    re.compile(r"^\\lambda\s*/\s*\\omega"),               # dieselbe, als LaTeX verlesen
    re.compile(r"^[–\-—]\s*\d+\s*[–\-—]?\s*$"),           # "– 1 –"
    re.compile(r"^\d{1,3}\s*[-–]\s*[Il1]\s*$"),           # "26-I"
]


# Schwache Signale, die nur in der Kopf-/Fusszone als Boilerplate gelten.
# Dort ist ein Fehlalarm harmlos, im Textkoerper waere er Inhaltsverlust.
ZONEN_SIGNAL = [
    # VERANKERT, nicht als Teilstring: Boilerplate ist die alleinstehende
    # Logo-Zeile. Ein Teilstring-Treffer wuerde "len Sie HEMMER/WÜST, BGB-AT I,
    # Rn. 56 ff." loeschen — eine echte Literaturangabe.
    re.compile(r"^he[mn]+er\s*[.,:]?$", re.I),
    re.compile(r"^\W*(Juristisches\s*)?Repetitorium\W*$", re.I),
    re.compile(r"^(BGB|StGB|StR|ZR|OeR|ÖR)[\s-]*(AT|BT)?\s*$"),
    re.compile(r"(Lösung|Sachverhalte?|Übersicht)\s*[-–]\s*Seite", re.I),
    re.compile(r"^Fall\s*\d*\s*[-–]?\s*L[äöa]?"),      # "Fall 3 - Lä" (abgeschnitten)
    re.compile(r"^\s*Seite\s*\d+\s*$", re.I),
]


# Wird je Dokument aus laufende_zeilen() gefuellt.
LAUFEND = set()


def laufend_setzen(zeilen):
    """Laufende Kopf-/Fusszeilen des Dokuments setzen (aus laufende_zeilen()).

    In-place statt Umbinden: `LAUFEND` ist Modul-Global, gelesen wird es in
    ist_boilerplate(). Eine Zuweisung von aussen wuerde nur den Namen im
    aufrufenden Modul umbinden und die Unterdrueckung still ausfallen lassen.
    """
    LAUFEND.clear()
    LAUFEND.update(zeilen)

def ist_boilerplate(text, y=None, kopf=70, fuss=950):
    """Hemmer-Boilerplate erkennen.

    `y` ist die normierte Zeilenposition (0–1000). In der Kopf- und Fusszone
    greifen zusaetzlich schwache Signale (einzelner Ortsname, verlesenes
    "hemmer", abgeschnittene Seitenangabe) — die Kopfzeile laeuft ueber die
    volle Blattbreite, wird vom Kachelschnitt zerteilt und kommt als
    unvorhersehbares Fragment an, gegen das reine Textmuster nicht ankommen.
    """
    # Fett-Marker vorher entfernen: fett_markieren() laeuft frueher, und
    # "**BGB-AT**" wuerde sonst an keinem ^-verankerten Muster mehr greifen.
    t = text.strip().strip("*").strip()
    if not t:
        return False
    # Nachgewiesene laufende Kopf-/Fusszeile: exakter Treffer aus einer Zeile,
    # die auf mehreren Seiten in derselben Zone stand. Staerkeres Indiz als
    # jedes Textmuster, weil es aus dem Dokument selbst kommt.
    # Leerraum GLEICH normieren wie beim Sammeln: die Kopfzeile enthaelt
    # "Fall 12  |  Begleitskript" mit doppelten Leerzeichen, der gesammelte
    # Vergleichswert hat einfache.
    if LAUFEND and re.sub(r"\s+", " ", re.sub(r"\*", "", t)).strip() in LAUFEND:
        return True
    if any(p.search(t) for p in BOILERPLATE):
        return True
    if t.count(" - ") >= 2 and sum(1 for s in STAEDTE if s in t) >= 2:
        return True
    # Die Staedteliste im Briefkopf steht fuenfzeilig am rechten Blattrand und
    # wird vom Kachelschnitt zerhackt: "Mainz - Man", "Passau - Reg". Die
    # unteren Zeilen liegen ausserhalb der Kopfzone und blieben deshalb stehen.
    # Streng verankert — Stadtname am Zeilenanfang, dahinter hoechstens ein
    # angeschnittenes Wort — damit kein Zitat wie "OLG München - Urteil vom …"
    # hineinlaeuft.
    if STADT_FRAGMENT.match(t):
        return True

    # Kurze Zeilen: Laengengrenze ersetzt die Zonenpruefung. Notwendig, weil das
    # Modell fuer manche Seiten gar keine Koordinaten liefert — dann ist y None.
    # Der Laengenschutz bewahrt echten Inhalt: "HEMMER/Wuest, BGB-AT I, Rn. 56
    # ff." und "jurisbyhemmer" stehen in langen Saetzen und bleiben erhalten.
    if len(t) <= 45 and any(p.search(t) for p in ZONEN_SIGNAL):
        return True

    # Blosse Seitenzahl. Stand bisher als eigener Absatz in der Ausgabe und
    # zerlegte bei Doppelseiten zusaetzlich das Spalten-Histogramm. Eigene,
    # weitere Zone: die Skripte setzen sie oberhalb der Fusszone, in der die
    # Textsignale greifen. 905 statt 920 ist gemessen — auf den Scanseiten von
    # "Strafrecht AT VI" liegt die Seitenzahl bei y = 911…938, vier von zwoelf
    # blieben deshalb stehen. Was zwischen 905 und 920 sonst noch nackt
    # herumsteht, sind nach dem Anbinden der Fussnotennummern nur noch
    # Seitenzahlen (54 im Bestand, geprueft).
    if (y is not None and (y <= 80 or y >= 905)
            and re.fullmatch(r"\d{1,4}", t)):
        return True

    in_zone = y is not None and (y <= kopf or y >= fuss)
    if in_zone:
        if any(p.search(t) for p in ZONEN_SIGNAL):
            return True
        # Fragmentierte Staedteliste: "Augsburg - Ba", "Erlangen - Fr"
        if len(t) <= 40 and sum(1 for s in STAEDTE if s in t) >= 1 and "-" in t:
            return True
    return False
# Das Modell gibt Pfeile mal als Zeichen, mal als LaTeX aus. Ein Muster je
# Befehl statt einer Liste je Schreibweise: die Dollarzeichen sind optional,
# weil beide Formen vorkommen.
PFEILE = {"rightarrow": "→", "Rightarrow": "⇒", "leftarrow": "←",
          "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
          "downarrow": "↓", "Downarrow": "⇩", "uparrow": "↑", "to": "→",
          "Longrightarrow": "⟹", "mapsto": "↦"}

LATEX = [
    (re.compile(r"\$?\\(" + "|".join(PFEILE) + r")\$?(?![A-Za-z])"),
     lambda m: PFEILE[m.group(1)]),
    (re.compile(r"\\\(\\underline\{\\text\{(.*?)\}\}\\\)"), r"\1"),
    (re.compile(r"\\underline\{(.*?)\}"), r"\1"),
    (re.compile(r"\\text\{(.*?)\}"), r"\1"),
    (re.compile(r"\\\(|\\\)"), ""),
    (re.compile(r"\^\{(\d{1,2})\}"), r"[^\1]"),      # Fussnotenzeichen → Obsidian
    (re.compile(r"\$\^\{(\d{1,2})\}\$"), r"[^\1]"),
]
# Fussnotendefinition am Seitenfuss: "1 Vgl. BGH, …" / "2 So auch MüKo-BGB, …"
# Die Klammer gehoert dazu: "4 (= Wiederbeschaffungswert abzüglich Restwert)"
# blieb sonst im Text der vorangehenden Fussnote stecken, und der Verweis im
# Fliesstext fand keine Definition.
FN_ANFANG = r"[A-ZÄÖÜ„»§(]"
FN_DEF = re.compile(r"^(\d{1,2})\s+(?=" + FN_ANFANG + r")(.+)$")
def fussnoten_obsidian(absaetze):
    """Fussnoten in Obsidian-Syntax bringen: [^n] im Text, [^n]: am Blockende.

    Definitionen werden nur dann als solche gewertet, wenn die Nummer klein und
    aufsteigend ist — sonst wuerde jede mit einer Zahl beginnende Zeile
    (Gliederungspunkte, Betraege) falsch erkannt.
    """
    defs, rest = {}, []
    ist_tabelle = lambda p: p.lstrip().startswith("|")
    for p in absaetze:
        if ist_tabelle(p):
            rest.append(p)
            continue
        # Fett-Marker stoeren die Nummernerkennung ("**7 Vgl. Grueneberg …**");
        # bei einer Fussnotendefinition ist die Auszeichnung ohnehin belanglos.
        p = re.sub(r"\*\*(.+?)\*\*", r"\1", p) if FN_DEF.match(p.strip("* ")) else p
        # Mehrere Definitionen koennen in einer Zeile stehen: "2 … 3 …"
        m = FN_DEF.match(p.strip())
        if m and int(m.group(1)) <= 99:
            teile = re.split(r"(?<=[.\s])(?=\d{1,2}\s+" + FN_ANFANG + ")", p.strip())
            erkannt = False
            for t in teile:
                mm = FN_DEF.match(t.strip())
                if mm:
                    # Anhaengen statt ueberschreiben: auf Doppelseiten stehen
                    # zwei Fussnotenbloecke nebeneinander und dieselbe Nummer
                    # kann zweimal auftreten. Ueberschreiben loeschte den
                    # ersten Text ersatzlos — genau der Fehler, der ohne das
                    # Original nicht mehr auffindbar ist.
                    k = int(mm.group(1))
                    defs[k] = (defs[k] + " " if k in defs else "") \
                        + mm.group(2).strip()
                    erkannt = True
            if erkannt:
                continue
        rest.append(p)

    if not defs:
        return rest

    # Inline-Marker: "entbehrlich.1" → "entbehrlich.[^1]"
    #
    # Entscheidend ist das FEHLENDE Leerzeichen: ein hochgestelltes
    # Fussnotenzeichen klebt am Wort ("entbehrlich.1", "Geschaeftspartners4"),
    # waehrend Bestandteile von Normzitaten immer eines haben ("S. 1",
    # "Alt. 2", "Rn. 9"). Ohne diese Unterscheidung werden aus Normzitaten
    # Fussnoten — und ein verfaelschtes Zitat ist der teuerste Fehler ueberhaupt.
    ZITAT_VOR = re.compile(
        r"(§+|Art\.|Abs\.|S\.|Satz|Alt\.|Nr\.|Rn\.|Rz\.|Hs\.|Halbs\.|Var\.|"
        r"lit\.|Buchst\.|Seite|Fall|Teil|Rspr\.|Anm\.)\s*$")
    nums = sorted(defs)

    def markiere(s):
        for n in nums:
            for m in re.finditer(rf"(?<=[a-zäöüßA-ZÄÖÜ)\].,;:]){n}(?![\d\]])", s):
                if ZITAT_VOR.search(s[:m.start()]):
                    continue
                s = s[:m.start()] + f"[^{n}]" + s[m.end():]
                break
        return s
    rest = [p if ist_tabelle(p) else markiere(p) for p in rest]
    rest += [""] + [f"[^{n}]: {defs[n]}" for n in nums]
    return rest
# Word setzt Aufzaehlungszeichen und Pfeile aus Symbolfonts. Deren ToUnicode
# zeigt in den Private-Use-Bereich (U+F000 + Fontcode) — Obsidian rendert dort
# ein Tofu-Kaestchen. Die Tabelle deckt alle 441 Vorkommen im Bestand ab.
PUA = {
    "\uf0f0": "\u21e8",   # Wingdings 0xF0  Schattenpfeil nach rechts
    "\uf0e0": "\u21e8",   # Wingdings 0xE0  Blockpfeil nach rechts
    "\uf0d8": "\u27a2",   # Wingdings 0xD8  Pfeilspitze als Aufzaehlungszeichen
    "\uf0fc": "\u2714",   # Wingdings 0xFC  Haken
    "\uf0b7": "\u2022",   # Symbol    0xB7  Punkt
    "\uf020": " ",        # Symbol    0x20  Leerzeichen
}
PUA_VON, PUA_BIS = "\ue000", "\uf8ff"
def entpua(s):
    if not any(PUA_VON <= c <= PUA_BIS for c in s):
        return s
    # Unbekannte Symbolglyphe: \u25aa ist der kleinste gemeinsame Nenner. Ein
    # konkretes Zeichen zu raten waere falsch, ein Tofu-Kaestchen aber auch.
    return "".join(PUA.get(c, "\u25aa") if PUA_VON <= c <= PUA_BIS else c
                   for c in s)
def saeubern(s):
    s = entpua(s)
    for pat, rep in LATEX:
        s = pat.sub(rep, s)
    # $ statt § — nur wenn eine Zahl folgt, sonst bleibt es ein Dollarzeichen
    s = re.sub(r"\$\s*(?=\d)", "§ ", s)
    s = re.sub(r"§\s*§\s*", "§§ ", s)                 # "§ § 929" → "§§ 929"
    s = re.sub(r"§\s+(?=\d)", "§ ", s)
    # Roemische I in Normzitaten als Pipe gelesen: "§ 854 | BGB"
    s = re.sub(r"(§+\s*\d+[a-z]?\s*)\|", r"\1I", s)
    s = re.sub(r"\|\s+(?=(BGB|StGB|GG|VwGO|VwVfG|ZPO|HGB)\b)", "I ", s)
    # Benachbarte Fett-Laeufe verschmelzen: der Textlayer trennt Spans oft
    # mitten in einer fetten Wortgruppe ("**gesetzliches** **Schuldverhaeltnis**").
    s = re.sub(r"\*\*(\s*)\*\*", r"\1", s)
    return s.rstrip()
def parse_zeilen(text):
    """['text', (x_min, y_min, x_max, y_max)] je Ausgabezeile."""
    zeilen = []
    for roh in text.splitlines():
        koords = [int(m) for m in LOC.findall(roh)]
        klartext = LOC.sub("", roh).strip()
        if not klartext:
            continue
        if len(koords) >= 8:
            xs, ys = koords[0::2], koords[1::2]
            box = (min(xs), min(ys), max(xs), max(ys))
        else:
            box = None
        zeilen.append([klartext, box])
    return zeilen
# Gliederungsmarker juristischer Aufschriebe. aa)/bb)/cc) und (1)/(2) sind
# eigene Ebenen — sie muessen einen Absatz aufmachen, sonst verschmelzen
# Pruefungspunkte, und die Gliederungstiefe ist im Gutachten tragender Inhalt.
AUFZAEHLUNG = re.compile(
    r"^\s*([-•·▪○●⇒⇨→➢✔]"
    r"|\(?\d{1,2}[.)]"
    r"|[a-z]{1,3}[.)]"
    r"|[IVXL]{1,5}\.)(?=\s|$)"          # Zeilenende zaehlt: der Textlayer liefert
)                                       # den Marker als eigenes, getrimmtes Fragment
# Kastenartige Einschuebe im Hemmer-Layout beginnen einen eigenen Block
SCHLAGWORT_WOERTER = (r"Anmerkung|Hinweis|Merksatz|Merke|Ergebnis|Beachte|"
                      r"Achtung|Exkurs|Vertiefung|Klausurtipp|"
                      r"Zwischenergebnis|Beispiele|Beispiel|Definition")
SCHLAGWORT = re.compile(r"^(" + SCHLAGWORT_WOERTER + r")\s*:", re.I)
# Dieselben Woerter, aber als ALLEINSTEHENDE Zeile — die Form, in der Hemmer
# sie in den linken Rand setzt.
RANDLABEL = re.compile(r"^\**\s*(?:" + SCHLAGWORT_WOERTER
                       + r")\s*:?\s*\**$", re.I)
# Ebenen der juristischen Gliederung in ihrer ueblichen Ordnung. Der Grad
# beginnt bei ## — # bleibt dem Dokumenttitel vorbehalten. (1)/(a) stehen vor
# den einfachen Zahlen, sonst faengt "\(?\d" die geklammerte Form zuerst.
# `f.` und `ff.` fehlen bewusst: das ist die Abkuerzung fuer "folgende"
# ("§§ 842 ff. (P: …)"), im Bestand 15 solcher Stellen gegen 8 echte f)/ff).
# Die Klammerform bleibt, sie ist eindeutig.
EBENEN = (
    (re.compile(r"^\((?:\d{1,2}|[a-h]{1,2})\)(?=\s)"), 6),
    (re.compile(r"^(?:(?:aa|bb|cc|dd|ee|gg|hh)[.)]|ff\))(?=\s)"), 6),
    (re.compile(r"^(?:[a-eg-h][.)]|f\))(?=\s)"), 5),
    (re.compile(r"^\d{1,2}[.)](?=\s)"), 4),
    (re.compile(r"^[IVX]{1,5}\.(?=\s)"), 3),
    (re.compile(r"^[A-H][.)](?=\s)"), 2),
)
# Steht hinter dem Marker gleich die naechste Abkuerzung, war es keine:
# "h. L.", "h. M.", "d. h.", "a. A." — der Punkt gehoert zum Fachkuerzel.
ABKUERZUNG = re.compile(r"^[A-Za-zÄÖÜ]{1,2}\.")
def ebene(text):
    """Gliederungsgrad (2–6) oder None."""
    # Fett-Sternchen ganz heraus: der Textlayer zeichnet oft den Marker allein
    # aus ("**a)** Gemaess …"), dann steht hinter der Klammer kein Leerzeichen.
    nackt = re.sub(r"\*+", "", text).lstrip()
    for pat, grad in EBENEN:
        m = pat.match(nackt)
        if m:
            return None if ABKUERZUNG.match(nackt[m.end():].lstrip()) else grad
    return None
def ohne_fett(text):
    return re.sub(r"\*\*", "", text).strip()
def nur_fett(text):
    """Besteht der Absatz ausschliesslich aus fetten Laeufen?

    Auch ueber mehrere Zeilen hinweg ("**IV. Exkurs: … – V** **ZR 67/22**") —
    solche Ueberschriften sind laenger als die 90-Zeichen-Grenze, und die
    Grenze zu lockern hiesse 133 weitere Absaetze zu Ueberschriften zu machen,
    darunter Inhaltsverzeichniszeilen mit Fuellpunkten.
    """
    return bool(text.strip()) and not re.sub(r"\*\*.*?\*\*", "", text,
                                             flags=re.S).strip()
def fett_ausgleichen(text):
    """Ungerade Zahl von `**` heilen.

    Entsteht beim Verschmelzen ueber einen Trennstrich hinweg, wenn nur eine
    der beiden Zeilen fett erkannt wurde. Obsidian faerbt sonst den Rest des
    Absatzes — der Fehler ist im Text unsichtbar und erst im Rendern zu sehen.
    """
    if text.count("**") % 2 == 0:
        return text
    i = text.rfind("**")
    return text[:i] + text[i + 2:]
# Alleinstehende Fussnotennummer und der Anfang ihres Textes
FN_NUMMER = re.compile(r"^\**\s*(\d{1,2})\s*\**$")
FN_TEXT = re.compile(r"^[A-ZÄÖÜ„»§]")
def fussnotennummern_anbinden(zeilen, fuss=900, naehe=40):
    """Ausgerueckte Fussnotennummer am Seitenfuss mit ihrem Text verbinden.

    Der Satz stellt die Nummer der Definition nach links aus; als eigene Zeile
    ist sie danach eine blosse Zahl. Die Boilerplate-Regel haelt sie fuer eine
    Seitenzahl und wirft sie weg — der Definition fehlt anschliessend die
    Nummer, und die Verweise im Text bleiben als nackte Ziffer am Wort kleben
    ("abzulehnen.9" statt "abzulehnen.[^9]").

    Erkennungszeichen ist der haengende Einzug: die Nummer beginnt LINKS von
    ihrem Text (gemessen 8 gegen 82, 53 gegen 137). Das trennt sie von den
    hochgestellten Verweiszeichen im Fliesstext, die am rechten Rand stehen
    (x = 977 vor einer Textzeile bei x = 98). Eine feste x-Grenze taugt dafuer
    nicht: auf zweispaltigen Seiten beginnt der rechte Fussnotenblock bei 523
    und blieb damit unerkannt — 151 Faelle im Bestand.
    """
    aus, i = [], 0
    while i < len(zeilen):
        z = zeilen[i]
        n = zeilen[i + 1] if i + 1 < len(zeilen) else None
        m = FN_NUMMER.match(z[0].strip())
        if (m and n and z[1] and n[1] and z[1][1] >= fuss
                and z[1][0] <= n[1][0]
                and abs(n[1][1] - z[1][1]) <= naehe
                and FN_TEXT.match(n[0].lstrip("*").lstrip())):
            box = (min(z[1][0], n[1][0]), min(z[1][1], n[1][1]),
                   max(z[1][2], n[1][2]), max(z[1][3], n[1][3]))
            aus.append([f"{m.group(1)} {n[0].lstrip()}", box] + list(n[2:]))
            i += 2
            continue
        aus.append(z)
        i += 1
    return aus
def ist_ueberschrift(text, nackt):
    """Ist diese kurze, vollstaendig fette Zeile eine Ueberschrift?

    Ausgenommen ist die blosse Randmarke ("**Beispiel:**", "**Merke**"). Die
    gehoert VOR ihren Text, nicht darueber: als Ueberschrift gewertet setzt sie
    `war_ueberschrift` und reisst den folgenden Satz ab. Auf `Strafrecht AT VI`
    S. 3 wurde so aus "Beispiel: Mutter M putzt gerade die Fenster …" ein
    Absatz "**Beispiel:**" und ein zweiter, der mitten im Satz beginnt.

    Eigene Funktion, damit `regress_randmarke.py` die alte Fassung dagegen
    rechnen kann.
    """
    return (text.startswith("**") and text.endswith("**")
            and text.count("**") == 2 and len(nackt) <= 90
            and not RANDLABEL.match(text.strip()))
def randlabel_vorziehen(zeilen, normal, ausrueckung=25, fenster=8):
    """Ausgerueckte Randbeschriftung an den Anfang ihres Blocks ziehen.

    Hemmer setzt "Beispiel:", "Anmerkung:" & Co. in den linken Rand, und zwar
    senkrecht MITTIG zu dem Block, den sie beschriften. Nach y sortiert landet
    die Marke damit irgendwo im Fliesstext — im Bestand mitten im Satz:
    "stiess dabei aus Unachtsamkeit einen **Beispiel:** Blumentopf herunter".
    Sie gehoert vor den Block, nicht in ihn.

    Erkennungszeichen ist wie bei den Fussnotennummern der haengende Einzug:
    die Marke beginnt links vom Rumpf ihrer Nachbarschaft. Der Rumpf wird
    lokal bestimmt, nicht ueber die Seite — auf zweispaltigen Seiten haben
    die Spalten je eigene Einzuege.

    Rueckwaerts gelaufen wird nur innerhalb des Blocks: an einer Absatzluecke,
    einem Gliederungsmarker oder einer weiteren Randzeile ist Schluss.
    """
    if not normal or len(zeilen) < 4:
        return zeilen
    aus = list(zeilen)
    for i in range(1, len(aus)):
        z = aus[i]
        if not z[1] or not RANDLABEL.match(z[0].strip()):
            continue
        nah = [x for x in aus[max(0, i - fenster):i + fenster + 1]
               if x[1] and x is not z]
        if len(nah) < 4:
            continue
        rumpf = statistics.median([x[1][0] for x in nah])
        if z[1][0] > rumpf - ausrueckung:
            continue                          # steht nicht im Rand
        j, letztes_y = i, None
        while j > 0:
            vor = aus[j - 1]
            if not vor[1] or vor[1][0] < rumpf - ausrueckung:
                break                         # Sonderzeile oder zweite Marke
            if letztes_y is not None and vor[1][3] < letztes_y - 1.6 * normal:
                break                         # Absatzluecke
            # Sternchen ganz heraus, nicht nur vorne abgestreift: der Textlayer
            # zeichnet den Marker oft allein aus ("**1.** Der Anspruch …"),
            # dann steht hinter dem Punkt kein Leerzeichen und AUFZAEHLUNG
            # greift nicht.
            if AUFZAEHLUNG.match(re.sub(r"\*+", "", vor[0]).lstrip()):
                break                         # Gliederungspunkt bleibt oben
            letztes_y = vor[1][1]
            j -= 1
        if j < i:
            aus.insert(j, aus.pop(i))
    return aus
def kurze_zeilen(zeilen, fenster=15, luft=0.08, block_anteil=0.55):
    """Je Zeile: endet sie im Blocksatz erkennbar vor dem rechten Rand?

    Im Blocksatz ist die kurze Zeile der Beweis fuer das Ende einer Einheit —
    das einzige Signal, das eine Gliederungsueberschrift von ihrem Fliesstext
    trennt, wenn der Satz keinen zusaetzlichen Abstand dazwischen setzt.

    Der Rand wird aus der NACHBARSCHAFT bestimmt, nicht aus der Seite: Spalten
    und OCR-Kacheln haben je eigene Satzspiegel (gemessen 876 und 957 auf
    derselben Seite), ein gemeinsamer Rand erklaerte die schmalere Spalte
    vollstaendig zu Kurzzeilen.
    """
    n = len(zeilen)
    kurz, block = [False] * n, [False] * n
    idx = [i for i, z in enumerate(zeilen) if z[1]]
    for rang, i in enumerate(idx):
        nah = [zeilen[j] for k, j in enumerate(idx) if abs(k - rang) <= fenster]
        xs = sorted(z[1][2] for z in nah)
        rand = statistics.median(xs[-max(3, len(nah) // 5):])
        breite = rand - min(z[1][0] for z in nah)
        if breite <= 0:
            continue
        voll = sum(1 for z in nah if z[1][2] >= rand - 0.02 * breite)
        if voll < block_anteil * len(nah):   # kein Blocksatz: der Rand sagt nichts
            continue
        block[i] = True
        kurz[i] = zeilen[i][1][2] < rand - luft * breite
    # Zeilen ohne Koordinaten: das Modell liefert das Grounding nicht immer
    # (gemessen 1 von 13 Kacheln). Ersatzmass ist die Zeichenzahl — gemessen
    # trennt 0,95 die Absatzenden (≤ 0,93) sauber vom Fliesstext (≥ 0,98).
    # Bewusst nur als Notbehelf: `block` bleibt hier False, das Mass steuert
    # deshalb allein die Gliederungsregel und nicht die Absatzlogik insgesamt.
    ohne = [i for i, z in enumerate(zeilen) if not z[1]]
    if len(ohne) >= 6:
        med = statistics.median([len(zeilen[i][0].strip()) for i in ohne]) or 1
        for i in ohne:
            kurz[i] = len(zeilen[i][0].strip()) < 0.95 * med
    # Eine klein beginnende Folgezeile setzt den Satz fort — dort waere der
    # Umbruch falsch. Gemessen: nach kurzer Zeile 10–17 % solcher Faelle, nach
    # voller Zeile 53 %. Der Filter nimmt der Regel genau diesen Fehlschnitt.
    for i in range(n - 1):
        if kurz[i] and zeilen[i + 1][0].lstrip("*").lstrip()[:1].islower():
            kurz[i] = False
    return kurz, block
def zusammenfuegen(zeilen):
    """Trennstriche aufloesen und Zeilen zu Absaetzen verschmelzen.

    Weicher Zeilenumbruch innerhalb eines Absatzes wird geschluckt; ein neuer
    Absatz beginnt erst bei auffaelligem senkrechten Abstand oder an einem
    Aufzaehlungs-/Gliederungsmarker. Ohne Koordinaten bleibt jede Zeile eigen.
    """
    zeilen = fussnotennummern_anbinden(zeilen)
    # typischen Zeilenabstand aus den Boxen schaetzen
    ys = [z[1][1] for z in zeilen if z[1]]
    abstaende = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 200]
    normal = statistics.median(abstaende) if abstaende else None
    # Vor kurze_zeilen(): die Nachbarschaftsfenster dort rechnen mit der
    # Reihenfolge, die Randmarke soll sie nicht mehr verschieben.
    zeilen = randlabel_vorziehen(zeilen, normal)
    kurz, block = kurze_zeilen(zeilen)

    aus, puffer, letztes_y, verworfen = [], "", None, []
    war_ueberschrift, letzte_marke, vorher, puffer_x0 = False, None, None, None
    for i, z in enumerate(zeilen):
        text, box = z[0], z[1]
        marke = z[2] if len(z) > 2 else None
        if marke == "tabelle":
            # Unangetastet: keine Saeuberung (Pipes!), kein Boilerplate-Test,
            # kein Verschmelzen mit Nachbarabsaetzen.
            if puffer:
                aus.append(puffer)
                puffer = ""
            aus.append(text)
            war_ueberschrift, letztes_y = False, (box[3] if box else letztes_y)
            letzte_marke = marke
            continue
        text = saeubern(text)
        y = box[1] if box else None
        if not text:
            continue
        if ist_boilerplate(text, y):
            # Verworfenes mitprotokollieren: stilles Loeschen von Inhalt ist der
            # gefaehrlichste Fehler dieser Pipeline, weil er ohne das Original
            # unauffindbar ist.
            verworfen.append(text)
            continue

        # Ein Trennstrich am Zeilenende ist fuer sich schon Beweis der
        # Fortsetzung — er gilt unabhaengig von jeder Abstandsheuristik.
        # Auch dann, wenn die Folgezeile fett anfaengt: die Fetterkennung
        # arbeitet zeilenweise und setzt die Auszeichnung mitten ins Wort
        # ("Berei-" / "**cherungsrecht, Rn. 395)**").
        weiter = text.lstrip("*")
        trennstrich = (puffer.rstrip("*").endswith("-")
                       and not KEIN_JOIN.match(weiter) and weiter[:1].islower())

        # Marker-Pruefung ohne Fett-Sternchen: "**I. Der Ausgangspunkt**" ist
        # ein Gliederungsmarker. Die Sternchen setzt entweder fett_markieren()
        # oder der Textlayer — beide laufen vorher, und beide wuerden hier
        # sonst jeden Marker unsichtbar machen.
        nackt = text.lstrip("*").lstrip()
        ueberschrift = ist_ueberschrift(text, nackt)
        # Zwei Gruende, warum eine fette Zeile KEINE Ueberschrift ist, sondern
        # die Fortsetzung der laufenden: die Vorzeile reichte im Blocksatz bis
        # an den Rand, oder die Zeile steht auf dem Fortsetzungseinzug des
        # Absatzes. Beides trifft die Hemmer-Gliederung, deren Ueberschriften
        # ausgerueckt sind und mitten im Satz fett weiterlaufen
        # ("1. Anspruch aus § 985 BGB auf Rueckgabe des" / "**Bargeldes.**",
        # "5. Wertersatz, § 818 II BGB (Geld ist nicht" /
        # "**mehr identifizierbar vorhanden)**").
        # Dazu das Textsignal: Komma oder Trennstrich am Ende ist ein
        # unfertiger Satz ("B. Gefaehrliche Koerperverletzung, §§ 223 I," /
        # "**224 I Nr. 2, 5 StGB (+)**" — der Trennstrich allein genuegt hier
        # nicht, weil die Folgezeile mit einer Ziffer beginnt). Der Doppelpunkt
        # bleibt aussen vor: "III. Arbeitsanleitung:" ist fertig.
        laeuft_weiter = (vorher is not None and block[vorher] and not kurz[vorher]
                         or box and puffer_x0 is not None
                         and box[0] > puffer_x0 + 8
                         or bool(re.search(r"[,;\-–]\**$", puffer)))

        if not puffer or trennstrich:
            neu = False
        elif marke != letzte_marke:
            # Kastengrenze. Ein umrandeter Kasten ist immer ein eigener
            # Gedanke — bisher verschmolz er mit dem Vorabsatz, weil er weder
            # Schlagwort noch Gliederungsmarker mitbringt.
            neu = True
        elif (AUFZAEHLUNG.match(nackt) or SCHLAGWORT.match(nackt)
              or (ueberschrift and not laeuft_weiter) or war_ueberschrift):
            # war_ueberschrift: nach einer Ueberschrift beginnt immer ein neuer
            # Absatz — sonst zieht die Ueberschrift den Folgetext an sich.
            neu = True
        elif y is not None and letztes_y is not None and y < letztes_y - 50:
            # Der Text springt an den Kopf der naechsten Spalte oder Kachel
            # zurueck. Dort laeuft ein Absatz nur weiter, wenn der Satz
            # sichtbar unfertig ist — Trennstrich faengt das oben ab, sonst
            # die Kleinschreibung. Bisher blieb der Rueckwaertssprung
            # unbemerkt (negativer Abstand ist nie > normal * 1.6) und der
            # Fussnotenblock der einen Spalte verschmolz mit dem Fliesstext
            # der naechsten.
            neu = not text[:1].islower()
        elif normal and y is not None and letztes_y is not None:
            neu = (y - letztes_y) > normal * 1.6
        else:
            # Ohne Koordinaten (das Modell liefert das Grounding-Format nicht
            # immer): am Satzende trennen, sonst weiterlaufen lassen. Jede Zeile
            # als eigenen Absatz zu behandeln zerstueckelt Fliesstext.
            neu = bool(re.search(r'[.!?:]["“»)]?\s*$', puffer))

        if puffer and not neu:
            if trennstrich:
                puffer = puffer.rstrip("*")[:-1] + weiter
            else:
                puffer = puffer + " " + text
        else:
            if puffer:
                aus.append(puffer)
            puffer, puffer_x0 = text, (box[0] if box else None)
        # Ein Gliederungspunkt endet, sobald eine seiner Zeilen vor dem rechten
        # Rand aufhoert. Ohne diese Regel zieht die Ueberschrift den Fliesstext
        # ihres eigenen Punktes in sich hinein — im Gutachten der haeufigste
        # Fall, weil dort zwischen Ueberschrift und Text kein Abstand steht.
        # Nur solange der Punkt selbst noch als Ueberschrift durchgeht: bei
        # laengerem Fliesstext hinter dem Marker traegt die Regel nichts mehr
        # bei, wuerde aber auf Kachelseiten ohne Koordinaten (Zeichenmass
        # statt Rand) mitten im Satz schneiden.
        # Reicht die fette Zeile im Blocksatz bis an den Rand, ist die
        # Ueberschrift nicht zu Ende — sie laeuft in der naechsten weiter
        # ("**IV. Exkurs: … – V**" / "**ZR 67/22**").
        war_ueberschrift = ((ueberschrift and puffer == text
                             and not (block[i] and not kurz[i]))
                            or (kurz[i] and ebene(puffer) is not None
                                and (len(ohne_fett(puffer)) <= 90
                                     or nur_fett(puffer))))
        letztes_y, letzte_marke, vorher = y, marke, i
    if puffer:
        aus.append(puffer)
    # Erst hier, nicht in saeubern(): "**a** **b**" entsteht ueberhaupt erst
    # beim Verschmelzen zweier Zeilen, also nach der zeilenweisen Saeuberung.
    aus = [fett_ausgleichen(re.sub(r"\*\*(\s*)\*\*", r"\1", p)) for p in aus]
    zusammenfuegen.verworfen = verworfen
    return gliederung_auszeichnen(fussnoten_obsidian(aus))
# Satzschlusszeichen. Eine Ueberschrift traegt keines — daran unterscheidet
# sich "3. Anspruch aus § 816 I S. 2 BGB" von "cc) Diese Ansicht ist mit der
# h.M. abzulehnen." und "1. Was versteht man unter Geldwertvindikation?"
SATZENDE = re.compile(r"[.!?][\"“»)\]]?$")
def gliederung_auszeichnen(absaetze, max_ueberschrift=90):
    """Gliederungsebenen sichtbar machen.

    Im Gutachten ist die Ebene tragender Inhalt: a) und aa) sind
    Pruefungsschritte. Markdown kennt fuer `a)` aber keine Liste — die Ebene
    stand deshalb als gewoehnlicher Text im Absatz und war unsichtbar.

    Kurzer Absatz ohne Satzschlusszeichen wird echte Ueberschrift (und damit
    auch anklickbar in Obsidians Gliederungsleiste), sonst wird wenigstens der
    Marker fett. Zahlmarker bleiben unangetastet: `1.` traegt Markdown schon
    als Liste, und ein `**1.**` naehme ihm diese Einrueckung.
    """
    aus = []
    for p in absaetze:
        roh = p.lstrip()
        grad = ebene(roh)
        if grad is None or roh[:1] in "|>[":
            aus.append(p)
            continue
        blank = ohne_fett(roh)
        # Satzschlusszeichen spricht gegen die Ueberschrift ("cc) Diese Ansicht
        # ist mit der h.M. aber abzulehnen."), Fettschrift dafuer: Hemmer setzt
        # die Gliederungspunkte fett, auch die mit Punkt am Ende ("1. Anspruch
        # aus § 985 BGB auf Rueckgabe des **Bargeldes.**").
        if ((len(blank) <= max_ueberschrift or nur_fett(roh))
                and (not SATZENDE.search(blank) or "**" in roh)):
            aus.append("#" * grad + " " + blank)
        elif not roh.startswith("**"):
            marke, rest = roh.split(None, 1) if " " in roh else (roh, "")
            aus.append(f"**{marke}** {rest}" if not marke[:1].isdigit()
                       else p)
        else:
            aus.append(p)
    return aus
# Ein Fragment, das fuer sich allein nur ein Gliederungs- oder
# Aufzaehlungszeichen traegt. Word setzt den Marker an einen Tabulator, und
# PyMuPDF macht daraus eine eigene "line" — "1." und "Die Abtretung als
# Verfuegung" stehen auf derselben Grundlinie, kommen aber getrennt an.
# Buchstabenmarker nur mit Klammer: "aus.", "bzw.", "vgl." sind im Blocksatz
# ebenfalls eigene Fragmente und wuerden als "a.b.c."-Marker durchgehen.
MARKER_ALLEIN = re.compile(
    r"^(?:\*\*)?\s*(?:[-•·▪○●⇒⇨→➢✔o]"
    r"|\(?\d{1,2}[.)]"
    r"|\(?[a-z]{1,3}\)"
    r"|[IVXL]{1,5}\.)\s*(?:\*\*)?$"
)
def fragmente_verschmelzen(zeilen, W, luecke=0.15, max_pt=60):
    """Marker-Fragment und Folgetext derselben Grundlinie zusammenziehen.

    Bewusst NUR fuer alleinstehende Marker, nicht fuer beliebige Fragmente auf
    gleicher Hoehe: eine allgemeine Regel wuerde auf zweispaltigen Seiten die
    linke und die rechte Spalte in eine Zeile ziehen, und die Spaltenlogik
    kaeme nie mehr zum Zug. Ein Prosaabsatz kann nie ein alleinstehender
    Marker sein — die Regel ist damit von der Spaltenfrage unabhaengig.

    Ohne das hier passiert zweierlei, beides in der Ausgabe sichtbar:
      * "1." wird ein eigener Absatz, die Ueberschrift der naechste
      * der Marker steht am Zeilenende ohne trennendes \\s, dadurch greift
        AUFZAEHLUNG nicht und die Listenpunkte verschmelzen zu einem Absatz
    """
    # Nach Grundlinie gruppieren, nicht nach Oberkante: der Courier-Punkt einer
    # Aufzaehlung sitzt 0,8 pt tiefer als der Times-Text daneben. Nach Oberkante
    # sortiert steht er hinter seiner eigenen Zeile und wandert beim Verschmelzen
    # an den Anfang der naechsten ("keine ueberzogenen o Anforderungen").
    reihen = []
    for z in sorted(zeilen, key=lambda z: z[1][1]):
        y0, y1 = z[1][1], z[1][3]
        if reihen:
            r = reihen[-1]
            ry0 = min(a[1][1] for a in r)
            ry1 = max(a[1][3] for a in r)
            hoch = min(y1 - y0, ry1 - ry0) or 1
            if (min(y1, ry1) - max(y0, ry0)) > 0.5 * hoch:
                r.append(z)
                continue
        reihen.append([z])

    aus = []
    for r in reihen:
        r.sort(key=lambda z: z[1][0])
        i = 0
        while i < len(r):
            text, box = r[i][0], r[i][1]
            while (i + 1 < len(r) and MARKER_ALLEIN.match(text.strip())
                   and r[i + 1][1][0] >= box[2]
                   and r[i + 1][1][0] - box[2] < min(luecke * W, max_pt)):
                t2, b2 = r[i + 1][0], r[i + 1][1]
                text = re.sub(r"\*\*(\s*)\*\*", r"\1",
                              text.rstrip() + " " + t2.lstrip())
                box = (box[0], min(box[1], b2[1]), b2[2], max(box[3], b2[3]))
                i += 1
            aus.append([text, box])
            i += 1
    return aus
def als_callout(absaetze, titel):
    """Text in ein eingeklapptes Obsidian-Callout legen.

    Wichtig fuer die Suche: `semantic_search.py` indiziert Text, keine Bilder.
    Ein Diagramm nur als Bild abzulegen macht die Seite unfindbar — genau das,
    was die Umstellung auf .md vermeiden soll. Darum Bild ZUERST, Text darunter
    eingeklappt.
    """
    aus = [f"> [!note]- {titel}"]
    for p in absaetze:
        aus += ["> " + z for z in p.splitlines()] + [">"]
    return "\n".join(aus).rstrip("\n>").rstrip()
