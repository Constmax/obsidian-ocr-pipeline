#!/usr/bin/env python3
"""Dictionary matching after OCR: find mistyped letters (Stage 2).

After assembly, the text of each OCR page runs against a dictionary.
What is not found there is a candidate for a reading error — measured cases
from bench/RESULT.md: `Besitzverschaiung` (ff→i), `Rechtsbehels` (fs→s),
`Schu1dverhaeltnis` (l→1), `Leistuug` (n→u).

This module REPORTS such words. It corrects only if the caller explicitly
requests it AND exactly one known confusion variant exists.
Reason for restraint: a corrupted citation of a legal norm is the most expensive
error of this pipeline, and a dictionary knows neither the names of the
parties nor every technical term. Better one more error reported than a
correct word "improved".

Two rules ensure safety:
  * Citations, numbers, and abbreviations are not checked at all (`word_positions`)
    — the dominant remaining error class (Roman I as 1/l/|) is thus explicitly
    NOT subject to this module.
  * A word is replaced only if exactly one candidate is known. For two
    plausible readings (`Hans`/`Haus`), it remains unchanged and is reported.

LIMIT, measured on 202 words of real legal prose against `de_DE_frami`
(0 false alarms, 6 out of 7 artificially inserted reading errors found): A reading
error that yields a morphologically WELL-FORMED pseudo-word gets through.
`Verhaltungsakte` is broken down by the derivation rule below into `verhalten` + `Akte`
and considered a compound word. These rules must be broad — without them
every second `-ung` noun would be a false alarm, because .dic files leave
this derivation to their affix rules. Anyone who wants affix rules evaluated
properly installs `hunspell` with a German dictionary; then it decides and the list
only supplements technical terms.

Pure Python, no external modules, no network access — testable without MLX, fitz,
and Vault assets (pdf2md/test/test_dictionary.py). Imports nothing from sibling
modules.
"""
import os
import re
import shutil
import subprocess
from collections import Counter, namedtuple
from pathlib import Path

# --- What is checked at all ------------------------------------------------

# A word starts and ends with a letter, but may carry digits inside:
# exactly like letter/digit confusions arrive ("Schu1dverhaeltnis").
# A naked number never matches, "823" stays out.
LETTER = "A-Za-zÄÖÜäöüßẞ"
WORD = re.compile(f"[{LETTER}](?:[{LETTER}0-9]*[{LETTER}])?")

# Regions where search is disabled. Wikilinks and image embeds are filenames,
# footnote marks and code spans are syntax — a "correction" therein breaks
# the reference instead of improving text.
BLOCKED = re.compile(
    r"!?\[\[[^\]]*\]\]"          # [[wikilink]], ![[image.png]]
    r"|\[\^[^\]]*\]"             # Footnote mark [^12] and its definition
    r"|`[^`]*`"                  # Code span
    r"|%%.*?%%"                  # Page marker
    r"|https?://\S+|www\.\S+")

# Characters whose proximity reveals a citation. A word next to them is left untouched:
# "§ 823 Abs. 1 BGB" and "Rn. 56 ff." are not body text.
# The slash stands for citation sources: "Kopp/Schenke", "HEMMER/WÜST", "Grüneberg/Herrler".
# Names of commentators are in no dictionary and were the largest remaining false alarm class.
CITATION_NEIGHBOR = set("§/0123456789")

JOINING_ELEMENTS = ("", "s", "es", "n", "en", "er", "ns")
MIN_PART = 4          # shorter compound parts accept any nonsense
MIN_WORD = 4          # below four letters the signal is worthless

# Endings of German inflection. A .dic holds root forms; without this
# mapping every inflected form ("Willenserklaerungen", "liegt") would be a
# false alarm. The detour is necessary because affix rules (.aff) are not
# evaluated here — if hunspell is available, it does this itself and better.
ENDINGS = ("ungen", "ung", "ern", "nen", "test", "eten", "etet", "ete",
           "est", "ten", "tet", "end", "en", "em", "er", "es", "et", "te",
           "st", "e", "n", "s", "t")
# What stripped stems are matched against. Verbs and nouns derived from them
# stand as infinitives in the dictionary: neither "liegt" nor "Einigung" are found there,
# "liegen" and "einigen" are.
STEM_SUFFIX = ("", "en", "n")
# The stem may be shorter than a compound part: "fuß" (from "fußend")
# and "ein" (from "eine") are words, "fuß"+"end" is not a compound.
MIN_STEM = 3
# Negation. It does not stand in .dic as a separate entry, but is common in this material
# ("unvollständig", "Unmöglichkeit", "unstreitig"). Other prefixes remain out:
# they are mostly lexicalized and would make the rule too permissive.
PREFIX = "un"

# --- Confusions ------------------------------------------------------------

