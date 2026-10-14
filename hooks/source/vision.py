"""Lazy vision rendering for source-mode units (Milestone B2).

Renders images/pages on demand for units whose segments or figures have
"needs_vision": True. Output PNGs are bounded by SCHOLA_MAX_RENDER_MP (default 4 MP)
and SCHOLA_OCR_PAGE_TIMEOUT_S (Decision 32 caps).

Summary is saved to <store>/vision/<unit_id>.json.
"""
import datetime
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as st  # noqa: E402


def env_num(name, default, lo, hi):
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        v = default
    return min(max(v, lo), hi)


DEFAULT_MAX_MP = 4.0
TIMEOUT = env_num("SCHOLA_OCR_PAGE_TIMEOUT_S", 120, 1, 1800)


def png_wh(p):
    """Read width and height of a PNG file from its header."""
    try:
        with open(p, "rb") as f:
            b = f.read(24)
            if b[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", b[16:24])
    except OSError:
        pass
    return None


def get_unit_segments_and_spans(store, unit):
    """Return (set_of_segment_ids, set_of_span_ids) for the given unit."""
    spans_path = os.path.join(store, "spans.jsonl")
    if not os.path.isfile(spans_path):
        return set(), set()
    s_start, s_end = unit.get("span_start"), unit.get("span_end")
    segments = set()
    spans = set()
    on = False
    with open(spans_path, encoding="utf-8") as f:
        for line in f:
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if s["id"] == s_start:
                on = True
            if on:
                spans.add(s["id"])
                if s.get("segment"):
                    segments.add(s["segment"])
            if s["id"] == s_end:
                break
    return segments, spans


def render_page(pdf_path, page, out_png, max_mp, timeout=TIMEOUT):
    """Render a 1-based PDF page to out_png, downscaling if > max_mp."""
    max_pixels = int(max_mp * 1_000_000)
    stem = out_png[:-4] if out_png.endswith(".png") else out_png
    actual_png = stem + ".png"
    try:
        # 1. First attempt at 150 dpi
        cmd = ["pdftoppm", "-r", "150", "-png", "-f", str(page), "-l", str(page), "-singlefile", pdf_path, stem]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            if os.path.exists(actual_png):
                try: os.unlink(actual_png)
                except OSError: pass
            return False, None, None

        if r.returncode != 0 or not os.path.exists(actual_png):
            if os.path.exists(actual_png):
                try: os.unlink(actual_png)
                except OSError: pass
            return False, None, None

        wh = png_wh(actual_png)
        if not wh:
            if os.path.exists(actual_png):
                try: os.unlink(actual_png)
                except OSError: pass
            return False, None, None

        w, h = wh
        if w * h > max_pixels:
            # Downscale to fit within max_mp
            scale = (max_pixels / (w * h)) ** 0.5
            new_dpi = max(1, int(150 * scale))
            cmd = ["pdftoppm", "-r", str(new_dpi), "-png", "-f", str(page), "-l", str(page), "-singlefile", pdf_path, stem]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                if os.path.exists(actual_png):
                    try: os.unlink(actual_png)
                    except OSError: pass
                return False, None, None

            if r.returncode != 0 or not os.path.exists(actual_png):
                if os.path.exists(actual_png):
                    try: os.unlink(actual_png)
                    except OSError: pass
                return False, None, None

            wh = png_wh(actual_png)
            if not wh or wh[0] * wh[1] > int(max_pixels * 1.05):
                if os.path.exists(actual_png):
                    try: os.unlink(actual_png)
                    except OSError: pass
                return False, None, None
            w, h = wh

        return True, w, h
    except Exception:
        if os.path.exists(actual_png):
            try: os.unlink(actual_png)
            except OSError: pass
        return False, None, None


def render_unit_vision(store, unit, max_mp=None, timeout=None):
    """Find all segments/assets needing vision in unit, render on-demand, write vision/<uid>.json."""
    if max_mp is None:
        max_mp = env_num("SCHOLA_MAX_RENDER_MP", DEFAULT_MAX_MP, 0.1, 400)
    if timeout is None:
        timeout = TIMEOUT

    if isinstance(unit, str):
        units_json = os.path.join(store, "units.json")
        if os.path.isfile(units_json):
            with open(units_json, encoding="utf-8") as f:
                all_u = json.load(f)
            unit = next((x for x in all_u if x["id"] == unit), {"id": unit})
        else:
            unit = {"id": unit}

    raw_uid = str(unit.get("id", ""))
    uid = st.check_slug(os.path.basename(raw_uid.rstrip("/")))
    unit_segs, unit_spans = get_unit_segments_and_spans(store, unit)

    # 1. Identify items needing vision
    v_items = []

    # From segments/index.json
    seg_idx_path = os.path.join(store, "segments", "index.json")
    if os.path.isfile(seg_idx_path):
        try:
            with open(seg_idx_path, encoding="utf-8") as f:
                seg_idx = json.load(f)
            for s in seg_idx:
                if (s.get("id") in unit_segs or not unit_segs) and s.get("needs_vision"):
                    pg = s.get("pdf_index")
                    if pg is None and s.get("id", "").startswith("p") and s["id"][1:].isdigit():
                        pg = int(s["id"][1:])
                    v_items.append({"id": s["id"], "kind": "segment", "pdf_index": pg})
        except (OSError, ValueError):
            pass

    # From assets/index.json
    asset_idx_path = os.path.join(store, "assets", "index.json")
    if os.path.isfile(asset_idx_path):
        try:
            with open(asset_idx_path, encoding="utf-8") as f:
                asset_idx = json.load(f)
            for a in asset_idx:
                if not a.get("needs_vision"):
                    continue
                match = (a.get("caption_span") in unit_spans)
                if not match and a.get("pdf_index") is not None:
                    # check if this page is in unit range
                    loc_s = unit.get("loc_start")
                    loc_e = unit.get("loc_end")
                    if isinstance(loc_s, int) and isinstance(loc_e, int):
                        match = (loc_s <= a["pdf_index"] <= loc_e)
                if match:
                    v_items.append({"id": a["id"], "kind": "asset", "file": a.get("file", f"{a['id']}.png"),
                                    "pdf_index": a.get("pdf_index")})
        except (OSError, ValueError):
            pass

    # Read source origin
    src_json_path = os.path.join(store, "source.json")
    source_origin = None
    if os.path.isfile(src_json_path):
        try:
            with open(src_json_path, encoding="utf-8") as f:
                source_origin = json.load(f).get("origin")
        except (OSError, ValueError):
            pass

    store_real = os.path.realpath(store)
    vdir = os.path.join(store_real, "vision", uid)
    adir = os.path.join(store_real, "assets")
    os.makedirs(vdir, exist_ok=True)
    os.makedirs(adir, exist_ok=True)

    summary_items = []
    for item in v_items:
        iid = st.check_slug(os.path.basename(str(item["id"]).rstrip("/")))
        pg = item.get("pdf_index")
        if item["kind"] == "asset":
            fname = os.path.basename(str(item.get("file", f"{iid}.png")).rstrip("/"))
            target_path = st.inside(store_real, os.path.join(adir, fname))
        else:
            target_path = st.inside(store_real, os.path.join(vdir, f"{iid}.png"))

        # If already exists and valid
        wh = png_wh(target_path)
        if wh and wh[0] * wh[1] <= int(max_mp * 1_000_000 * 1.05):
            summary_items.append({
                "id": iid, "kind": item["kind"], "pdf_index": pg,
                "file": os.path.relpath(target_path, store_real),
                "width": wh[0], "height": wh[1],
                "mp": round(wh[0] * wh[1] / 1e6, 3), "status": "cached"
            })
            continue

        if not source_origin or not os.path.isfile(source_origin) or pg is None:
            summary_items.append({
                "id": iid, "kind": item["kind"], "pdf_index": pg,
                "file": os.path.relpath(target_path, store_real),
                "status": "skipped_no_source"
            })
            continue

        ok, w, h = render_page(source_origin, pg, target_path, max_mp, timeout=timeout)
        if ok and w and h:
            summary_items.append({
                "id": iid, "kind": item["kind"], "pdf_index": pg,
                "file": os.path.relpath(target_path, store_real),
                "width": w, "height": h,
                "mp": round(w * h / 1e6, 3), "status": "rendered"
            })
        else:
            summary_items.append({
                "id": iid, "kind": item["kind"], "pdf_index": pg,
                "file": os.path.relpath(target_path, store_real),
                "status": "failed"
            })

    report = {
        "unit": uid,
        "rendered_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "max_render_mp": max_mp,
        "items": summary_items
    }
    st.write_json(os.path.join(store_real, "vision", f"{uid}.json"), report)
    return summary_items


def main():
    if len(sys.argv) < 3:
        sys.exit(f"usage: python3 {sys.argv[0]} <store> <unit-id> [--max-mp N]")
    store_path, unit_id = sys.argv[1], sys.argv[2]
    max_mp = None
    if "--max-mp" in sys.argv:
        idx = sys.argv.index("--max-mp")
        if idx + 1 < len(sys.argv):
            max_mp = float(sys.argv[idx + 1])
    items = render_unit_vision(store_path, unit_id, max_mp=max_mp)
    print(f"Rendered vision for {unit_id}: {len(items)} items processed")
    for it in items:
        print(f"  {it['id']} ({it['kind']}): {it['status']} {it.get('file', '')} {it.get('mp', '')} MP")


if __name__ == "__main__":
    main()
