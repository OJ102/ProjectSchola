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
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from srcnorm import display_safe, neutralize, norm  # noqa: E402
import store as st  # noqa: E402
import units as sunits  # noqa: E402


class VideoParseError(Exception):
    pass


VIDEO_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be",
    "vimeo.com", "www.vimeo.com", "player.vimeo.com",
}


def check_timeout(deadline, default_timeout, stage="video processing"):
    st.check_deadline(deadline, stage)
    if deadline:
        left = deadline - time.monotonic()
        if left <= 0:
            raise st.DeadlineExceeded(f"the ingest time limit ran out during {stage}")
        return min(left, default_timeout)
    return default_timeout


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
    yt_chapters = []

    if source_path_or_url.startswith(("http://", "https://")):
        video_url = source_path_or_url
        origin = source_path_or_url
        sha = hashlib.sha256(source_path_or_url.encode()).hexdigest()
        u = urllib.parse.urlsplit(source_path_or_url)
        host = (u.hostname or "").lower()
        if host not in VIDEO_HOSTS or u.username or u.password:
            raise VideoParseError(f"unsupported video URL or host: {u.hostname or 'unknown'}")
        if not shutil.which("yt-dlp"):
            raise VideoParseError("yt-dlp is required to process video URLs (install with: brew install yt-dlp)")

        # 1. Fetch metadata
        try:
            t = check_timeout(opts.get("deadline"), 60, "fetching video metadata")
            cmd_meta = ["yt-dlp", "--ignore-config", "--no-playlist", "--dump-json", "--skip-download", source_path_or_url]
            r_meta = subprocess.run(cmd_meta, capture_output=True, text=True, timeout=t)
            yt_meta = json.loads(r_meta.stdout) if r_meta.returncode == 0 else {}
        except subprocess.TimeoutExpired:
            st.check_deadline(opts.get("deadline"), "fetching video metadata")
            raise VideoParseError("fetching video metadata timed out") from None
        title = st.clean_meta(yt_meta.get("title") or "Video", 120)
        yt_chapters = yt_meta.get("chapters") or []

        # 2. Download subtitles
        sub_out = os.path.join(build_dir, "subs")
        try:
            t = check_timeout(opts.get("deadline"), 120, "downloading video subtitles")
            cmd_subs = ["yt-dlp", "--ignore-config", "--no-playlist", "--write-subs", "--write-auto-subs", "--sub-format", "vtt",
                        "--skip-download", "-o", sub_out, source_path_or_url]
            subprocess.run(cmd_subs, capture_output=True, timeout=t)
        except subprocess.TimeoutExpired:
            st.check_deadline(opts.get("deadline"), "downloading video subtitles")
            raise VideoParseError("downloading video subtitles timed out") from None
        vtt_file = None
        for fn in sorted(os.listdir(build_dir)):
            if fn.startswith("subs") and fn.endswith(".vtt"):
                vtt_file = os.path.join(build_dir, fn)
                break
        if not vtt_file or not os.path.exists(vtt_file):
            raise VideoParseError("no subtitle or caption track found for video URL")
        with open(vtt_file, "rb") as f:
            raw_bytes = f.read()
        txt = raw_bytes.decode("utf-8-sig", "replace")

    elif os.path.exists(source_path_or_url):
        if not (os.path.isfile(source_path_or_url) and stat.S_ISREG(os.stat(source_path_or_url).st_mode)):
            raise VideoParseError(f"video source is not a regular file: {source_path_or_url}")
        ext = os.path.splitext(source_path_or_url)[1].lower()
        origin = os.path.abspath(source_path_or_url)
        title = st.clean_meta(os.path.splitext(os.path.basename(source_path_or_url))[0], 120)

        if ext in (".vtt", ".srt"):
            if os.path.getsize(source_path_or_url) > 50 * 1024 * 1024:
                raise VideoParseError(f"subtitle file exceeds maximum size limit of 50 MB: {source_path_or_url}")
            with open(source_path_or_url, "rb") as f:
                raw_bytes = f.read()
            txt = raw_bytes.decode("utf-8-sig", "replace")
            sha = hashlib.sha256(raw_bytes).hexdigest()
        elif ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4a"):
            if not shutil.which("ffmpeg"):
                raise VideoParseError("ffmpeg is required to process video media files (install with: brew install ffmpeg)")
            if not shutil.which("ffprobe"):
                raise VideoParseError("ffprobe is required to process video media files (install with: brew install ffmpeg)")

            # Probe format using ffprobe before calling ffmpeg
            cmd_probe = ["ffprobe", "-protocol_whitelist", "file,crypto,data",
                         "-v", "error", "-show_entries", "format=format_name",
                         "-of", "default=noprint_wrappers=1:nokey=1", "--", origin]
            try:
                t_probe = check_timeout(opts.get("deadline"), 30, "probing video container format")
                r_probe = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=t_probe)
            except subprocess.TimeoutExpired:
                st.check_deadline(opts.get("deadline"), "probing video container format")
                raise VideoParseError("probing video container format timed out") from None

            if r_probe.returncode != 0:
                raise VideoParseError(f"ffprobe failed to probe video file: {r_probe.stderr.strip() or 'unknown error'}")

            probed_format = r_probe.stdout.strip()
            if not probed_format:
                raise VideoParseError("ffprobe returned empty format for video file")

            probed_lower = probed_format.lower()
            dangerous = ("hls", "applehttp", "concat", "subfile", "tee", "image2", "pipe")
            if any(d in probed_lower for d in dangerous):
                raise VideoParseError(f"prohibited video format or demuxer detected: {probed_format}")

            fmt_parts = [p.strip() for p in probed_lower.split(",")]
            if ext in (".mp4", ".mov", ".m4a"):
                allowed = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")
                if not any(p in allowed or "mp4" in p or "mov" in p or "m4a" in p for p in fmt_parts):
                    raise VideoParseError(f"probed format '{probed_format}' does not match expected container format for {ext}")
                demuxer = "mov"
            elif ext in (".mkv", ".webm"):
                allowed = ("matroska", "webm")
                if not any(p in allowed or "matroska" in p or "webm" in p for p in fmt_parts):
                    raise VideoParseError(f"probed format '{probed_format}' does not match expected container format for {ext}")
                demuxer = "matroska"
            elif ext == ".avi":
                if not any(p == "avi" for p in fmt_parts):
                    raise VideoParseError(f"probed format '{probed_format}' does not match expected container format for {ext}")
                demuxer = "avi"
            else:
                raise VideoParseError(f"unsupported video file format: {ext}")

            h = hashlib.sha256()
            with open(origin, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            sha = h.hexdigest()

            # Try extracting embedded subtitle track
            vtt_out = os.path.join(build_dir, "embedded.vtt")
            cmd_sub = ["ffmpeg", "-nostdin", "-protocol_whitelist", "file,crypto,data",
                       "-y", "-f", demuxer, "-i", origin, "-map", "0:s:0", vtt_out]
            try:
                t = check_timeout(opts.get("deadline"), 30, "extracting embedded subtitles")
                r_sub = subprocess.run(cmd_sub, capture_output=True, timeout=t)
            except subprocess.TimeoutExpired:
                r_sub = None

            if r_sub and r_sub.returncode == 0 and os.path.exists(vtt_out) and os.path.getsize(vtt_out) > 20:
                with open(vtt_out, "rb") as f:
                    txt = f.read().decode("utf-8-sig", "replace")
            else:
                # Audio extraction + whisper transcription
                audio_wav = os.path.join(build_dir, "audio.wav")
                cmd_aud = ["ffmpeg", "-nostdin", "-protocol_whitelist", "file,crypto,data",
                           "-y", "-f", demuxer, "-i", origin, "-vn", "-ar", "16000", "-ac", "1", audio_wav]
                try:
                    t = check_timeout(opts.get("deadline"), 120, "extracting audio")
                    subprocess.run(cmd_aud, capture_output=True, timeout=t)
                except subprocess.TimeoutExpired:
                    st.check_deadline(opts.get("deadline"), "extracting audio")
                    raise VideoParseError("extracting audio timed out") from None

                whisper_bin = shutil.which("whisper") or shutil.which("whisper-cpp")
                if whisper_bin:
                    cmd_w = [whisper_bin, audio_wav, "--output_format", "vtt", "--output_dir", build_dir]
                    try:
                        t = check_timeout(opts.get("deadline"), 300, "whisper transcription")
                        subprocess.run(cmd_w, capture_output=True, timeout=t)
                    except subprocess.TimeoutExpired:
                        st.check_deadline(opts.get("deadline"), "whisper transcription")
                        raise VideoParseError("whisper transcription timed out") from None
                    w_vtt = os.path.splitext(audio_wav)[0] + ".vtt"
                    if os.path.exists(w_vtt):
                        with open(w_vtt, "rb") as f:
                            txt = f.read().decode("utf-8-sig", "replace")
                    else:
                        raise VideoParseError("whisper failed to transcribe video audio")
                else:
                    raise VideoParseError("video media file has no caption track and whisper is not installed (install with: pip install openai-whisper or brew install whisper-cpp)")

            # Keyframes extraction via ffmpeg
            assets_dir = os.path.join(build_dir, "assets")
            os.makedirs(assets_dir, exist_ok=True)
            kf_pattern = os.path.join(assets_dir, "kf_%04d.jpg")
            cmd_kf = ["ffmpeg", "-nostdin", "-protocol_whitelist", "file,crypto,data",
                      "-y", "-f", demuxer, "-i", origin, "-vf", "select='gt(scene,0.3)',showinfo",
                      "-fps_mode", "vfr", "-frames:v", "30", "-q:v", "3", kf_pattern]
            try:
                t = check_timeout(opts.get("deadline"), 60, "keyframe extraction")
                r_kf = subprocess.run(cmd_kf, capture_output=True, timeout=t)
                if r_kf.returncode != 0:
                    cmd_kf_old = ["ffmpeg", "-nostdin", "-protocol_whitelist", "file,crypto,data",
                                  "-y", "-f", demuxer, "-i", origin, "-vf", "select='gt(scene,0.3)',showinfo",
                                  "-vsync", "vfr", "-frames:v", "30", "-q:v", "3", kf_pattern]
                    subprocess.run(cmd_kf_old, capture_output=True, timeout=t)
            except subprocess.TimeoutExpired:
                st.check_deadline(opts.get("deadline"), "keyframe extraction")
            except st.DeadlineExceeded:
                raise
            except Exception:
                pass
        else:
            raise VideoParseError(f"unsupported video file format: {ext}")
    else:
        raise VideoParseError(f"video source file or URL not found: {source_path_or_url}")

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
        used_ids[seg_id] = used_ids.get(seg_id, 0) + 1
        sid = f"{seg_id}.{used_ids[seg_id]:02d}"

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
    if yt_chapters:
        for idx, ch in enumerate(yt_chapters, 1):
            ch_start = float(ch.get("start_time", 0))
            ch_title = st.clean_meta(ch.get("title") or f"Chapter {idx}", 120)
            m_span = next((s for s in spans_data if s.get("start", 0) >= ch_start), spans_data[-1] if spans_data else None)
            outline_data.append({
                "id": f"o{idx:03d}",
                "title": ch_title,
                "depth": 1,
                "start_span": m_span["id"] if m_span else None
            })
    else:
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
    assets_index = []
    for f in sorted(os.listdir(assets_dir)):
        if f.endswith((".jpg", ".png")):
            assets_index.append({
                "id": os.path.splitext(f)[0],
                "kind": "keyframe",
                "file": f,
                "path": f"assets/{f}",
                "locator": title
            })
    st.write_json(os.path.join(assets_dir, "index.json"), assets_index)

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