# Unordered pairs, applied in BOTH directions. Documented in bench/RESULT.md
# or classic scan confusions; each pair is valid only because a hit counts
# only if it is the sole hit in the dictionary.
CONFUSIONS = (
    ("m", "rn"), ("n", "ri"), ("n", "u"), ("h", "b"), ("h", "k"), ("h", "w"),
    ("f", "t"), ("f", "s"), ("ff", "i"), ("i", "l"), ("i", "j"), ("c", "e"),
    ("e", "o"), ("a", "ä"), ("o", "ö"), ("u", "ü"), ("ss", "ß"), ("s", "ß"),
    ("w", "vv"), ("g", "q"), ("d", "cl"), ("t", "l"), ("z", "2"), ("l", "1"),
    ("o", "0"), ("s", "5"), ("b", "6"), ("g", "9"),
)


def candidates(word):
    """All words arising from `word` by ONE confusion.

    Exactly one replacement at exactly one place — not full edit distance 1.
    A general neighborhood would be much larger and match almost any dictionary word;
    the uniqueness rule would then be worthless.
    """
    capitalized = word[:1].isupper()
    base = word[:1].lower() + word[1:] if capitalized else word
    out = set()
    for a, b in CONFUSIONS:
        for src, dst in ((a, b), (b, a)):
            start = 0
            while (i := base.find(src, start)) != -1:
                new_word = base[:i] + dst + base[i + len(src):]
                if capitalized:
                    new_word = new_word[:1].upper() + new_word[1:]
                if new_word != word:
                    out.add(new_word)
                start = i + 1
    return out


# --- Dictionary ------------------------------------------------------------

