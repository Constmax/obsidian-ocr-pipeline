"""Wo liegt `pdf2md.py`? — je nach Baum an zwei verschiedenen Stellen.

Dieselben Testskripte laufen an zwei Orten:

  * im Vault als `.ocr-bench/` — alles flach in einem Verzeichnis
  * im Repo `obsidian-ocr-pipeline` als `bench/` neben `pdf2md/`

Statt zwei Fassungen zu pflegen, sucht dieses Modul den Pfad. Dadurch bleiben
die Dateien in beiden Baeumen zeichengleich und ein Abgleich ist ein Kopieren —
die Alternative waere ein Diff, in dem jede echte Aenderung zwischen
Pfadanpassungen untergeht.
"""
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent


def _finde_pdf2md():
    for kandidat in (BENCH / "pdf2md.py", BENCH.parent / "pdf2md" / "pdf2md.py"):
        if kandidat.exists():
            return kandidat
    raise SystemExit("!! pdf2md.py nicht gefunden — weder neben den Tests "
                     "noch in ../pdf2md/")


PDF2MD_PY = _finde_pdf2md()

for _p in (str(PDF2MD_PY.parent), str(BENCH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os

# Wurzel, gegen die `pages.json` seine Pfade aufloest. Im Vault ist das das
# Vault-Root mit `raw/` darunter; im Repo waere es dessen Elternverzeichnis, wo
# es kein `raw/` gibt. Die Bestandsregressionen (regress_*.py) laufen deshalb
# nur im Vault — im Repo liegen sie als Beleg und zum Nachvollziehen.
_vault_env = os.environ.get("VAULT_ROOT")
if _vault_env:
    WURZEL = Path(os.path.expanduser(_vault_env))
elif (Path.home() / "JuraExamenVault").exists():
    WURZEL = Path.home() / "JuraExamenVault"
else:
    WURZEL = BENCH.parent
