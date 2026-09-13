#!/usr/bin/env python3
"""Cancellation coordination for pdf2md (SIGINT/SIGTERM, Issue #25).

The first signal sets only a flag: the currently running page finishes its
calculation, after which page execution terminates orderly. A second signal
raises SystemExit with exit code 6 — this cleans up via finally blocks and
TemporaryDirectory contexts of the caller, so the process does not leave
temporary files behind.
"""
import signal
import sys

EXIT_CODE = 6

_requested = False
_exit_code = EXIT_CODE


def install(exit_code=EXIT_CODE):
    """Install SIGINT/SIGTERM handlers; first signal sets the flag,
    second exits immediately with `exit_code`."""
    global _exit_code
    _exit_code = exit_code
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def requested():
    """True as soon as a signal requested an orderly cancellation."""
    return _requested


def reset():
    """Reset flag (for tests and repeated runs in the same process)."""
    global _requested
    _requested = False


def _handle(signum, frame):
    global _requested
    if _requested:
        raise SystemExit(_exit_code)
    _requested = True
    name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    sys.stderr.write(
        f"{name} received — cancellation requested, current page will finish "
        "calculation. Repeating signal exits immediately.\n")
    sys.stderr.flush()

