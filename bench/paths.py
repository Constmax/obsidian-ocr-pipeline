"""Repository and vault paths shared by benchmark tools.

Benchmark tools run from this repository. Source PDFs remain outside the
repository and are resolved against ``VAULT_ROOT``. When the repository is
stored directly inside a vault, its parent is detected as a convenience.
"""

import os
import sys
from pathlib import Path


BENCH = Path(__file__).resolve().parent
REPOSITORY = BENCH.parent
PDF2MD_PY = REPOSITORY / "pdf2md" / "pdf2md.py"

if not PDF2MD_PY.is_file():
    raise RuntimeError(f"pdf2md entry point not found: {PDF2MD_PY}")

pdf2md_dir = str(PDF2MD_PY.parent)
if pdf2md_dir not in sys.path:
    sys.path.insert(0, pdf2md_dir)

configured_vault = os.environ.get("VAULT_ROOT")
if configured_vault:
    VAULT_ROOT = Path(configured_vault).expanduser().resolve()
elif (REPOSITORY.parent / "raw").is_dir():
    VAULT_ROOT = REPOSITORY.parent
else:
    VAULT_ROOT = REPOSITORY
