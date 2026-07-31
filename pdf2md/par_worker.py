#!/usr/bin/env python3
"""Ein Arbeiter: laedt das Modell und arbeitet die uebergebenen Bilder ab.
Ausgabe eine Zeile JSON auf stdout, damit der Treiber rechnen kann."""
import sys, time, json

MODEL = "mlx-community/PaddleOCR-VL-1.5-4bit"
PROMPT = "Parse this document page to Markdown."


def main():
    bilder = sys.argv[1:]
    t0 = time.perf_counter()
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    model, processor = load(MODEL)
    config = load_config(MODEL)
    formatted = apply_chat_template(processor, config, PROMPT, num_images=1)
    t_load = time.perf_counter() - t0

    zeichen, t_inf = 0, time.perf_counter()
    for b in bilder:
        r = generate(model, processor, formatted, image=[b],
                     max_tokens=8192, temperature=0.0, verbose=False)
        zeichen += len(r.text if hasattr(r, "text") else str(r))
    print(json.dumps(dict(n=len(bilder), laden=round(t_load, 1),
                          inferenz=round(time.perf_counter() - t_inf, 1),
                          zeichen=zeichen)))


if __name__ == "__main__":
    main()
