#!/usr/bin/env python3
"""Benchmark-Runner. Wird IM jeweiligen venv aufgerufen:

  source .venv-paddleocr/bin/activate && python .ocr-bench/run_bench.py --path A --smoke
  source .venv-mlxocr/bin/activate     && python .ocr-bench/run_bench.py --path B --smoke

Misst pro Seite Wandzeit und Peak-RSS, schreibt Markdown nach out-<Pfad>/
und eine Zeile je Lauf in ergebnisse.csv.
"""
import argparse, csv, os, resource, sys, time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
CSV = BENCH / "ergebnisse.csv"

# Reihenfolge nach Haertegrad — leicht zuerst, damit frueh klar wird wo es bricht
ORDER = [
    "04-einspaltig-sauber",
    "02-zweispalter-dicht",
    "01-zweispalter-handschrift",
    "05-diagramm-sekundaeranspruch",
    "06-diagramm-kausalitaet",
    "03-durchschlag-handschrift",
]
SMOKE = ["04-einspaltig-sauber"]

# Pfad B ist ein roher VLM-Call — der Prompt entscheidet mit. Kandidaten in
# absteigender Erwartung; --prompt-idx waehlt aus, --try-prompts testet alle.
PROMPTS_B = [
    "Parse this document page to Markdown.",
    "OCR:",
    "Convert the document to markdown.",
    "Extract all text from this page in correct reading order as Markdown.",
]


def peak_rss_mb():
    """macOS: ru_maxrss in Bytes; Linux: in KiB."""
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / (1024 * 1024) if sys.platform == "darwin" else v / 1024


def log_row(row):
    new = not CSV.exists()
    with CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "pfad", "seite", "sekunden", "peak_rss_mb", "zeichen",
            "baseline_zeichen", "prompt", "fehler"])
        if new:
            w.writeheader()
        w.writerow(row)


def baseline_chars(name):
    p = BENCH / f"{name}.baseline.txt"
    return len(p.read_text(encoding="utf-8").strip()) if p.exists() else 0


# ────────────────────────────────────────────────────────────── Pfad A
def run_a(names):
    from paddleocr import PaddleOCRVL
    print("Lade PaddleOCRVL (device=cpu) — erster Lauf laedt Modelle herunter ...")
    t0 = time.perf_counter()
    pipe = PaddleOCRVL(device="cpu")
    print(f"   Modell geladen in {time.perf_counter()-t0:.1f} s\n")

    out = BENCH / "out-A"
    out.mkdir(exist_ok=True)
    for name in names:
        img = BENCH / f"{name}.png"
        if not img.exists():
            print(f"!! fehlt: {img.name}")
            continue
        print(f"→ {name} ...", flush=True)
        t = time.perf_counter()
        err, chars = "", 0
        try:
            for res in pipe.predict(str(img)):
                res.save_to_markdown(save_path=str(out))
            dt = time.perf_counter() - t
            md = sorted(out.glob(f"{name}*.md"))
            chars = len(md[0].read_text(encoding="utf-8").strip()) if md else 0
        except Exception as e:
            dt = time.perf_counter() - t
            err = f"{type(e).__name__}: {e}"
            print(f"   ✗ {err}")
        bl = baseline_chars(name)
        print(f"   {dt:6.1f} s | {chars:5d} Z. (Baseline {bl}) | Peak {peak_rss_mb():.0f} MB")
        log_row(dict(pfad="A", seite=name, sekunden=round(dt, 2),
                     peak_rss_mb=round(peak_rss_mb()), zeichen=chars,
                     baseline_zeichen=bl, prompt="", fehler=err))


# ────────────────────────────────────────────────────────────── Pfad B
def _mlx_api():
    """mlx-vlm hat die Signaturen zwischen 0.3.x und 0.6.x bewegt.
    Statt eine Version zu raten: einsammeln was da ist und melden."""
    import mlx_vlm
    from mlx_vlm import load, generate
    ver = getattr(mlx_vlm, "__version__", "?")

    apply_ct = None
    for mod, fn in (("mlx_vlm.prompt_utils", "apply_chat_template"),
                    ("mlx_vlm.utils", "apply_chat_template")):
        try:
            apply_ct = getattr(__import__(mod, fromlist=[fn]), fn)
            break
        except (ImportError, AttributeError):
            continue

    load_config = None
    for mod in ("mlx_vlm.utils", "mlx_vlm"):
        try:
            load_config = getattr(__import__(mod, fromlist=["load_config"]), "load_config")
            break
        except (ImportError, AttributeError):
            continue

    print(f"   mlx-vlm {ver} | apply_chat_template: {'ok' if apply_ct else 'FEHLT'} "
          f"| load_config: {'ok' if load_config else 'FEHLT'}")
    return load, generate, apply_ct, load_config


