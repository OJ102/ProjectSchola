"""/ingest: turn a source into the normalized store.

usage: source-ingest.sh <path> [--slug NAME] [--vision auto|all|none]
                               [--ocr auto|applevision|tesseract|vision|none]
                               [--force] [--rebuild] [--no-vault] [--json]

  --slug     store name ([a-z0-9-], at most 60); default: from the file name
  --vision   which pages/assets are marked for (lazy) Claude vision; no vision
             call is ever made at ingest
  --ocr      engine for pages without a text layer (default: Apple Vision on
             macOS, else tesseract; "vision" defers them to lazy Claude vision)
  --force    keep the result even if label validation fails (>5% mismatch)
  --rebuild  ingest again even if the store is up to date
  --no-vault do not touch the Obsidian vault
  --json     print the report as JSON instead of the summary

Exit codes: 0 ok (or up to date), 1 error, 2 usage, 3 label validation
failed, 4 another ingest of the slug is running, 5 the source changed while
it was being ingested, 6 the time limit (SCHOLA_INGEST_TIMEOUT_S, default 600 s)
ran out or OCR did not finish on some pages (the store is marked incomplete);
progress is cached and the next run resumes. 7 the built store
failed the adapter contract (contract.py) and was not committed.

Caps (decision 32): SCHOLA_INGEST_TIMEOUT_S (default 600), SCHOLA_MAX_RENDER_MP
(default 25) and SCHOLA_OCR_PAGE_TIMEOUT_S (default 120).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf as pdfadapter  # noqa: E402
import web as webadapter  # noqa: E402
import pandoc_adapter as pandocadapter  # noqa: E402
import video as videoadapter  # noqa: E402
import fetch as fetchadapter  # noqa: E402
import contract  # noqa: E402
import store as st  # noqa: E402
from store import (SLUG_RE, Locked, Store, StoreError, check_slug, clean_meta, meta_text, sha256_file,  # noqa: E402
                   slugify, sources_root, update_vault, vault_root, write_json)

INGEST_VERSION = 1
MAX_SOURCE = 2 * 1024 * 1024 * 1024
REQUIRED = {"pdftotext": "brew install poppler", "pdfinfo": "brew install poppler",
            "pdfimages": "brew install poppler", "pdftoppm": "brew install poppler",
            "pdftohtml": "brew install poppler"}


class Usage(Exception):
    pass


def ingest_timeout():
    """Wall clock for one ingest (decision 32): SCHOLA_INGEST_TIMEOUT_S
    seconds, default 600. A run that hits it exits 6 and resumes next time."""
    v = os.environ.get("SCHOLA_INGEST_TIMEOUT_S", "600")
    try:
        t = float(v)
    except ValueError:
        t = 600.0
    return min(max(t, 1.0), 86400.0)


def parse_args(argv):
    opts = {"slug": None, "vision": "auto", "ocr": "auto", "force": False, "rebuild": False, "vault": True,
            "json": False, "path": None}
    it = iter(argv)
    for a in it:
        if a in ("-h", "--help"):
            raise Usage(__doc__)
        if a in ("--slug", "--vision", "--ocr"):
            v = next(it, None)
            if v is None:
                raise Usage(f"{a} needs a value")
            opts[a[2:]] = v
        elif a == "--force":
            opts["force"] = True
        elif a == "--rebuild":
            opts["rebuild"] = True
        elif a == "--no-vault":
            opts["vault"] = False
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--"):
            raise Usage(f"unknown option {a}")
        elif opts["path"] is None:
            opts["path"] = a
        else:
            raise Usage("only one source per /ingest")
    if not opts["path"]:
        raise Usage("usage: source-ingest.sh <path> [--slug NAME] [--vision auto|all|none] "
                    "[--ocr auto|applevision|tesseract|vision|none] [--force] [--rebuild] [--no-vault] [--json]")
    if opts["slug"] is not None:
        check_slug(opts["slug"])
    if opts["vision"] not in ("auto", "all", "none"):
        raise Usage("--vision must be auto, all or none")
    if opts["ocr"] not in ("auto", "applevision", "tesseract", "vision", "none"):
        raise Usage("--ocr must be auto, applevision, tesseract, vision or none")
    return opts


def detect(path):
    if "://" in path:
        u = urllib.parse.urlsplit(path)
        if u.path.lower().endswith(".pdf"):
            raise StoreError("remote PDF URLs are not supported (download the PDF first)")
        if any(h in u.netloc.lower() for h in ("youtube.com", "youtu.be", "vimeo.com")):
            raise StoreError("video URLs are not supported yet")
        return "web", 0
    if not os.path.exists(path):
        raise StoreError(f"no such file: {path}")
    if not os.path.isfile(path):
        raise StoreError(f"not a regular file: {path}")
    size = os.path.getsize(path)
    if size > MAX_SOURCE:
        raise StoreError("source larger than 2 GB")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        return "web", size
    if ext == ".epub":
        return "epub", size
    if ext in (".md", ".markdown"):
        return "markdown", size
    if ext == ".ipynb":
        return "notebook", size
    if ext in (".docx", ".doc"):
        return "docx", size
    if ext in (".vtt", ".srt"):
        return "video", size
    if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4a"):
        raise StoreError("video media files are not supported yet (use .vtt or .srt caption files)")
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" in head:
        return "pdf", size
    raise StoreError(f"unsupported source type: {ext or 'unknown'}")


def code_hash():
    """Hash of the adapter code: a store built by older code is rebuilt."""
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):
        if name.endswith((".py", ".swift")):
            with open(os.path.join(here, name), "rb") as f:
                h.update(name.encode() + b"\0" + f.read())
    return h.hexdigest()[:16]


def pdf_meta(path):
    meta = {}
    try:
        r = subprocess.run(["pdfinfo", "-enc", "UTF-8", path], capture_output=True, timeout=60)
        for ln in r.stdout.decode("utf-8", "replace").splitlines():
            k, _, v = ln.partition(":")
            if k in ("Title", "Author", "CreationDate", "Encrypted"):
                meta[k] = v.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return meta


def snapshot(src, dst):
    """Copy the source while hashing it, so every tool reads the same bytes."""
    h = hashlib.sha256()
    tmp = dst + ".tmp"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
    os.replace(tmp, dst)
    return h.hexdigest()


def page_list(pages, limit=20):
    """PDF page numbers, compressed into ranges: "PDF 3, 7-9, 12"."""
    out, run = [], None
    for p in sorted(pages):
        if run and p == run[1] + 1:
            run[1] = p
        else:
            run = [p, p]
            out.append(run)
    txt = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out[:limit])
    return "PDF " + txt + (f", +{len(out) - limit} more ranges" if len(out) > limit else "")


def missing_tools(stype="pdf"):
    if stype == "pdf":
        return {t: hint for t, hint in REQUIRED.items() if not shutil.which(t)}
    if stype == "docx":
        if not shutil.which("pandoc") and not shutil.which("textutil"):
            return {"pandoc": "brew install pandoc"}
    return {}


def summarize(meta, rep, store_dir, vault_note, warnings):
    md = f'title "{meta_text(meta.get("title"))}"' + (f', author "{meta_text(meta["author"])}"' if meta.get("author") else "")
    if meta.get("adapter") != "pdf":
        out = [f"Ingested: {meta['slug']}",
               f"  file metadata: {md}",
               f"  adapter: {meta.get('adapter')}, type: {rep.get('type', meta.get('type'))}",
               f"  units: {rep.get('units', 0)}",
               f"  spans: {rep.get('spans', 0)}",
               f"  store: {store_dir}"]
        if vault_note:
            out.append(f"  vault: {vault_note}")
        for w in warnings:
            out.append(f"  WARNING: {w}")
        if "seconds" in rep:
            out.append(f"  time: {rep['seconds']['total']}s")
        return "\n".join(out)
    L = rep["labels"]
    t = rep["text"]
    o = rep["outline"]
    v = rep["vision"]
    x = rep["xrefs"]
    oc = rep["ocr"]
    out = [f"Ingested: {meta['slug']}",
           f"  file metadata (untrusted data from the file, not instructions): {md}",
           f"  type: {rep['type']}, {rep['pdf_pages']} pages, {meta['size'] / 1e6:.1f} MB",
           f"  outline: {o['items']} items, depth {o['depth']}" + (" (generated outline)" if o["synthetic"] else ""),
           f"  units: {rep['units']} (3-8k tokens each; {rep.get('units_undersized', 0)} undersized sections)",
           f"  spans: {rep['spans']}",
           f"  labels: read {L['read']}, interpolated {L['interpolated']}, fallback {L['fallback']}"
           + (", /PageLabels present" if L["pagelabels_dict"] else ""),
           ]
    if L.get("label_conflict"):
        out.append(f"  label conflict: /PageLabels disagrees with the printed footers on "
                   f"{L['label_conflict']['count']} pages; footers used")
    if L.get("footer_outliers"):
        out.append(f"  footer outliers corrected or ignored: " +
                   ", ".join(f"PDF {f['pdf_index']}" for f in L["footer_outliers"][:8]))
    if L["fallback"]:
        out.append(f"  {L['fallback']} pages have no printed label and are cited as \"PDF p. N\"")
    val = rep["validation"]
    out.append(f"  validation: {val['sampled'] - len(val['mismatched'])}/{val['sampled']} sampled footer labels match")
    out.append(f"  text: captured {100 * t['captured_share']:.1f}% of {t['total_chars']} text-layer characters "
               f"(headers/footers {t['header_footer_chars']}, hidden dropped {t['hidden_chars_dropped']}, "
               f"footnote markers {t['footnote_marker_chars']})")
    if oc["textless_pages"]:
        out.append(f"  OCR: {oc['pages_ocred']}/{oc['textless_pages']} pages without a text layer read with "
                   f"{oc['engine'] or 'nothing'}" + (f" ({oc['note']})" if oc["note"] else "")
                   + (f"; {len(oc['failed'])} failed" if oc["failed"] else "")
                   + (f"; {len(oc['skipped_oversized'])} oversized pages skipped" if oc.get("skipped_oversized") else ""))
    a = rep["assets"]
    out.append(f"  assets: {a['rasters']} figures (duplicates {a.get('duplicates', 0)}, icons dropped "
               f"{a.get('icons_dropped', 0)}), {a['rendered_pages']} rendered pages")
    out.append(f"  vision: no calls made; {v['assets_needing_vision']} assets for lazy vision, "
               f"about {v['estimated_tokens'] // 1000}k tokens if all are described")
    out.append(f"  cross-refs: {x['resolved']}/{x['total']} resolved ({x['titled_resolved']}/{x['titled']} "
               f"\"title\" on page N)")
    out.append(f"  store: {store_dir}")
    if vault_note:
        out.append(f"  vault: {vault_note}")
    for w in warnings:
        out.append(f"  WARNING: {w}")
    out.append(f"  time: {rep['seconds']['total']}s")
    return "\n".join(out)


def store_for_origin(root, src):
    """The slug whose store records this absolute source path (store
    identity, lead's ruling), or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        if not SLUG_RE.match(name) or os.path.islink(os.path.join(root, name)):
            continue
        for rel in ("source.json", os.path.join(".migrate", "source.json")):
            try:
                with open(os.path.join(root, name, rel)) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("origin") == src:
                return name
    return None


def free_slug(root, base):
    """base, or base-2, base-3, ... : the first slug no other source uses."""
    for k in range(1, 1000):
        cand = base if k == 1 else f"{base[:60 - len(str(k)) - 1].rstrip('-')}-{k}"
        d = os.path.join(root, cand)
        if not os.path.lexists(d) or not os.path.exists(os.path.join(d, "source.json")) and \
                not os.path.exists(os.path.join(d, ".migrate")):
            return cand
    raise StoreError("no free slug; pass --slug")


def has_ocr_cache(st):
    base = st.path(".cache")
    for dp, _, files in os.walk(base):
        if any(f.endswith(".json") for f in files):
            return True
    return False


def run(argv):
    opts = parse_args(argv)
    opts["deadline"] = time.monotonic() + ingest_timeout()
    if "://" in opts["path"]:
        src = opts["path"]
        stype, size = detect(src)
    else:
        src = os.path.realpath(os.path.expanduser(opts["path"]))
        stype, size = detect(src)
    miss = missing_tools(stype)
    if miss:
        hints = sorted(set(miss.values()))
        raise StoreError(f"missing tools: {', '.join(sorted(miss))}; install with: {'; '.join(hints)}")
    root = sources_root()
    if "://" in src:
        sha_pre = hashlib.sha256(src.encode()).hexdigest()
    else:
        sha_pre = sha256_file(src, MAX_SOURCE)
    if opts["slug"]:
        slug = opts["slug"]
    else:
        if "://" in src:
            u = urllib.parse.urlsplit(src)
            bname = os.path.basename(u.path.rstrip("/")) or u.netloc
            slug = store_for_origin(root, src) or free_slug(root, slugify(bname, f"web-{sha_pre[:8]}"))
        else:
            slug = store_for_origin(root, src) or \
                free_slug(root, slugify(os.path.splitext(os.path.basename(src))[0], f"source-{sha_pre[:8]}"))
    fresh = not os.path.lexists(os.path.join(root, slug))
    st = Store(slug, root)
    st.lock()
    ok = False
    try:
        rc = ingest_locked(st, src, stype, size, opts)
        ok = True
        return rc
    finally:
        if not ok and fresh and not os.path.exists(st.path("source.json")) \
                and not os.path.exists(st.path(".migrate")) and not has_ocr_cache(st):
            shutil.rmtree(st.dir, ignore_errors=True)  # a failed first ingest leaves nothing behind
        st.unlock()


def ingest_locked(st, src, stype, size, opts):
    warnings = []
    existing = st.source_json()
    cache = None
    t0 = time.time()
    if "://" in src:
        sha = hashlib.sha256(src.encode()).hexdigest()
    else:
        sha = sha256_file(src, MAX_SOURCE)
    cache = st.cache_dir(sha)
    settings = {"ocr": opts["ocr"], "vision": opts["vision"], "code": code_hash()}
    content_match = existing and ("://" not in src) and existing.get("sha256") == sha
    if existing and existing.get("status") == "complete" and content_match \
            and existing.get("ingest_version") == INGEST_VERSION and existing.get("settings") == settings \
            and not opts["rebuild"]:
        try:
            with open(st.path("report.json"), "r", encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError):
            rep = None
        # a run whose OCR partly failed is resumed, not reported as up to date
        if rep and not (rep.get("ocr") or {}).get("failed_pages"):
            vault_note = None
            if opts["vault"]:
                vp, vw = update_vault(st.slug, existing, {"units": rep.get("units", 0)}, st.root)
                vault_note = os.path.join(vp, "sources", st.slug, "progress.md")
                warnings += [f"the vault looks synced or version-controlled ({w})" for w in vw]
            if opts["json"]:
                print(json.dumps(dict(rep, up_to_date=True, store=st.dir), indent=1, ensure_ascii=False))
            else:
                print("Up to date (same file, same settings); nothing re-ingested. Use --rebuild to force.")
                print(summarize(existing, rep, st.dir, vault_note, warnings))
            return 0

    build = st.build_dir()
    snap = None
    if stype == "pdf":
        snap = os.path.join(cache, "source.pdf")
        snap_sha = snapshot(src, snap)
        if snap_sha != sha:
            raise SourceChanged("the source changed while it was being read; try again when it is stable")
        rep = pdfadapter.build(snap, build, {"ocr": opts["ocr"], "vision": opts["vision"], "deadline": opts["deadline"]},
                               cache, log=lambda m: print(m, file=sys.stderr))
        if sha256_file(src, MAX_SOURCE) != sha:
            shutil.rmtree(build, ignore_errors=True)
            raise SourceChanged("the source changed during ingest; nothing was written. Run /ingest again")
        info = pdf_meta(snap)
        xmp = rep.pop("xmp", {}) or {}
        title = clean_meta(info.get("Title")) or xmp.get("title") or \
            clean_meta(os.path.splitext(os.path.basename(src))[0])
        author = clean_meta(info.get("Author")) or xmp.get("author", "")
        published = clean_meta(info.get("CreationDate"), 60)
        adapter_name = "pdf"
    elif stype == "web":
        rep = webadapter.build(src, build, opts, cache, log=lambda m: print(m, file=sys.stderr))
        with open(os.path.join(build, "source.json"), "r", encoding="utf-8") as f:
            s_meta = json.load(f)
        title = s_meta.get("title") or st.slug
        author = ""
        published = ""
        sha = s_meta.get("sha256", sha)
        adapter_name = "web"
        if existing and existing.get("status") == "complete" and existing.get("sha256") == sha \
                and existing.get("ingest_version") == INGEST_VERSION and existing.get("settings") == settings \
                and not opts["rebuild"]:
            shutil.rmtree(build, ignore_errors=True)
            try:
                with open(st.path("report.json"), "r", encoding="utf-8") as f:
                    rep = json.load(f)
            except (OSError, ValueError):
                rep = None
            vault_note = None
            if opts["vault"]:
                vp, vw = update_vault(st.slug, existing, {"units": rep.get("units", 0) if rep else 0}, st.root)
                vault_note = os.path.join(vp, "sources", st.slug, "progress.md")
                warnings += [f"the vault looks synced or version-controlled ({w})" for w in vw]
            if opts["json"]:
                print(json.dumps(dict(rep or {}, up_to_date=True, store=st.dir), indent=1, ensure_ascii=False))
            else:
                print("Up to date (same file, same settings); nothing re-ingested. Use --rebuild to force.")
                print(summarize(existing, rep or {}, st.dir, vault_note, warnings))
            return 0
    elif stype in ("epub", "markdown", "notebook", "docx"):
        rep = pandocadapter.build(src, build, opts, cache, log=lambda m: print(m, file=sys.stderr))
        with open(os.path.join(build, "source.json"), "r", encoding="utf-8") as f:
            s_meta = json.load(f)
        title = s_meta.get("title") or st.slug
        author = ""
        published = ""
        sha = s_meta.get("sha256", sha)
        adapter_name = "pandoc"
    elif stype == "video":
        rep = videoadapter.build(src, build, opts, cache, log=lambda m: print(m, file=sys.stderr))
        with open(os.path.join(build, "source.json"), "r", encoding="utf-8") as f:
            s_meta = json.load(f)
        title = s_meta.get("title") or st.slug
        author = ""
        published = ""
        sha = s_meta.get("sha256", sha)
        adapter_name = "video"
    else:
        raise StoreError(f"unhandled source type {stype}")

    # the adapter contract gates the commit (decisions 31 and the lead's
    # ruling): a failing store is never committed
    chk = contract.run(build, building=True)
    if chk.failed:
        shutil.rmtree(build, ignore_errors=True)
        fails = [l for l in chk.lines if l.startswith("FAIL")]
        raise ContractFailed("the store failed the adapter contract; nothing was written (this is a bug, "
                             "please report it):\n  " + "\n  ".join(fails[:8]))

    val = rep.get("validation", {"sampled": 0, "mismatched": [], "mismatch_rate": 0.0})
    if val.get("mismatch_rate", 0.0) > 0.05 and not opts["force"]:
        shutil.rmtree(build, ignore_errors=True)
        lines = "; ".join(f"PDF {m['pdf_index']}: footer {m['footer']!r} vs label {m['label']!r}"
                          for m in val["mismatched"][:5])
        raise ValidationFailed(f"label validation failed: {len(val['mismatched'])}/{val['sampled']} sampled pages "
                               f"disagree with their printed footer ({lines}). Nothing was written. "
                               "Re-run with --force to keep these labels.")

    meta_file = os.path.join(build, "source.json")
    if os.path.exists(meta_file):
        meta = json.load(open(meta_file))
    else:
        meta = {}
    meta.update({
        "slug": st.slug, "type": rep.get("type", stype), "adapter": adapter_name, "origin": src,
        "sha256": sha, "size": size,
        "mtime": int(os.path.getmtime(src)) if os.path.exists(src) else int(time.time()),
        "title": title, "author": author,
        "published": published, "ingest_version": INGEST_VERSION,
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "settings": settings,
        "license_note": "Personal local copy for study. Never publish, push or upload this store."
    })
    if "://" in src:
        meta["url_sha"] = hashlib.sha256(src.encode()).hexdigest()
    rep["units_undersized"] = sum(1 for u in json.load(open(os.path.join(build, "units.json")))
                                  if u.get("undersized"))
    if stype == "pdf":
        if rep["labels"]["fallback"] > 0.4 * rep["pdf_pages"]:
            warnings.append("no consistent printed page numbers; most pages are cited as \"PDF p. N\"")
        if rep["labels"].get("label_conflict"):
            warnings.append("the PDF's /PageLabels dictionary disagrees with the printed footers; footers used")
        if rep["ocr"]["note"] == "missing" and rep["ocr"]["textless_pages"]:
            warnings.append("pages without a text layer were not OCRed: install tesseract (brew install tesseract) "
                            "or Xcode command line tools for Apple Vision (xcode-select --install)")
        if rep["ocr"]["failed_pages"]:
            warnings.append(f"INCOMPLETE: OCR did not finish on {len(rep['ocr']['failed_pages'])} pages "
                            f"({page_list(rep['ocr']['failed_pages'])}); run /ingest again to retry them")
        if rep["ocr"].get("skipped_oversized"):
            warnings.append(f"{len(rep['ocr']['skipped_oversized'])} pages hold an image too large to decode and were "
                            f"not OCRed ({page_list(rep['ocr']['skipped_oversized'])})")
        if rep["ocr"].get("blank_pages"):
            warnings.append(f"{len(rep['ocr']['blank_pages'])} pages without text look blank and were skipped "
                            f"({page_list(rep['ocr']['blank_pages'])})")
        if rep["assets"].get("skipped_pages"):
            warnings.append(f"figures were not extracted from {len(rep['assets']['skipped_pages'])} pages (image size "
                            f"budget): {page_list(rep['assets']['skipped_pages'])}")
        if rep["hidden"].get("outside_cropbox_chars_dropped"):
            warnings.append(f"{rep['hidden']['outside_cropbox_chars_dropped']} characters outside the visible page "
                            "area (CropBox) were dropped")
        if rep["hidden"]["suspect_span_count"]:
            warnings.append(f"{rep['hidden']['suspect_span_count']} spans hold text that may be hidden from a reader "
                            "(white, clipped or covered); they are flagged suspect_hidden")
        if rep["hidden"]["invisible_chars_dropped"]:
            warnings.append(f"{rep['hidden']['invisible_chars_dropped']} characters of invisible text were dropped")
        if rep["encrypted"]:
            warnings.append("the PDF is encrypted; text was read through poppler")
        if val["mismatch_rate"] > 0.05:
            warnings.append("label validation failed and was overridden with --force")

    vroot = vault_root()
    if st.root == vroot or st.root.startswith(vroot + os.sep):
        warnings.append("the source store is inside the vault; set SCHOLA_SOURCES_DIR outside it")
    rep["warnings"] = warnings
    write_json(os.path.join(build, "report.json"), dict(rep, source=meta))

    # span-id migration survives a crash mid-commit: the old spans and hash
    # are parked in .migrate/ before anything is replaced, and removed only
    # after the migration is written
    pending = st.pending_migration()
    if pending is None and existing and existing.get("sha256") and existing.get("sha256") != sha \
            and os.path.isfile(st.path("spans.jsonl")):
        st.park_migration(existing)
        pending = st.pending_migration()
    with open(os.path.join(build, "spans.jsonl"), "r", encoding="utf-8") as f:
        new_spans = [json.loads(l) for l in f]
    incomplete = bool((rep.get("ocr") or {}).get("failed_pages"))
    st.commit(build, meta, status="incomplete" if incomplete else "complete")
    if pending is not None and pending[1] != sha:
        mig = st.migrate(pending[0], new_spans, pending[1], sha)
        rep["migration"] = {k: mig[k] for k in ("mapped", "briefs_rewritten", "briefs_stale")}
        rep["migration"]["unmatched"] = len(mig["unmatched"])
        write_json(st.path("report.json"), dict(rep, source=meta))
    st.clear_migration()
    if snap and os.path.exists(snap):
        try:
            os.unlink(snap)
        except OSError:
            pass
    vault_note = None
    if opts["vault"]:
        vp, vw = update_vault(st.slug, meta, {"units": rep["units"]}, st.root)
        vault_note = os.path.join(vp, "sources", st.slug, "progress.md")
        warnings += [f"the vault looks synced or version-controlled ({w}); the store stays outside it, "
                     "but progress notes there may be uploaded" for w in vw]
    rep.setdefault("seconds", {})["wall"] = round(time.time() - t0, 2)
    rep["seconds"].setdefault("total", rep["seconds"]["wall"])
    if opts["json"]:
        print(json.dumps(dict(rep, source=meta, store=st.dir), indent=1, ensure_ascii=False))
    else:
        print(summarize(meta, rep, st.dir, vault_note, warnings))
        if "migration" in rep:
            m = rep["migration"]
            print(f"  re-ingest: source changed; {m['mapped']} span ids migrated, {m['unmatched']} unmatched, "
                  f"{len(m['briefs_stale'])} briefs flagged for re-brief")
    if incomplete:
        # decision 36: unfinished pages are never silent; the store is marked
        # incomplete and the next run resumes them (OCR results are cached)
        print(f"ingest: INCOMPLETE: OCR did not finish on {len(rep['ocr']['failed_pages'])} pages; "
              "run /ingest again to retry them", file=sys.stderr)
        return 6
    return 0


class SourceChanged(StoreError):
    pass


class ValidationFailed(StoreError):
    pass


class ContractFailed(StoreError):
    pass


def main():
    os.umask(0o077)  # the store is personal: owner-only files and directories
    try:
        sys.exit(run(sys.argv[1:]))
    except Usage as e:
        print(str(e).strip(), file=sys.stderr)
        sys.exit(2)
    except Locked as e:
        print(f"ingest: {e}", file=sys.stderr)
        sys.exit(4)
    except SourceChanged as e:
        print(f"ingest: {e}", file=sys.stderr)
        sys.exit(5)
    except ValidationFailed as e:
        print(f"ingest: {e}", file=sys.stderr)
        sys.exit(3)
    except ContractFailed as e:
        print(f"ingest: {e}", file=sys.stderr)
        sys.exit(7)
    except (pdfadapter.DeadlineExceeded, st.DeadlineExceeded) as e:
        print(f"ingest: {e} (time limit {ingest_timeout():.0f}s; run /ingest again to resume)", file=sys.stderr)
        sys.exit(6)
    except (StoreError, pdfadapter.AdapterError, webadapter.WebParseError,
            pandocadapter.SourceError, videoadapter.VideoParseError,
            fetchadapter.FetchError, json.JSONDecodeError, OSError) as e:
        print(f"ingest: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("ingest: interrupted; run it again to resume", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
