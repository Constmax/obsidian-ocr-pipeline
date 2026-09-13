#!/usr/bin/env python3
"""The golden snapshot against the split modules.

generate_snapshot.py records input and output of all pure functions in
daten/snapshot.json. This test recalculates the same state against the
modules and ensures nothing deviates.

  python3 -m pytest pdf2md/test/test_snapshot.py
"""
import json
from pathlib import Path

from generate_snapshot import compute_result, modules, compare

DATA = Path(__file__).resolve().parent / "daten" / "snapshot.json"


def test_snapshot_unchanged():
    old = json.loads(DATA.read_text(encoding="utf-8"))
    errors = compare(compute_result(modules()), old)
    assert not errors, "\n".join(
        f"{path}\n  new: {n!r}\n  old: {a!r}" for path, n, a in errors[:5])