def _mlx_generate(generate, model, processor, formatted, img):
    """Ruft generate() ueber die bekannten Signaturvarianten durch.
    Gibt (text, benutzte_variante) zurueck."""
    variants = [
        ("image=list, temperature", dict(image=[img], max_tokens=8192, temperature=0.0, verbose=False)),
        ("image=list, temp",        dict(image=[img], max_tokens=8192, temp=0.0, verbose=False)),
        ("image=list, nur max",     dict(image=[img], max_tokens=8192, verbose=False)),
        ("image=str",               dict(image=img, max_tokens=8192, verbose=False)),
    ]
    last = None
    for label, kw in variants:
        try:
            res = generate(model, processor, formatted, **kw)
        except TypeError as e:
            last = f"TypeError [{label}]: {e}"
            continue
        text = res if isinstance(res, str) else getattr(res, "text", str(res))
        return text, label
    # positionale Variante als letzter Versuch (alte 0.3.x-Reihenfolge)
    try:
        res = generate(model, processor, formatted, [img], max_tokens=8192, verbose=False)
        text = res if isinstance(res, str) else getattr(res, "text", str(res))
        return text, "positional"
    except TypeError as e:
        raise RuntimeError(f"keine generate()-Signatur passte. Letzter Fehler: {last or e}")


def run_b(names, prompts):
    load, generate, apply_ct, load_config = _mlx_api()

    model_id = os.environ.get("MLX_OCR_MODEL", "mlx-community/PaddleOCR-VL-1.5-4bit")
    print(f"Lade {model_id} — erster Lauf laedt ~700 MB herunter ...")
    t0 = time.perf_counter()
    model, processor = load(model_id)
    config = load_config(model_id) if load_config else None
    print(f"   Modell geladen in {time.perf_counter()-t0:.1f} s\n")

    def build_prompt(p):
        if apply_ct is None:
            return p
        for kw in (dict(num_images=1), {}):
            try:
                return apply_ct(processor, config, p, **kw)
            except TypeError:
                continue
        return p

    out = BENCH / "out-B"
    out.mkdir(exist_ok=True)
    for name in names:
        img = BENCH / f"{name}.png"
        if not img.exists():
            print(f"!! fehlt: {img.name}")
            continue
        for pi, prompt in enumerate(prompts):
            tag = f"{name}" if len(prompts) == 1 else f"{name}.p{pi}"
            print(f"→ {tag}  prompt={prompt!r}", flush=True)
            t = time.perf_counter()
            err, text = "", ""
            try:
                formatted = build_prompt(prompt)
                text, variante = _mlx_generate(generate, model, processor,
                                               formatted, str(img))
                dt = time.perf_counter() - t
                (out / f"{tag}.md").write_text(text, encoding="utf-8")
                print(f"   generate()-Variante: {variante}")
            except Exception as e:
                dt = time.perf_counter() - t
                err = f"{type(e).__name__}: {e}"
                print(f"   ✗ {err}")
            bl = baseline_chars(name)
            print(f"   {dt:6.1f} s | {len(text.strip()):5d} Z. (Baseline {bl}) "
                  f"| Peak {peak_rss_mb():.0f} MB")
            log_row(dict(pfad="B", seite=name, sekunden=round(dt, 2),
                         peak_rss_mb=round(peak_rss_mb()), zeichen=len(text.strip()),
                         baseline_zeichen=bl, prompt=prompt, fehler=err))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["A", "B"], required=True)
    ap.add_argument("--smoke", action="store_true", help="nur Seite 04 (Gate 2)")
    ap.add_argument("--pages", nargs="*", help="explizite Seitennamen")
    ap.add_argument("--try-prompts", action="store_true",
                    help="Pfad B: alle Prompt-Kandidaten testen")
    ap.add_argument("--prompt-idx", type=int, default=0)
    a = ap.parse_args()

    names = a.pages or (SMOKE if a.smoke else ORDER)
    if a.path == "A":
        run_a(names)
    else:
        prompts = PROMPTS_B if a.try_prompts else [PROMPTS_B[a.prompt_idx]]
        run_b(names, prompts)

    print(f"\n→ {CSV}")


if __name__ == "__main__":
    main()
