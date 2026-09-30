"""Setup commands of the PaddleOCR engine plugin.

    python -m ocrmypdf_paddle fetch-models

fetch-models puts the pinned model files into the model directory
(OCRMYPDF_PADDLE_MODEL_DIR, default ~/.cache/ocrmypdf-paddle/models) and
checks their SHA-256, so the first OCR run never downloads. Exit 0 when all
models are ready, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys

from ocrmypdf_paddle import runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ocrmypdf_paddle")
    parser.add_argument("command", choices=["fetch-models"])
    parser.parse_args(argv)
    directory = runtime.model_dir()
    try:
        # Looked up at call time, so tests can replace the download.
        fetched = runtime.fetch_models(directory, lambda url: runtime.download_url(url))
    except (OSError, RuntimeError) as error:
        print(f"model download failed: {error}", file=sys.stderr)
        return 1
    for name in fetched:
        print(f"fetched {name} into {directory}")
    print(f"models ready in {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
