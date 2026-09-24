#!/usr/bin/env python3
"""Cancellation coordination for pdf2md (SIGINT/SIGTERM, Issues #25, #105).

The first signal sets only a flag: the currently running page finishes its
calculation, after which page execution terminates orderly. A second signal
raises `Interrupted` right where the process is — usually inside the model
call of the current page. The runner catches it, drops that unfinished page
and writes the partial file from the pages before it; finally blocks and
TemporaryDirectory contexts clean up on the way out. Which exit code that
ends in (6 with a partial file, 7 without) is decided by the caller.
"""
import signal
import sys

_requested = False


class Interrupted(BaseException):
    """A repeated signal stopped the current page.

    Derives from BaseException like KeyboardInterrupt, so no `except
    Exception` around a model call or a file write can swallow it.
    """


def install():
    """Install SIGINT/SIGTERM handlers; the first signal sets the flag, every
    further one raises `Interrupted`."""
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
        raise Interrupted()
    _requested = True
    name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    sys.stderr.write(
        f"{name} received — cancellation requested, current page will finish "
        "calculation. Repeating signal stops the current page.\n")
    sys.stderr.flush()
