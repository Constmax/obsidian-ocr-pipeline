#!/usr/bin/env python3
"""Woerterbuchabgleich nach dem OCR: verlesene Buchstaben finden (Stufe 2).

Nach dem Zusammenbau laeuft der Text jeder OCR-Seite gegen ein Woerterbuch.
Was dort nicht steht, ist ein Kandidat fuer einen Lesefehler — gemessene
Faelle aus bench/ERGEBNIS.md: `Besitzverschaiung` (ff→i), `Rechtsbehels`
(fs→s), `Schu1dverhaeltnis` (l→1), `Leistuug` (n→u).

Das Modul MELDET solche Woerter. Es korrigiert nur, wenn der Aufrufer es
ausdruecklich verlangt UND genau eine bekannte Verwechslungsvariante existiert.
Grund fuer die Zurueckhaltung: ein verfaelschtes Normzitat ist der teuerste
Fehler dieser Pipeline, und ein Woerterbuch kennt weder die Namen der
Beteiligten noch jede Fachvokabel. Lieber ein Fehler mehr gemeldet als ein
richtiges Wort "verbessert".

Zwei Regeln tragen die Sicherheit:
  * Zitate, Zahlen und Abkuerzungen werden gar nicht erst geprueft (`stellen`)
    — die dominante Restfehlerklasse (roemisch I als 1/l/|) ist damit
    ausdruecklich NICHT Gegenstand dieses Moduls.
  * Ein Wort wird nur ersetzt, wenn genau ein Kandidat bekannt ist. Bei zwei
    plausiblen Lesarten (`Hans`/`Haus`) bleibt es stehen und wird gemeldet.

GRENZE, gemessen an 202 Woertern echter Gutachtenprosa gegen `de_DE_frami`
(0 Fehlalarme, 6 von 7 eingestreuten Lesefehlern gefunden): Ein Lesefehler,
der ein morphologisch WOHLGEFORMTES Scheinwort ergibt, kommt durch.
`Verhaltungsakte` zerlegt die Ableitungsregel unten in `verhalten` + `Akte`
und haelt es fuer ein Kompositum. Diese Regeln muessen so weit sein — ohne sie
waere jedes zweite `-ung`-Substantiv ein Fehlalarm, weil die .dic-Dateien
diese Ableitung ihren Affixregeln ueberlassen. Wer die Affixregeln richtig
ausgewertet haben will, installiert `hunspell` mit deutschem Woerterbuch; dann
entscheidet es und die Liste ergaenzt nur noch die Fachbegriffe.

Reines Python, keine Fremdmodule, kein Netzzugriff — testbar ohne MLX, fitz
und Vault-Bestand (pdf2md/test/test_woerterbuch.py). Importiert nichts aus den
Geschwistermodulen.
"""
import os
import re
import shutil
import subprocess
from collections import Counter, namedtuple
from pathlib import Path

# --- Was ueberhaupt geprueft wird ------------------------------------------

# Ein Wort beginnt und endet mit einem Buchstaben, darf innen aber Ziffern
# tragen: genau so kommt die Sperrschrift-/Ziffernverwechslung an
# ("Schu1dverhaeltnis"). Eine nackte Zahl passt damit nie, "823" bleibt
# aussen vor.
BUCHSTABE = "A-Za-zÄÖÜäöüßẞ"
WORT = re.compile(f"[{BUCHSTABE}](?:[{BUCHSTABE}0-9]*[{BUCHSTABE}])?")

# Bereiche, in denen nicht gesucht wird. Wikilinks und Bildeinbettungen sind
# Dateinamen, Fussnotenmarken und Codespannen Syntax — eine "Korrektur" darin
# zerstoert den Verweis, statt Text zu verbessern.
GESPERRT = re.compile(
    r"!?\[\[[^\]]*\]\]"          # [[wikilink]], ![[bild.png]]
    r"|\[\^[^\]]*\]"             # Fussnotenmarke [^12] und ihre Definition
    r"|`[^`]*`"                  # Codespanne
    r"|%%.*?%%"                  # Seitenmarker
    r"|https?://\S+|www\.\S+")

