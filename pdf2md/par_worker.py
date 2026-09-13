#!/usr/bin/env python3
"""A worker: loads the model and processes the passed images.
Output is one line of JSON on stdout, so the driver can process it."""
import json
import sys
import time

MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PROMPT = "Parse this document page to Markdown."


def main():
    images = sys.argv[1:]
    t0 = time.perf_counter()
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    model, processor = load(MODEL)
    config = load_config(MODEL)
    formatted = apply_chat_template(processor, config, PROMPT, num_images=1)
    t_load = time.perf_counter() - t0

    characters, t_inf = 0, time.perf_counter()
    for b in images:
        r = generate(model, processor, formatted, image=[b],
                     max_tokens=8192, temperature=0.0, verbose=False)
        characters += len(r.text if hasattr(r, "text") else str(r))
    print(json.dumps(dict(n=len(images), load=round(t_load, 1),
                          inference=round(time.perf_counter() - t_inf, 1),
                          characters=characters)))


if __name__ == "__main__":
    main()
