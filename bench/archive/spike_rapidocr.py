"""Spike #62: measure RapidOCR (ONNX Runtime) with PP-OCR models on page images.

Run one process per configuration so the peak RSS belongs to that configuration:

    python bench/archive/spike_rapidocr.py --name v5-mobile-full-t4 --det mobile \
        --max-side 4000 --threads 4 --pages p1.png p2.png --repeat-page p1.png

Models are downloaded by RapidOCR into --models on first use. Writes
<out>/<name>/result.json plus one JSON per page with lines, boxes, and scores.
Results are recorded in bench/ERGEBNIS.md (Nachtrag 18).
"""
import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
import unicodedata

T_PROCESS = time.monotonic()

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--version", default="PP-OCRv5", choices=["PP-OCRv5", "PP-OCRv6"])
ap.add_argument("--det", default="mobile")
ap.add_argument("--rec", default="mobile")
ap.add_argument("--threads", type=int, default=1)
ap.add_argument("--max-side", type=int, default=2000)
ap.add_argument("--cls", action="store_true")
ap.add_argument("--word-box", action="store_true")
ap.add_argument("--pages", nargs="+", required=True)
ap.add_argument("--repeat-page", default=None)
ap.add_argument("--repeat", type=int, default=5)
ap.add_argument("--models", default="models")
ap.add_argument("--out", default="runs")
a = ap.parse_args()


def swap_state():
    usage = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout.strip()
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    stats = {}
    for line in vm.splitlines():
        if line.startswith(("Swapins", "Swapouts", "Pageouts")):
            key, value = line.split(":")
            stats[key] = int(value.strip().rstrip("."))
    return {"swapusage": usage, **stats}


swap_before = swap_state()
t_import = time.monotonic()
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR  # noqa: E402
import onnxruntime  # noqa: E402
t_import = time.monotonic() - t_import

version = OCRVersion(a.version)
params = {
    "Global.model_root_dir": a.models,
    "Global.log_level": "warning",
    "Global.max_side_len": a.max_side,
    "Global.use_cls": a.cls,
    "Global.return_word_box": a.word_box,
    "EngineConfig.onnxruntime.intra_op_num_threads": a.threads,
    "EngineConfig.onnxruntime.inter_op_num_threads": a.threads,
    "Det.engine_type": EngineType.ONNXRUNTIME,
    # The PP-OCRv5 detector is multilingual; RapidOCR files it under "ch".
    "Det.lang_type": LangDet.CH,
    "Det.model_type": ModelType(a.det),
    "Det.ocr_version": version,
    "Rec.engine_type": EngineType.ONNXRUNTIME,
    # German needs the Latin PP-OCRv5 recogniser; PP-OCRv6 ships one multilingual
    # recogniser, which RapidOCR 3.9.2 also files under "ch".
    "Rec.lang_type": LangRec.LATIN if version is OCRVersion.PPOCRV5 else LangRec.CH,
    "Rec.model_type": ModelType(a.rec),
    "Rec.ocr_version": version,
}

t_init = time.monotonic()
engine = RapidOCR(params=params)
t_init = time.monotonic() - t_init
t_cold = time.monotonic() - T_PROCESS

run_dir = os.path.join(a.out, a.name)
os.makedirs(run_dir, exist_ok=True)


def normalized_text(txts):
    return "\n".join(unicodedata.normalize("NFC", " ".join(t.split())) for t in txts)


def run_page(path, tag):
    t = time.monotonic()
    res = engine(path)
    dt = time.monotonic() - t
    txts = list(res.txts or ())
    boxes = [[[round(float(x)), round(float(y))] for x, y in box]
             for box in (res.boxes if res.boxes is not None else [])]
    scores = [round(float(s), 4) for s in (res.scores or ())]
    text = normalized_text(txts)
    record = {
        "page": os.path.basename(path), "seconds": round(dt, 3), "lines": len(txts),
        "chars": sum(len(t) for t in txts),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        "lines_sha256": hashlib.sha256(json.dumps([txts, boxes], ensure_ascii=False).encode()).hexdigest()[:16],
        "umlauts": sum(text.count(c) for c in "äöüÄÖÜ"), "eszett": text.count("ß"),
        "section_sign": text.count("§"),
        "word_results": len(res.word_results) if a.word_box and res.word_results else None,
    }
    with open(os.path.join(run_dir, f"{tag}.json"), "w", encoding="utf-8") as f:
        json.dump({**record, "txts": txts, "boxes": boxes, "scores": scores}, f, ensure_ascii=False)
    return record


pages = [run_page(p, os.path.basename(p).rsplit(".", 1)[0]) for p in a.pages]
repeats = [run_page(a.repeat_page, f"repeat-{i}") for i in range(a.repeat)] if a.repeat_page else []

peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
result = {
    "config": vars(a), "onnxruntime": onnxruntime.__version__,
    "providers": onnxruntime.get_available_providers(),
    "import_s": round(t_import, 2), "init_s": round(t_init, 2), "cold_to_ready_s": round(t_cold, 2),
    "first_page_s": pages[0]["seconds"], "first_page": pages[0], "warm_pages": pages[1:],
    "repeats": repeats,
    "repeat_text_hashes": sorted({r["text_sha256"] for r in repeats}),
    "repeat_line_hashes": sorted({r["lines_sha256"] for r in repeats}),
    "peak_rss_mb": round(peak_rss / 2**20), "swap_before": swap_before, "swap_after": swap_state(),
    "models": sorted(os.listdir(a.models)),
}
with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(json.dumps({k: result[k] for k in ("import_s", "init_s", "cold_to_ready_s", "peak_rss_mb")}))
for r in pages + repeats:
    print(f'{r["page"]:36s} {r["seconds"]:7.2f}s lines={r["lines"]:4d} chars={r["chars"]:5d} '
          f'ä/ö/ü={r["umlauts"]:3d} ß={r["eszett"]:2d} §={r["section_sign"]:2d} '
          f'{r["text_sha256"]} {r["lines_sha256"]}')
print("repeat hashes:", result["repeat_text_hashes"], result["repeat_line_hashes"])
print("swap:", swap_before, "->", result["swap_after"])
