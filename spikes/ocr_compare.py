"""Spike 0a: compare OCR / figure methods on the mixed sample set.

usage: python3 spikes/ocr_compare.py [--methods tesseract,applevision,docling,vision] [--samples a,b]

Samples come from spikes/ocr_samples.sh (spikes/out/ocr/samples). Outputs go
to spikes/out/ocr/<method>/<sample>.md, plus spikes/out/ocr/results.json with
time, tokens/cost (vision) and word accuracy against *.truth.txt where one
exists. Figures (chart, diagram) and LaTeX are scored by hand in RESULTS.md.
Methods:
  A vision      claude -p (sonnet) Reads the image, returns Markdown + LaTeX + figure notes
  B docling     Docling in .spike-venv (layout + tables + its default OCR)
  D applevision VNRecognizeTextRequest, accurate (spikes/applevision.swift)
  - tesseract   tesseract 5, psm 3
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out", "ocr")
SAMPLES = os.path.join(OUT, "samples")
VENV_PY = os.path.join(ROOT, ".spike-venv", "bin", "python")
AV = os.path.join(OUT, "bin", "applevision")

VISION_PROMPT = (
    "Read the image at {path} with the Read tool. Transcribe ALL text faithfully in reading order as Markdown. "
    "Write math as LaTeX ($...$ inline, $$...$$ display). Write tables as Markdown tables. "
    "If the image is a chart or diagram, then add a section '## Figure' with: what it shows; axes, units and "
    "data ranges or node/arrow structure; every label; and the takeaway. Mark anything you cannot read as [unclear]. "
    "Output only the transcription.")

DOCLING = r'''
import sys, time
from docling.document_converter import DocumentConverter
t0 = time.time()
conv = DocumentConverter()
t1 = time.time()
res = conv.convert(sys.argv[1])
sys.stdout.write(res.document.export_to_markdown())
sys.stderr.write(f"\nINIT {t1-t0:.1f} CONVERT {time.time()-t1:.1f}\n")
'''


def image_of(name):
    for ext in (".png", ".jpg"):
        p = os.path.join(SAMPLES, name + ext)
        if os.path.exists(p):
            return p
    return None


def run_tesseract(img):
    r = subprocess.run(["tesseract", img, "stdout", "--psm", "3"], capture_output=True, text=True)
    return r.stdout, {}


def run_applevision(img):
    r = subprocess.run([AV, img], capture_output=True, text=True)
    return r.stdout, {}


def run_docling(img):
    r = subprocess.run([VENV_PY, "-c", DOCLING, img], capture_output=True, text=True, timeout=1800)
    m = re.search(r"INIT ([\d.]+) CONVERT ([\d.]+)", r.stderr)
    extra = {"init_s": float(m.group(1)), "convert_s": float(m.group(2))} if m else {"stderr": r.stderr[-400:]}
    return r.stdout, extra


def run_vision(img):
    cmd = ["claude", "-p", "--model", "sonnet", "--output-format", "json", "--allowedTools", "Read"]
    r = subprocess.run(cmd, input=VISION_PROMPT.format(path=img), capture_output=True, text=True, cwd=ROOT, timeout=900)
    d = json.loads(r.stdout)
    u = d.get("usage", {})
    return d.get("result", ""), {"input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                                 "output_tokens": u.get("output_tokens", 0), "cost_usd": d.get("total_cost_usd")}


def words(t):
    t = t.replace("‐", "-").replace("’", "'").replace("ﬁ", "fi").replace("ﬂ", "fl")
    t = re.sub(r"[`*_#|>$\\]", " ", t)
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", t.lower())


def wer(ref, hyp):
    """Word error rate via edit distance."""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ref[i - 1] != hyp[j - 1]))
        prev = cur
    return prev[m] / max(n, 1)


RUNNERS = {"tesseract": run_tesseract, "applevision": run_applevision, "docling": run_docling, "vision": run_vision}


def main():
    a = sys.argv[1:]
    methods = a[a.index("--methods") + 1].split(",") if "--methods" in a else list(RUNNERS)
    names = a[a.index("--samples") + 1].split(",") if "--samples" in a else \
        ["prose", "scan", "math", "table", "chart", "diagram", "slide", "board"]
    res_path = os.path.join(OUT, "results.json")
    results = json.load(open(res_path)) if os.path.exists(res_path) else {}
    for meth in methods:
        os.makedirs(os.path.join(OUT, meth), exist_ok=True)
        for name in names:
            img = image_of(name)
            if not img:
                continue
            t0 = time.time()
            try:
                text, extra = RUNNERS[meth](img)
            except Exception as e:  # record and continue: a spike, not a pipeline
                text, extra = "", {"error": str(e)[:300]}
            secs = round(time.time() - t0, 1)
            open(os.path.join(OUT, meth, name + ".md"), "w").write(text)
            rec = {"seconds": secs, "chars": len(text), **extra}
            tp = os.path.join(SAMPLES, name + ".truth.txt")
            if os.path.exists(tp):
                ref = words(open(tp).read())
                rec["word_acc"] = round(1 - wer(ref, words(text)), 3)
            results.setdefault(name, {})[meth] = rec
            print(f"{meth:<12} {name:<8} {secs:>6}s  acc={rec.get('word_acc', '-')}  {extra}")
            json.dump(results, open(res_path, "w"), indent=1)


if __name__ == "__main__":
    main()