LEGAL_TERMS = """
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

BASIC_VOCABULARY = """
kann kannst konnte konnten könne könnte könnten muss musst musste mussten
müsse müsste müssten soll sollst sollte sollten will willst wollte wollten
darf darfst durfte durften dürfe dürfte dürften mag mochte möchte möchten
weiß wusste wussten wisse sind seid wäre wären waren gewesen wird werde
wurde wurden würde würden worden hätte hätten gehabt
""".split()

DICT_LOCATIONS = (
    "/opt/homebrew/share/hunspell", "/usr/local/share/hunspell",
    "/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts",
    "~/Library/Spelling", "/Library/Spelling",
    "/Applications/LibreOffice.app/Contents/Resources/extensions/dict-de",
)
DICT_PATTERNS = ("de_DE*.dic", "de_AT*.dic", "de_CH*.dic", "de*.dic")


def _encoding(path):
    """Character set of a .dic file."""
    aff = Path(path).with_suffix(".aff")
    if aff.exists():
        for line in aff.read_bytes().splitlines()[:20]:
            if line.upper().startswith(b"SET "):
                return line.split(None, 1)[1].decode("ascii", "replace").strip()
    return None


def _read_dic(path):
    """Words from a .dic or plain wordlist file."""
    out = set()
    raw_bytes = Path(path).read_bytes()
    for enc in (_encoding(path), "utf-8", "iso-8859-1"):
        try:
            raw = raw_bytes.decode(enc) if enc else ""
        except (LookupError, UnicodeDecodeError):
            continue
        if raw:
            break
    for i, line in enumerate(raw.splitlines()):
        z = line.split("\t", 1)[0].split("/", 1)[0].strip()
        if not z or z.startswith("#") or (i == 0 and z.isdigit()):
            continue
        out.add(z.lower())
    return out


class Dictionary:
    """Vocabulary plus rules to check German text:
    strip inflection, split compounds, allow joining morphemes.

    `hunspell` is optional and replaces the first two. If available,
    it decides first; the internal list only appends technical terms.
    """

    def __init__(self, words, source="", hunspell=None):
        self.words = {w.lower() for w in words}
        self.source = source
        self.hunspell = hunspell

    def __bool__(self):
        return bool(self.words) or bool(self.hunspell)

    def knows(self, word):
        return not self.unknown([word])

    def direct(self, word):
        """Is the word itself — or its inflected form — in the list?"""
        return self._simple(word.lower())

    def unknown(self, words):
        """Subset of words not known by the dictionary."""
        open_set = set(words)
        if self.hunspell and open_set:
            try:
                open_set = self.hunspell(open_set)
            except (OSError, subprocess.SubprocessError):
                self.hunspell = None
        return {w for w in open_set if not self._is_known(w.lower())}

    # --- internal rules ----------------------------------------------------

    def _is_known(self, w):
        if self._simple(w) or self._decomposable(w):
            return True
        rest = w[len(PREFIX):]
        return (w.startswith(PREFIX) and len(rest) >= MIN_PART
                and (self._simple(rest) or self._decomposable(rest)))

    def _simple(self, w, depth=0):
        if w in self.words:
            return True
        for e in ENDINGS:
            if not w.endswith(e) or len(w) - len(e) < MIN_STEM:
                continue
            stem = w[:len(w) - len(e)]
            if any(stem + a in self.words for a in STEM_SUFFIX):
                return True
            if depth < 1 and self._simple(stem, depth + 1):
                return True
        return False

    def _head(self, part):
        for f in JOINING_ELEMENTS:
            body = part[:len(part) - len(f)] if f else part
            if f and not part.endswith(f):
                continue
            if len(body) >= MIN_PART and self._simple(body):
                return True
        return False

    def _decomposable(self, w, depth=0):
        if depth >= 3 or len(w) < MIN_PART + MIN_STEM:
            return False
        for i in range(MIN_PART, len(w) - MIN_STEM + 1):
            if not self._head(w[:i]):
                continue
            rest = w[i:]
            if self._simple(rest) or self._decomposable(rest, depth + 1):
                return True
        return False


def hunspell_checker(language="de_DE"):
    """Checker function via `hunspell`, or None."""
    if not shutil.which("hunspell"):
        return None

    def check_words(words):
        inp = "\n".join(sorted(words)) + "\n"
        p = subprocess.run(["hunspell", "-l", "-d", language, "-i", "utf-8"],
                           input=inp, capture_output=True, text=True,
                           timeout=120, check=False)
        if p.returncode not in (0, 1):
            raise OSError(p.stderr.strip() or "hunspell failed")
        return {z.strip() for z in p.stdout.splitlines() if z.strip()}

    try:
        sample = check_words({"Haus", "Rechtsanwalt", "Xqzwmpfk"})
    except (OSError, subprocess.SubprocessError):
        return None
    if "Haus" in sample or "Xqzwmpfk" not in sample:
        return None
    return check_words


def load(paths=(), with_hunspell=True):
    """Assemble dictionary from system, environment, and CLI sources."""
    sources, words = [], set()
    files = [Path(p) for p in paths]
    files += [Path(p) for p in
              filter(None, os.environ.get("PDF2MD_DICTIONARY", "")
                     .split(os.pathsep))]
    if not files:
        files = [p for p in (_first_system_file(),) if p]
    for f in files:
        if not f.exists():
            raise SystemExit(f"!! Dictionary not found: {f}")
        new_words = _read_dic(f)
        words |= new_words
        sources.append(f"{f.name} ({len(new_words)} words)")

    hs = hunspell_checker() if with_hunspell else None
    if hs:
        sources.insert(0, "hunspell de_DE")
    if words or hs:
        words |= {w.lower() for w in LEGAL_TERMS + BASIC_VOCABULARY}
        sources.append(f"{len(LEGAL_TERMS)} legal terms")
    return Dictionary(words, " + ".join(sources), hs)


def _first_system_file():
    for loc in DICT_LOCATIONS:
        p = Path(loc).expanduser()
        if not p.is_dir():
            continue
        for pattern in DICT_PATTERNS:
            matches = sorted(p.glob(pattern))
            if matches:
                return matches[0]
    return None


# --- Checking and Correcting ------------------------------------------------

Finding = namedtuple("Finding", "word count suggestion corrected")


def _is_abbreviation(w):
    return w.isupper() or any(c.isupper() for c in w[1:])


def word_positions(paragraph):
    """Checkable word positions in a paragraph as (word, start, end)."""
    if paragraph.lstrip().startswith("|"):
        return []
    blocked = [m.span() for m in BLOCKED.finditer(paragraph)]
    out = []
    for m in WORD.finditer(paragraph):
        w, i, j = m.group(), m.start(), m.end()
        if len(w) < MIN_WORD or _is_abbreviation(w):
            continue
        if any(a <= i < b or a < j <= b for a, b in blocked):
            continue
        before = paragraph[i - 1] if i else ""
        after = paragraph[j] if j < len(paragraph) else ""
        if before in CITATION_NEIGHBOR or after in CITATION_NEIGHBOR:
            continue
        if after == "." and len(w) <= 5:
            continue
        out.append((w, i, j))
    return out


def check(paragraphs, dict_obj, correct=False):
    """Check paragraphs against dictionary.

    Returns `(paragraphs, findings)`.
    """
    if not dict_obj:
        return paragraphs, []
    positions = [word_positions(p) for p in paragraphs]
    counts = Counter(w for pos in positions for w, _, _ in pos)
    if not counts:
        return paragraphs, []

    unrecognized = dict_obj.unknown(counts)
    if not unrecognized:
        return paragraphs, []

    suggestions = {w: candidates(w) for w in unrecognized}
    all_candidates = {c for cs in suggestions.values() for c in cs}
    known = all_candidates - dict_obj.unknown(all_candidates)

    replacements, findings = {}, []
    for w in sorted(unrecognized, key=lambda x: (-counts[x], x)):
        matches = sorted(suggestions[w] & known)
        if len(matches) > 1:
            narrow = [t for t in matches if dict_obj.direct(t)]
            matches = narrow if len(narrow) == 1 else matches
        unique = matches[0] if len(matches) == 1 else None
        if unique and correct:
            replacements[w] = unique
        findings.append(Finding(w, counts[w], unique,
                                bool(unique and correct)))
    if not replacements:
        return paragraphs, findings

    out = []
    for paragraph, pos in zip(paragraphs, positions):
        for w, i, j in reversed(pos):
            if w in replacements:
                paragraph = paragraph[:i] + replacements[w] + paragraph[j:]
        out.append(paragraph)
    return out, findings