# Zeichen, deren Nachbarschaft ein Zitat verraet. Ein Wort daneben wird nicht
# angefasst: "§ 823 Abs. 1 BGB" und "Rn. 56 ff." sind kein Fliesstext.
# Der Schraegstrich steht fuer die Fundstellenangabe: "Kopp/Schenke",
# "HEMMER/WÜST", "Grüneberg/Herrler". Namen von Kommentatoren stehen in keinem
# Woerterbuch und waren im Versuch die groesste verbliebene Fehlalarmklasse.
ZITAT_NACHBAR = set("§/0123456789")

FUGEN = ("", "s", "es", "n", "en", "er", "ns")
MIN_TEIL = 4          # kuerzere Kompositumsteile akzeptieren jeden Unsinn
MIN_WORT = 4          # unter vier Buchstaben ist das Signal wertlos

# Endungen der deutschen Flexion. Ein .dic haelt Grundformen; ohne diese
# Ablage waere jede gebeugte Form ("Willenserklaerungen", "liegt") ein
# Fehlalarm. Der Umweg ist noetig, weil die Affixregeln (.aff) hier nicht
# ausgewertet werden — steht hunspell zur Verfuegung, macht es das selbst und
# besser.
ENDUNGEN = ("ungen", "ung", "ern", "nen", "test", "eten", "etet", "ete",
            "est", "ten", "tet", "end", "en", "em", "er", "es", "et", "te",
            "st", "e", "n", "s", "t")
# Woran der abgestreifte Stamm gemessen wird. Verben und die von ihnen
# abgeleiteten Substantive stehen als Infinitiv im Woerterbuch: weder "liegt"
# noch "Einigung" sind dort zu finden, "liegen" und "einigen" schon.
STAMM_ANHANG = ("", "en", "n")
# Der Stamm darf kuerzer sein als ein Kompositumsteil: "fuß" (aus "fußend")
# und "ein" (aus "eine") sind Woerter, "fuß"+"end" ist keine Zusammensetzung.
MIN_STAMM = 3
# Verneinung. Sie steht im .dic nicht als eigener Eintrag, ist aber der
# Normalfall dieses Materials ("unvollständig", "Unmöglichkeit", "unstreitig").
# Weitere Vorsilben bleiben draussen: sie sind ueberwiegend lexikalisiert und
# machten die Regel nur durchlaessiger.
PRAEFIX = "un"

# --- Verwechslungen --------------------------------------------------------

# Ungeordnete Paare, in BEIDE Richtungen angewandt. Belegt in
# bench/ERGEBNIS.md bzw. klassische Scanverwechslungen; jedes Paar taugt nur
# deshalb, weil ein Treffer erst zaehlt, wenn er als einziger im Woerterbuch
# steht.
VERWECHSLUNG = (
    ("m", "rn"), ("n", "ri"), ("n", "u"), ("h", "b"), ("h", "k"), ("h", "w"),
    ("f", "t"), ("f", "s"), ("ff", "i"), ("i", "l"), ("i", "j"), ("c", "e"),
    ("e", "o"), ("a", "ä"), ("o", "ö"), ("u", "ü"), ("ss", "ß"), ("s", "ß"),
    ("w", "vv"), ("g", "q"), ("d", "cl"), ("t", "l"), ("z", "2"), ("l", "1"),
    ("o", "0"), ("s", "5"), ("b", "6"), ("g", "9"),
)


def kandidaten(wort):
    """Alle Woerter, die aus `wort` durch EINE Verwechslung entstehen.

    Genau eine Ersetzung an genau einer Stelle — nicht die volle
    Editierdistanz 1. Eine allgemeine Nachbarschaft waere um ein Vielfaches
    groesser und traefe fast immer irgendein Woerterbuchwort; dann waere die
    Eindeutigkeitsregel wertlos.
    """
    # Der Anfangsbuchstabe wird kleingeschrieben mitgesucht und danach wieder
    # gross gesetzt. Ohne das bliebe der Wortanfang jedes Substantivs
    # ungeprueft — bei einer Sprache, die jedes Substantiv gross schreibt,
    # waere das die Haelfte der interessanten Faelle ("Hehler" / "Behler").
    gross = wort[:1].isupper()
    basis = wort[:1].lower() + wort[1:] if gross else wort
    aus = set()
    for a, b in VERWECHSLUNG:
        for von, nach in ((a, b), (b, a)):
            start = 0
            while (i := basis.find(von, start)) != -1:
                neu = basis[:i] + nach + basis[i + len(von):]
                if gross:
                    neu = neu[:1].upper() + neu[1:]
                if neu != wort:
                    aus.add(neu)
                start = i + 1
    return aus


