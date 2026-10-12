"""OCR for pages without a text layer (scanned PDFs).

Chain (decision 20 and the 0a results): Apple Vision on macOS for prose (free,
~0.8 s/page), tesseract as the fallback. Claude vision is never called at
ingest: pages that need it are only marked, and the workload is estimated.
Page-number strips use tesseract at 150 dpi (0b: higher resolutions upsample
low-ppi scans and hurt it), feeding the same label fit as born-digital pages.

Every engine returns lines as dicts {y, x, h, w, text, conf, par} in PDF
points with the origin at the top left; par is a paragraph id when the
engine knows one (tesseract), else None.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFT_SRC = os.path.join(HERE, "applevision.swift")
STRIP = 0.13  # fraction of the page height OCRed for page numbers
TIMEOUT = 120


def schola_home():
    return os.path.realpath(os.path.expanduser(
        os.environ.get("SCHOLA_HOME") or os.path.join("~", ".local", "share", "schola")))


def applevision_binary(build=True):
    """Path of the compiled Apple Vision tool, built on first use from
    applevision.swift (keyed by the source hash). None when unavailable."""
    if platform.system() != "Darwin" or not os.path.exists(SWIFT_SRC):
        return None
    tag = hashlib.sha256(open(SWIFT_SRC, "rb").read()).hexdigest()[:12]
    bindir = os.path.join(schola_home(), "bin")
    exe = os.path.join(bindir, f"applevision-{tag}")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    swiftc = shutil.which("swiftc")
    if not build or not swiftc:
        return None
    os.makedirs(bindir, mode=0o700, exist_ok=True)
    tmp = exe + f".tmp{os.getpid()}-{threading.get_ident()}"
    try:
        r = subprocess.run([swiftc, "-O", SWIFT_SRC, "-o", tmp], capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not os.path.exists(tmp):
        return None
    os.replace(tmp, exe)
    return exe


def engines_available():
    out = []
    if applevision_binary(build=False) or (platform.system() == "Darwin" and shutil.which("swiftc")):
        out.append("applevision")
    if shutil.which("tesseract"):
        out.append("tesseract")
    return out


def pick_engine(requested):
    """--ocr auto|applevision|tesseract|vision|none -> (engine or None, note)."""
    avail = engines_available()
    if requested in ("none", "vision"):
        return None, requested
    if requested == "auto":
        for e in ("applevision", "tesseract"):
            if e in avail:
                return e, None
        return None, "missing"
    if requested in avail:
        return requested, None
    return None, "missing"


def render(pdf_path, page, dpi, out_png, crop=None, gray=True):
    """Render one 1-based page (optionally a crop box in pixels) to PNG."""
    cmd = ["pdftoppm", "-r", str(dpi), "-png", "-f", str(page), "-l", str(page), "-singlefile"]
    if gray:
        cmd.append("-gray")
    if crop:
        x, y, w, h = crop
        cmd += ["-x", str(x), "-y", str(y), "-W", str(w), "-H", str(h)]
    stem = out_png[:-4] if out_png.endswith(".png") else out_png
    r = subprocess.run(cmd + [pdf_path, stem], capture_output=True, timeout=TIMEOUT)
    return r.returncode == 0 and os.path.exists(stem + ".png")


def run_applevision(png, page_w, page_h):
    exe = applevision_binary()
    if not exe:
        raise RuntimeError("Apple Vision tool unavailable")
    r = subprocess.run([exe, png], capture_output=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "applevision failed")
    lines = []
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        t = str(d.get("t", "")).strip()
        if t:
            lines.append({"y": float(d["y"]) * page_h, "x": float(d["x"]) * page_w, "h": float(d["h"]) * page_h,
                          "w": float(d["w"]) * page_w, "text": t, "conf": round(float(d.get("c", 0)), 3),
                          "par": None})
    return lines


def run_tesseract(png, dpi, psm="3", y_off_pt=0.0):
    r = subprocess.run(["tesseract", png, "stdout", "--psm", psm, "tsv"], capture_output=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[:200] or "tesseract failed")
    k = 72.0 / dpi
    groups = {}
    for row in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        key = (int(f[2]), int(f[3]), int(f[4]))  # block, paragraph, line
        x, y, w, h, conf = int(f[6]), int(f[7]), int(f[8]), int(f[9]), float(f[10])
        g = groups.setdefault(key, {"words": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h, "conf": []})
        g["words"].append(f[11].strip())
        g["x0"], g["y0"] = min(g["x0"], x), min(g["y0"], y)
        g["x1"], g["y1"] = max(g["x1"], x + w), max(g["y1"], y + h)
        g["conf"].append(conf)
    lines = []
    for (b, p, _), g in sorted(groups.items(), key=lambda kv: (kv[1]["y0"], kv[1]["x0"])):
        lines.append({"y": g["y0"] * k + y_off_pt, "x": g["x0"] * k, "h": (g["y1"] - g["y0"]) * k,
                      "w": (g["x1"] - g["x0"]) * k, "text": " ".join(g["words"]),
                      "conf": round(sum(g["conf"]) / len(g["conf"]) / 100.0, 3), "par": f"{b}.{p}"})
    return lines


def ocr_page(pdf_path, page, page_w, page_h, engine, dpi):
    """Full-page OCR of one 1-based page. Returns a list of line dicts."""
    with tempfile.TemporaryDirectory(prefix="schola-ocr-") as td:
        png = os.path.join(td, "page.png")
        if not render(pdf_path, page, dpi, png):
            raise RuntimeError(f"cannot render page {page}")
        if engine == "applevision":
            return run_applevision(png, page_w, page_h)
        return run_tesseract(png, dpi)


def strip_lines(pdf_path, page, page_w, page_h):
    """OCR only the top and bottom strips (page numbers) with tesseract."""
    if not shutil.which("tesseract"):
        return []
    dpi = 150
    r = dpi / 72.0
    W, H = int(page_w * r), int(page_h * r)
    strip = max(1, int(H * STRIP))
    out = []
    with tempfile.TemporaryDirectory(prefix="schola-strip-") as td:
        for y0 in (0, H - strip):
            png = os.path.join(td, f"s{y0}.png")
            if not render(pdf_path, page, dpi, png, crop=(0, y0, W, strip)):
                continue
            try:
                out += run_tesseract(png, dpi, psm="6", y_off_pt=y0 / r)
            except (RuntimeError, subprocess.TimeoutExpired):
                continue
    return out
