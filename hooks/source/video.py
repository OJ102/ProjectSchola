"""Video and caption adapter (increment 7): WebVTT (.vtt), SubRip (.srt), and YouTube captions.

Produces the standard store schema:
- source.json, spans.jsonl, outline.json, segments/, units.json, labels.json, assets/, report.json, .gitignore.
Satisfies the adapter contract (contract.py).
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from srcnorm import display_safe, neutralize, norm  # noqa: E402
import store as st  # noqa: E402
import units as sunits  # noqa: E402


class VideoParseError(Exception):
    pass


def rec(sid, segment, locator, kind, text, **extra):
    locator = st.clean_meta(locator, 200)
    text = neutralize(text)
    n = norm(text)
    return dict(id=sid, segment=segment, locator=locator, kind=kind, text=text, norm=n,
                sha1=hashlib.sha1(n.encode()).hexdigest(), **extra)


TS_RE = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"


def secs(m, off):
    h, mi, s, ms = (m.group(off + i) for i in range(4))
    if int(mi) > 59 or int(s) > 59:
        return None
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def hms(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def parse_clock(s):
    parts = [int(x) for x in s.split(":")]
    if len(parts) == 2:
        parts = [0] + parts
    h, m, sec = parts
    return None if m > 59 or sec > 59 else h * 3600 + m * 60 + sec


def video_link(url, start):
    """Deep link at a time: only http(s); the t parameter goes in query, fragment kept."""
    if not url:
        return None
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != "t"]
    q.append(("t", f"{int(start)}s"))
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), u.fragment))


def parse_vtt_or_srt(text):
    txt = text.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    bad = 0
    blocks = re.split(r"\n\s*\n", txt)

    for block in blocks:
        lines = block.strip("\n").split("\n")
        for i, ln in enumerate(lines):
            m = re.match(TS_RE + r"\s*-->\s*" + TS_RE, ln.strip())
            if m:
                a, b = secs(m, 1), secs(m, 5)
                if a is None or b is None:
                    bad += 1
                    break
                body = " ".join(lines[i + 1:])
                body = re.sub(r"<[^>]+>", "", body)  # voice/class tags
                body = html.unescape(re.sub(r"\s+", " ", body)).strip()
                if body:
                    cues.append((a, b, body))
                break

    cues.sort(key=lambda c: c[0])
    return cues, bad, txt


def build(source_path_or_url, build_dir, opts=None, cache_dir=None, log=print):
    opts = opts or {}
    os.makedirs(build_dir, exist_ok=True)
    st.check_deadline(opts.get("deadline"), "video processing")
    video_url = opts.get("url")

    if os.path.exists(source_path_or_url):
        with open(source_path_or_url, "rb") as f:
            raw_bytes = f.read()
        txt = raw_bytes.decode("utf-8-sig", "replace")
        origin = os.path.abspath(source_path_or_url)
        sha = hashlib.sha256(raw_bytes).hexdigest()
        title = st.clean_meta(os.path.splitext(os.path.basename(source_path_or_url))[0], 120)
    else:
        raise VideoParseError(f"caption source file not found: {source_path_or_url}")

    cues, bad_ts, full_txt = parse_vtt_or_srt(txt)
    if not cues:
        raise VideoParseError("no valid caption cues found in video source")

    # Group into spans and 1-minute windowed segments
    spans_data = []
    used_ids = {}
    segments_dict = {}  # seg_id -> list of spans

    for start, end, body in cues:
        st.check_deadline(opts.get("deadline"), "video cue processing")
        # Deduplicate identical consecutive cues from auto-captions
        if spans_data and norm(body) == spans_data[-1]["norm"]:
            continue
        seg_id = f"w{int(start // 60):04d}"
        base_id = f"{seg_id}.{int(start % 60):02d}"
        used_ids[base_id] = used_ids.get(base_id, 0) + 1
        sid = base_id if used_ids[base_id] == 1 else f"{base_id}_{used_ids[base_id]}"

        loc = hms(start)
        extra = {"start": start, "end": end, "segment_window": seg_id}
        link = video_link(video_url, start)
        if link:
            extra["link"] = link

        s_rec = rec(sid, seg_id, loc, "cue", body, **extra)
        spans_data.append(s_rec)
        segments_dict.setdefault(seg_id, []).append(s_rec)

    # Outline from NOTE markers, chapters, or minute ranges
    outline_data = []
    notes = "\n".join(l for l in full_txt.split("\n") if l.startswith("NOTE"))
    oidx = 1
    for m in re.finditer(r"(\d{1,2}:\d{2}(?::\d{2})?)\s+([^,\n]+)", notes):
        t = parse_clock(m.group(1))
        if t is not None:
            m_span = next((s for s in spans_data if s.get("start", 0) >= t), spans_data[-1] if spans_data else None)
            outline_data.append({
                "id": f"o{oidx:03d}",
                "title": st.clean_meta(m.group(2).strip().rstrip(".").strip(), 120),
                "depth": 1,
                "start_span": m_span["id"] if m_span else None
            })
            oidx += 1

    if not outline_data:
        # Synthesize top-level outline
        outline_data.append({
            "id": "o001",
            "title": f"Video: {title}"[:120],
            "depth": 1,
            "start_span": spans_data[0]["id"]
        })

    # Segments directory
    seg_dir = os.path.join(build_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    seg_index = []

    for seg_id in sorted(segments_dict.keys()):
        s_spans = segments_dict[seg_id]
        path = os.path.join(seg_dir, f"{seg_id}.md")
        lines = [f"[{s['id']}] " + s["text"].replace("\n", " ⏎ ") for s in s_spans]
        st.atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
        seg_toks = sum(len(s["text"]) // 4 for s in s_spans)
        seg_index.append({
            "id": seg_id,
            "locator": st.clean_meta(f"{title} [{seg_id}]", 120),
            "kind": "text",
            "tokens": seg_toks,
            "has_figures": False,
            "needs_vision": False,
            "spans": len(s_spans)
        })

    st.write_json(os.path.join(seg_dir, "index.json"), seg_index)

    # Write spans.jsonl
    with open(os.path.join(build_dir, "spans.jsonl"), "w", encoding="utf-8") as f:
        for s in spans_data:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # Units & Outline
    segments_summary = [(s["id"], s["locator"]) for s in seg_index]
    if outline_data and outline_data[0]["start_span"] is None:
        outline_data[0]["start_span"] = spans_data[0]["id"]

    st.check_deadline(opts.get("deadline"), "video building units")
    units_list, final_outline = sunits.build_units(spans_data, outline_data, {}, segments_summary)

    st.write_json(os.path.join(build_dir, "outline.json"), final_outline)
    st.write_json(os.path.join(build_dir, "units.json"), units_list)

    # Labels & Assets
    labels_list = [{"pdf_index": i, "label": str(i), "label_source": "synthetic"} for i in range(1, len(seg_index) + 1)]
    labels_data = {
        "pages": labels_list,
        "label_to_pdf": {str(i): [i] for i in range(1, len(seg_index) + 1)}
    }
    st.write_json(os.path.join(build_dir, "labels.json"), labels_data)

    assets_dir = os.path.join(build_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    st.write_json(os.path.join(assets_dir, "index.json"), [])

    # Report & Source metadata
    total_chars = sum(len(s["text"]) for s in spans_data)
    report_data = {
        "body_chars": total_chars,
        "captured_chars": total_chars,
        "spans": len(spans_data),
        "segments": len(seg_index),
        "units": len(units_list),
        "bad_timestamps": bad_ts,
        "capture_ratio": 1.0,
        "validation": {"sampled": 0, "mismatched": [], "mismatch_rate": 0.0}
    }
    st.write_json(os.path.join(build_dir, "report.json"), report_data)

    source_meta = {
        "type": "video",
        "origin": origin,
        "title": title,
        "adapter": "video",
        "ingest_version": 1,
        "sha256": sha
    }
    if video_url:
        source_meta["url"] = video_url
    st.write_json(os.path.join(build_dir, "source.json"), source_meta)

    with open(os.path.join(build_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("# Schola source store: personal copies of copyrighted sources. Never commit.\n*\n")

    return report_data