# --- Woerterbuch -----------------------------------------------------------

# Juristische Fachbegriffe, die kein allgemeines Woerterbuch kennt. Bewusst im
# Modul statt in einer Datei: die Vault-Kopie unter `.ocr-bench/` ist flach
# (docs/scripts-detail.md, Zwei-Orte-Konvention) und wuerde ein `daten/`
# stillschweigend verlieren — dann liefe der Abgleich mit halbem Woerterbuch.
# Es sind Grundformen; Flexion und Komposita erledigen ENDUNGEN und FUGEN.
JURISTISCH = """
Abgabenordnung Ablieferungsanspruch Abstraktionsprinzip Abtretung Adressat
Amtsermittlungsgrundsatz Analogie Anfechtung Anfechtungsklage Angeklagter
Anknüpfungspunkt Anscheinsvollmacht Anspruchsgrundlage Anspruchsinhaber
Anspruchsteller Antragsbefugnis Anwartschaft Anwartschaftsrecht Arglist
Auflassung Auflassungsvormerkung Aufopferung Aufrechnung Auslobung
Aussonderung Bedingungsfeindlichkeit Begleitskript Beihilfe Bereicherung
Bereicherungsrecht Beschwer Besitzdiener Besitzkonstitut Besitzmittler
Besitzmittlungsverhältnis Besitzverschaffung Bestimmtheitsgrundsatz
Beweislastumkehr Blankettstrafgesetz Bruchteilsgemeinschaft Deliktsfähigkeit
Deliktsrecht Dereliktion Dienstbarkeit Drittschadensliquidation Drittwirkung
Duldungsvollmacht Eigentumsvermutung Eigentumsvorbehalt Einrede Einwendung
Einziehungsermächtigung Erbbaurecht Erfolgsunrecht Erfüllungsgehilfe
Erledigungserklärung Ermessensfehler Ermessensreduzierung Ersatzaussonderung
Ersitzung Fahrlässigkeit Fahrnis Feststellungsinteresse Feststellungsklage
Fortsetzungsfeststellungsklage Fremdbesitzerexzess Fürsorgepflicht
Gefälligkeitsverhältnis Gefahrtragung Geldwertvindikation Geschäftsführung
Geschäftsgrundlage Gesamtschuld Gesamtschuldner Gestaltungsrecht Gewahrsam
Gewaltenteilung Gewährleistung Grundverhältnis Gutachtenstil
Gutglaubensschutz Haftungsausfüllung Haftungsbegründung Handlungsstörer
Herausgabeanspruch Hinterlegung Inzidentprüfung Irrtum Kausalität
Klagebefugnis Kondiktion Konkurrenz Legitimationswirkung
Leistungsbestimmungsrecht Leistungskondiktion Leistungsstörung
Mitverschulden Nachfrist Naturalrestitution Nebenpflicht Nichtigkeit
Nichtleistungskondiktion Normenkontrolle Nutzungsersatz Obliegenheit
Offenkundigkeit Opferperspektive Pfandrecht Prozessstandschaft
Rechtsbehelfsbelehrung Rechtsbindungswille Rechtsfolgenverweisung
Rechtsgrundverweisung Rechtsmissbrauch Rechtsscheinhaftung
Rechtswidrigkeitszusammenhang Rückgewährschuldverhältnis
Rückgriffskondiktion Rückwirkungsverbot Sachmangel Schadensersatz
Schuldbeitritt Schuldnerverzug Schuldverhältnis Schutzgesetz Schutzpflicht
Selbsthilfe Sicherungsübereignung Sittenwidrigkeit Sonderrechtsnachfolge
Sozialadäquanz Stellvertretung Streitgenossenschaft Subsumtion Surrogat
Tatbestandsirrtum Tateinheit Tatherrschaft Tatmehrheit Teilnahme Treuhand
Übereignung Übergabesurrogat Überholungskausalität Umdeutung Unmöglichkeit
Unterlassungsanspruch Untermaßverbot Unternehmerpfandrecht Verbotsirrtum
Verfügungsbefugnis Verhältnismäßigkeit Verjährung Verkehrssicherungspflicht
Vermögensverfügung Verrichtungsgehilfe Verschulden Verschuldensfähigkeit
Vertrauensschutz Vertretenmüssen Verwaltungsakt Verwaltungsrechtsweg
Verwendungskondiktion Verwirkung Vindikation Vindikationslage
Vollstreckungsgegenklage Vorbehaltsurteil Vorbereitungshandlung Vormerkung
Vorsatz Wegfall Weisungsgebundenheit Werkvertrag Wesentlichkeitstheorie
Widerklage Widerrechtlichkeit Widerrufsrecht Willenserklärung Wucher
Zueignungsabsicht Zurechnung Zurückbehaltungsrecht Zwangsvollstreckung
Zwischenfeststellungsklage
""".split()

