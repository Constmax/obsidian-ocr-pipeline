"""Legt pdf2md/ auf sys.path, damit die Tests die Module ohne Installation
importieren koennen.

Bewusst KEIN __init__.py: pdf2md.py laeuft weiterhin als Skript, und die
Vault-Kopie bleibt flach (siehe bench/pfade.py, Zwei-Orte-Konvention).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
