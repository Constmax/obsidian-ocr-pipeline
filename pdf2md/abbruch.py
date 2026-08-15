#!/usr/bin/env python3
"""Abbruch-Koordination fuer pdf2md (SIGINT/SIGTERM, Issue #25).

Das erste Signal setzt nur ein Flag: die laufende Seite wird zu Ende
gerechnet, danach beendet sich der Seitenlauf geordnet. Ein zweites Signal
erhebt SystemExit mit Exit-Code 6 — das raeumt ueber die finally-Bloecke und
TemporaryDirectory-Kontexte des Aufrufers mit auf, der Prozess haengt also
auch dann keine Temporärdateien hinter.
"""
import signal
import sys

EXIT_CODE = 6

_angefordert = False
_exit_code = EXIT_CODE


def installieren(exit_code=EXIT_CODE):
    """SIGINT/SIGTERM-Handler installieren; erstes Signal setzt das Flag,
    zweites beendet sofort mit `exit_code`."""
    global _exit_code
    _exit_code = exit_code
    signal.signal(signal.SIGINT, _behandeln)
    signal.signal(signal.SIGTERM, _behandeln)


def angefordert():
    """True, sobald ein Signal einen geordneten Abbruch angefordert hat."""
    return _angefordert


def zuruecksetzen():
    """Flag zuruecksetzen (fuer Tests und erneute Lauefe im selben Prozess)."""
    global _angefordert
    _angefordert = False


def _behandeln(signum, frame):
    global _angefordert
    if _angefordert:
        raise SystemExit(_exit_code)
    _angefordert = True
    name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    sys.stderr.write(
        f"{name} empfangen — Abbruch angefordert, laufende Seite wird fertig "
        "gerechnet. Nochmaliges Signal beendet sofort.\n")
    sys.stderr.flush()