# Hochfrequente Formen, die in .dic-Dateien nur als Grundform stehen und deren
# Stammwechsel keine Endungsregel einholt ("kann" zu "koennen"). Kurz zu
# halten: was hier steht, wird nie mehr gemeldet.
GRUNDWORTSCHATZ = """
kann kannst konnte konnten könne könnte könnten muss musst musste mussten
müsse müsste müssten soll sollst sollte sollten will willst wollte wollten
darf darfst durfte durften dürfe dürfte dürften mag mochte möchte möchten
weiß wusste wussten wisse sind seid wäre wären waren gewesen wird werde
wurde wurden würde würden worden hätte hätten gehabt
""".split()

# Wo Systemwoerterbuecher liegen. hunspell/myspell auf Linux und Homebrew,
# die Spelling-Ordner von macOS, das Woerterbuch-Bundle von LibreOffice.
DICT_ORTE = (
    "/opt/homebrew/share/hunspell", "/usr/local/share/hunspell",
    "/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts",
    "~/Library/Spelling", "/Library/Spelling",
    "/Applications/LibreOffice.app/Contents/Resources/extensions/dict-de",
)
DICT_MUSTER = ("de_DE*.dic", "de_AT*.dic", "de_CH*.dic", "de*.dic")


def _kodierung(pfad):
    """Zeichensatz einer .dic-Datei.

    Steht in der SET-Zeile der zugehoerigen .aff, und das ist nicht
    entbehrlich: das verbreitete `de_DE_frami.dic` ist ISO-8859-1. Als UTF-8
    gelesen zerfaellt jedes Wort mit Umlaut — und damit faellt fast der ganze
    juristische Wortschatz aus dem Woerterbuch, ohne dass irgendetwas
    fehlschlaegt. Der Abgleich meldete dann `Eigentümer` und `gemäß`.
    """
    aff = Path(pfad).with_suffix(".aff")
    if aff.exists():
        for zeile in aff.read_bytes().splitlines()[:20]:
            if zeile.upper().startswith(b"SET "):
                return zeile.split(None, 1)[1].decode("ascii", "replace").strip()
    return None


def _dic_lesen(pfad):
    """Woerter aus einer .dic- oder einer einfachen Wortlistendatei.

    Das hunspell-Format traegt hinter dem Wort die Affixflags (`Haus/PN`) und
    manchmal morphologische Felder hinter einem Tabulator; die erste Zeile ist
    die Eintragszahl. Eine `.txt`-Wortliste laeuft durch dieselbe Schleife —
    dort ist einfach nichts abzuschneiden.
    """
    aus = set()
    rohbytes = Path(pfad).read_bytes()
    for kod in (_kodierung(pfad), "utf-8", "iso-8859-1"):
        try:
            roh = rohbytes.decode(kod) if kod else ""
        except (LookupError, UnicodeDecodeError):
            continue
        if roh:
            break
    for i, zeile in enumerate(roh.splitlines()):
        z = zeile.split("\t", 1)[0].split("/", 1)[0].strip()
        if not z or z.startswith("#") or (i == 0 and z.isdigit()):
            continue
        aus.add(z.lower())
    return aus


class Woerterbuch:
    """Wortbestand plus die drei Regeln, die deutschen Text ueberhaupt
    pruefbar machen: Flexion abstreifen, Komposita zerlegen, Fugen zulassen.

    `hunspell` ist optional und ersetzt die ersten beiden: das Programm wertet
    die Affixregeln aus und kennt die Kompositionsregeln der Sprache. Steht es
    zur Verfuegung, entscheidet es zuerst; die eigene Liste haengt nur noch
    die Fachbegriffe an.
    """

    def __init__(self, woerter, quelle="", hunspell=None):
        self.woerter = {w.lower() for w in woerter}
        self.quelle = quelle
        self.hunspell = hunspell

    def __bool__(self):
        return bool(self.woerter) or bool(self.hunspell)

    def kennt(self, wort):
        return not self.unbekannt([wort])

    def direkt(self, wort):
        """Steht das Wort selbst — oder seine Flexionsform — in der Liste?

        Schaerfer als `kennt`, und dafuer gibt es einen Grund: im Deutschen
        laesst sich fast jede Buchstabenfolge irgendwie als Kompositum lesen.
        Aus `Besitzverschaiung` wird ueber die Verwechslungstabelle sowohl
        `Besitzverschaffung` (steht als Begriff in der Liste) als auch
        `Besitzverschalung` (nur zusammensetzbar). Ohne diesen Unterschied
        waeren beide gleich gut und die Korrektur bliebe aus.
        """
        return self._einfach(wort.lower())

    def unbekannt(self, woerter):
        """Teilmenge der Woerter, die das Woerterbuch nicht kennt.

        Stapelweise, weil hunspell ein Prozessaufruf ist: einer je Dokument
        statt einer je Wort.
        """
        offen = set(woerter)
        if self.hunspell and offen:
            try:
                offen = self.hunspell(offen)
            except (OSError, subprocess.SubprocessError):
                # Faellt hunspell mitten im Lauf aus, bleibt die eigene Liste.
                # Ein abgebrochener Lauf waere die schlechtere Antwort: der
                # Abgleich ist eine Zugabe, nicht der Zweck des Programms.
                self.hunspell = None
        return {w for w in offen if not self._bekannt(w.lower())}

    # --- innere Regeln -----------------------------------------------------

    def _bekannt(self, w):
        if self._einfach(w) or self._zerlegbar(w):
            return True
        rest = w[len(PRAEFIX):]
        return (w.startswith(PRAEFIX) and len(rest) >= MIN_TEIL
                and (self._einfach(rest) or self._zerlegbar(rest)))

    def _einfach(self, w, tiefe=0):
        """Wort oder Flexionsform eines bekannten Wortes.

        Zwei Runden, weil sich die Endungen stapeln: "überwiegender" ist
        "überwiegend" plus Adjektivendung und "überwiegend" wiederum die
        Partizipform von "überwiegen". Eine Runde meldete das Wort als
        verdaechtig.
        """
        if w in self.woerter:
            return True
        for e in ENDUNGEN:
            if not w.endswith(e) or len(w) - len(e) < MIN_STAMM:
                continue
            stamm = w[:len(w) - len(e)]
            if any(stamm + a in self.woerter for a in STAMM_ANHANG):
                return True
            if tiefe < 1 and self._einfach(stamm, tiefe + 1):
                return True
        return False

    def _kopf(self, teil):
        """Vorderglied eines Kompositums, Fugenelement abgezogen.

        Geprueft wird gegen `_einfach`, nicht gegen die nackte Wortliste: das
        Vorderglied ist oft selbst abgeleitet ("Aneignungsabsicht" =
        Aneignung + Absicht, und "Aneignung" steht im .dic nur als
        "aneignen").
        """
        for f in FUGEN:
            rumpf = teil[:len(teil) - len(f)] if f else teil
            if f and not teil.endswith(f):
                continue
            if len(rumpf) >= MIN_TEIL and self._einfach(rumpf):
                return True
        return False

    def _zerlegbar(self, w, tiefe=0):
        """Kompositum aus bekannten Teilen?

        Ohne diese Regel waere jedes zweite Wort im Bestand ein Fehlalarm —
        `Besitzmittlungsverhaeltnis` steht in keinem Woerterbuch. MIN_TEIL
        haelt die Regel eng: aus zwei- und dreibuchstabigen Bruchstuecken
        liesse sich auch `Verhaltungsakte` zusammensetzen.
        """
        if tiefe >= 3 or len(w) < MIN_TEIL + MIN_STAMM:
            return False
        # Vorderglieder ab MIN_TEIL, das letzte Glied darf kuerzer sein:
        # "Bundesgerichtshof" endet auf "hof", "Tatbestand" auf "stand".
        for i in range(MIN_TEIL, len(w) - MIN_STAMM + 1):
            if not self._kopf(w[:i]):
                continue
            rest = w[i:]
            if self._einfach(rest) or self._zerlegbar(rest, tiefe + 1):
                return True
        return False


def hunspell_pruefer(sprache="de_DE"):
    """Pruef-Funktion ueber das Programm `hunspell`, oder None.

    Die Probe ist Absicht: `hunspell` allein sagt nichts — ohne installiertes
    deutsches Woerterbuch meldet es JEDES Wort als unbekannt und wuerde jede
    Seite in einen Fehlalarmteppich verwandeln.
    """
    if not shutil.which("hunspell"):
        return None

    def pruefen(woerter):
        eingabe = "\n".join(sorted(woerter)) + "\n"
        p = subprocess.run(["hunspell", "-l", "-d", sprache, "-i", "utf-8"],
                           input=eingabe, capture_output=True, text=True,
                           timeout=120, check=False)
        if p.returncode not in (0, 1):
            raise OSError(p.stderr.strip() or "hunspell fehlgeschlagen")
        return {z.strip() for z in p.stdout.splitlines() if z.strip()}

    try:
        probe = pruefen({"Haus", "Rechtsanwalt", "Xqzwmpfk"})
    except (OSError, subprocess.SubprocessError):
        return None
    if "Haus" in probe or "Xqzwmpfk" not in probe:
        return None                       # kein deutsches Woerterbuch geladen
    return pruefen


def lade(pfade=(), mit_hunspell=True):
    """Woerterbuch aus System-, Umgebungs- und Aufrufquellen zusammensetzen.

    Reihenfolge: ausdruecklich uebergebene Dateien, dann `PDF2MD_WOERTERBUCH`
    (mit `os.pathsep` getrennt), dann das erste gefundene Systemwoerterbuch.
    Die juristische Liste kommt immer dazu — sie ergaenzt, sie ersetzt nichts.

    Findet sich keine Quelle, ist das Ergebnis leer und damit falsy: der
    Aufrufer soll den Abgleich dann ueberspringen und das sagen, statt jedes
    Wort des Dokuments zu melden.
    """
    quellen, woerter = [], set()
    dateien = [Path(p) for p in pfade]
    dateien += [Path(p) for p in
                filter(None, os.environ.get("PDF2MD_WOERTERBUCH", "")
                       .split(os.pathsep))]
    if not dateien:
        dateien = [p for p in (_erste_systemdatei(),) if p]
    for d in dateien:
        if not d.exists():
            raise SystemExit(f"!! Woerterbuch nicht gefunden: {d}")
        neu = _dic_lesen(d)
        woerter |= neu
        quellen.append(f"{d.name} ({len(neu)} Woerter)")

    hs = hunspell_pruefer() if mit_hunspell else None
    if hs:
        quellen.insert(0, "hunspell de_DE")
    if woerter or hs:
        woerter |= {w.lower() for w in JURISTISCH + GRUNDWORTSCHATZ}
        quellen.append(f"{len(JURISTISCH)} juristische Begriffe")
    return Woerterbuch(woerter, " + ".join(quellen), hs)


def _erste_systemdatei():
    for ort in DICT_ORTE:
        p = Path(ort).expanduser()
        if not p.is_dir():
            continue
        for muster in DICT_MUSTER:
            treffer = sorted(p.glob(muster))
            if treffer:
                return treffer[0]
    return None


# --- Pruefen und Korrigieren ------------------------------------------------

Befund = namedtuple("Befund", "wort anzahl vorschlag korrigiert")


def _abkuerzung(w):
    """Grossbuchstabe im Wortinneren — im Deutschen nie ein normales Wort,
    aber die Regel jeder Fachabkuerzung: VwVfG, MueKo, BGHZ, iSd, hM."""
    return w.isupper() or any(c.isupper() for c in w[1:])


def stellen(absatz):
    """Pruefbare Wortstellen eines Absatzes als (wort, start, ende).

    Hier sitzt der Schutz. Uebergangen werden: Tabellen (Pipes sind Syntax),
    gesperrte Bereiche (Wikilinks, Codespannen, Fussnotenmarken), zu kurze
    Woerter, Abkuerzungen und alles, was an eine Zahl oder ein Paragraphen-
    zeichen grenzt. Das Zitat ist damit strukturell aus dem Spiel — nicht
    ueber eine Wortliste, die man vergessen kann.
    """
    if absatz.lstrip().startswith("|"):
        return []
    sperren = [m.span() for m in GESPERRT.finditer(absatz)]
    aus = []
    for m in WORT.finditer(absatz):
        w, i, j = m.group(), m.start(), m.end()
        if len(w) < MIN_WORT or _abkuerzung(w):
            continue
        if any(a <= i < b or a < j <= b for a, b in sperren):
            continue
        # Nachbarzeichen: "§ 823" und "1. Abs" verraten das Zitat, ein Punkt
        # hinter einem kurzen Wort die Abkuerzung ("Rspr.", "Anm.").
        vor = absatz[i - 1] if i else ""
        nach = absatz[j] if j < len(absatz) else ""
        if vor in ZITAT_NACHBAR or nach in ZITAT_NACHBAR:
            continue
        if nach == "." and len(w) <= 5:
            continue
        aus.append((w, i, j))
    return aus


def pruefen(absaetze, wb, korrigieren=False):
    """Absaetze gegen das Woerterbuch halten.

    Liefert `(absaetze, befunde)`. Ohne `korrigieren` bleibt der Text
    unangetastet und die Funktion ist eine reine Meldung; mit `korrigieren`
    werden ausschliesslich die eindeutigen Faelle ersetzt — ein Wort, das
    ueber genau eine Verwechslung zu genau einem bekannten Wort wird.

    Ersetzt wird dann im ganzen Dokumentabschnitt gleich: derselbe Lesefehler
    tritt auf einer Seite mehrfach auf, und eine halb korrigierte Seite waere
    schlimmer als eine unkorrigierte.
    """
    if not wb:
        return absaetze, []
    fundstellen = [stellen(p) for p in absaetze]
    zaehler = Counter(w for s in fundstellen for w, _, _ in s)
    if not zaehler:
        return absaetze, []

    unbekannt = wb.unbekannt(zaehler)
    if not unbekannt:
        return absaetze, []

    # Alle Kandidaten in EINEM Stapel pruefen — bei hunspell ist das der
    # Unterschied zwischen einem Prozessaufruf und tausenden.
    vorschlaege = {w: kandidaten(w) for w in unbekannt}
    alle = {k for ks in vorschlaege.values() for k in ks}
    bekannt = alle - wb.unbekannt(alle)

    ersatz, befunde = {}, []
    for w in sorted(unbekannt, key=lambda x: (-zaehler[x], x)):
        treffer = sorted(vorschlaege[w] & bekannt)
        if len(treffer) > 1:
            # Zweite Runde, engerer Massstab: bleibt genau ein Kandidat uebrig,
            # der als Wort und nicht bloss als Zusammensetzung im Woerterbuch
            # steht, ist er der gemeinte.
            eng = [t for t in treffer if wb.direkt(t)]
            treffer = eng if len(eng) == 1 else treffer
        eindeutig = treffer[0] if len(treffer) == 1 else None
        if eindeutig and korrigieren:
            ersatz[w] = eindeutig
        befunde.append(Befund(w, zaehler[w], eindeutig,
                              bool(eindeutig and korrigieren)))
    if not ersatz:
        return absaetze, befunde

    aus = []
    for absatz, stellen_ in zip(absaetze, fundstellen):
        # Von hinten ersetzen, damit die vorderen Positionen gueltig bleiben.
        for w, i, j in reversed(stellen_):
            if w in ersatz:
                absatz = absatz[:i] + ersatz[w] + absatz[j:]
        aus.append(absatz)
    return aus, befunde
