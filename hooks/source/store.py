"""The normalized source store: location, slugs, confinement, locking, commit,
span-id migration and the vault side (progress stub + library index).

Layout (decision 8): the raw store lives OUTSIDE the vault, at
    ${SCHOLA_SOURCES_DIR:-~/.local/share/schola/sources}/<slug>/
and holds the files every adapter writes:
    source.json      metadata + status ("complete" once committed)
    labels.json      page labels (PDF), label -> [pdf indices] multimap
    outline.json     outline tree with segment and span ranges
    spans.jsonl      one span per line: id, segment, locator, text, norm, sha1
    segments/        one Markdown file per segment, one "[id] text" line per span
    segments/index.json
    units.json       teachable units (3-8k tokens)
    xrefs.json       resolved in-text cross-references
    assets/          figures and rendered pages, plus assets/index.json
    report.json      the ingest report
Lazy caches that ingest never rebuilds: briefs/, vision/. Resume state for a
running ingest: .cache/<sha>/ (per-page OCR results).

The vault gets only sources/<slug>/progress.md (a stub, never overwritten) and
library.md (a managed block, one row per source).

Safety (decision 9): slugs are [a-z0-9-] only and at most 60 characters; every
path is resolved with realpath and must stay inside its root.
"""
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import time

from srcnorm import display_safe, neutralize

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")
# Files every adapter produces; a commit replaces exactly these.
PRODUCED = ["labels.json", "outline.json", "spans.jsonl", "segments", "units.json", "xrefs.json",
            "assets", "report.json"]
LIB_START = "<!-- schola:library:start -->"
LIB_END = "<!-- schola:library:end -->"


class StoreError(Exception):
    pass


class Locked(StoreError):
    pass


def sources_root():
    raw = os.environ.get("SCHOLA_SOURCES_DIR") or os.path.join(os.path.expanduser("~"), ".local", "share",
                                                                 "schola", "sources")
    root = os.path.realpath(os.path.expanduser(raw))
    home = os.path.realpath(os.path.expanduser("~"))
    # A root of "/", $HOME or one of its parents would put the store's
    # "*" .gitignore and lock files over unrelated data.
    if root == os.path.dirname(root) or root == home or home.startswith(root + os.sep):
        raise StoreError(f"refusing store root {root}: set SCHOLA_SOURCES_DIR to a dedicated directory")
    return root


def vault_root():
    raw = os.environ.get("OBSIDIAN_VAULT_PATH") or os.path.join(os.path.expanduser("~"), "Obsidian", "learn")
    return os.path.realpath(os.path.expanduser(raw))


def clean_meta(s, limit=200):
    """Metadata strings (titles, authors, outline entries) are source data:
    one line, no control/bidi characters, no citation syntax, bounded."""
    s = display_safe(str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return neutralize(s)[:limit]


def slugify(text, fallback):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    s = s[:60].strip("-")
    return s if SLUG_RE.match(s) else fallback


def check_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise StoreError(f"invalid slug {slug!r}: use 1-60 characters from a-z, 0-9 and '-', "
                         "starting and ending with a letter or digit")
    return slug


def inside(root, path):
    """Resolve path and require it to stay inside root (both realpath'd)."""
    rr = os.path.realpath(root)
    rp = os.path.realpath(path)
    if rp != rr and not rp.startswith(rr + os.sep):
        raise StoreError(f"path escapes its root: {path}")
    return rp


def atomic_write(path, data, mode=0o600):
    d = os.path.dirname(path)
    tmp = os.path.join(d, f".tmp-{os.getpid()}-{os.path.basename(path)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)
    os.replace(tmp, path)


def write_json(path, obj):
    atomic_write(path, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += len(chunk)
            if limit and n > limit:
                raise StoreError(f"source larger than {limit // (1 << 20)} MB")
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, slug, root=None):
        self.slug = check_slug(slug)
        self.root = root or sources_root()
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self._gitignore(self.root)
        self.dir = os.path.join(self.root, slug)
        if os.path.islink(self.dir):
            raise StoreError(f"store directory is a symlink: {self.dir}")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        inside(self.root, self.dir)
        self._gitignore(self.dir)
        self._lock_fd = None

    @staticmethod
    def _gitignore(d):
        p = os.path.join(d, ".gitignore")
        if not os.path.exists(p) and not os.path.islink(p):
            atomic_write(p, "# Schola source store: personal copies of copyrighted sources. Never commit.\n*\n")

    def path(self, *parts):
        """A path inside this store; refuses anything that resolves outside."""
        p = os.path.join(self.dir, *parts)
        inside(self.dir, p)
        return p

    # ------------------------------------------------------------ locking
    def lock(self):
        """Exclusive per-slug lock (flock: released by the kernel if the
        ingest dies, so there are no stale locks to clean up)."""
        ld = os.path.join(self.root, ".locks")
        os.makedirs(ld, mode=0o700, exist_ok=True)
        lp = os.path.join(ld, self.slug + ".lock")
        fd = os.open(lp, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise Locked(f"another ingest of '{self.slug}' is running")
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {time.time():.0f}\n".encode())
        self._lock_fd = fd

    def unlock(self):
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    # ------------------------------------------------------------ state
    def source_json(self):
        p = self.path("source.json")
        try:
            with open(p) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def build_dir(self):
        """A fresh directory for the files a new ingest produces."""
        b = self.path(".build")
        if os.path.lexists(b):
            shutil.rmtree(b) if not os.path.islink(b) else os.unlink(b)
        os.makedirs(b, mode=0o700)
        return b

    def cache_dir(self, sha):
        """Resume cache for one source hash (per-page OCR and the like).
        Caches for other hashes are dropped."""
        base = self.path(".cache")
        os.makedirs(base, mode=0o700, exist_ok=True)
        for name in os.listdir(base):
            if name != sha[:16]:
                p = os.path.join(base, name)
                shutil.rmtree(p, ignore_errors=True) if not os.path.islink(p) else os.unlink(p)
        c = os.path.join(base, sha[:16])
        os.makedirs(c, mode=0o700, exist_ok=True)
        return inside(self.dir, c)

    def commit(self, build, source_meta):
        """Move the produced files from build into place. source.json is
        removed first and written last, so a store without a complete
        source.json is never mistaken for a finished one."""
        sj = self.path("source.json")
        if os.path.lexists(sj):
            os.unlink(sj)
        for name in PRODUCED:
            src = os.path.join(build, name)
            dst = self.path(name)
            if os.path.lexists(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    old = dst + ".old"
                    if os.path.lexists(old):
                        shutil.rmtree(old)
                    os.rename(dst, old)
                    shutil.rmtree(old)
                else:
                    os.unlink(dst)
            if os.path.lexists(src):
                os.rename(src, dst)
        shutil.rmtree(build, ignore_errors=True)
        write_json(sj, dict(source_meta, status="complete"))

    # ------------------------------------------------------------ migration
    def migrate(self, old_spans, new_spans, old_sha, new_sha):
        """Span ids are rebuilt when the source hash changes. Map old ids to
        new ones by normalized-text hash (nearest position wins when a text
        repeats), rewrite cites in cached briefs that map completely, and move
        briefs with unmatched ids to briefs/.stale/ for a re-brief."""
        by_sha = {}
        for k, s in enumerate(new_spans):
            by_sha.setdefault(s["sha1"], []).append((k, s["id"]))
        mapping, unmatched = {}, []
        n_old = max(1, len(old_spans))
        n_new = max(1, len(new_spans))
        used = set()
        for k, s in enumerate(old_spans):
            cands = [c for c in by_sha.get(s.get("sha1"), []) if c[1] not in used]
            if not cands:
                unmatched.append(s["id"])
                continue
            pos = k / n_old
            best = min(cands, key=lambda c: abs(c[0] / n_new - pos))
            used.add(best[1])
            mapping[s["id"]] = best[1]
        rewritten, stale = [], []
        bd = self.path("briefs")
        if os.path.isdir(bd) and not os.path.islink(bd):
            cite = re.compile(r"\[\[cite:([A-Za-z0-9:._-]+)")
            for name in sorted(os.listdir(bd)):
                p = os.path.join(bd, name)
                if not name.endswith(".md") or not os.path.isfile(p) or os.path.islink(p):
                    continue
                txt = open(p, encoding="utf-8", errors="replace").read()
                ids = set(cite.findall(txt))
                if ids and all(i in mapping for i in ids):
                    atomic_write(p, cite.sub(lambda m: "[[cite:" + mapping[m.group(1)], txt))
                    rewritten.append(name)
                elif ids:
                    sd = os.path.join(bd, ".stale")
                    os.makedirs(sd, mode=0o700, exist_ok=True)
                    for f in (name, name[:-3] + ".verify.json"):
                        if os.path.isfile(os.path.join(bd, f)):
                            os.replace(os.path.join(bd, f), os.path.join(sd, f))
                    stale.append(name)
        mig = {"old_sha256": old_sha, "new_sha256": new_sha, "migrated_at": int(time.time()),
               "mapped": len(mapping), "unmatched": unmatched, "map": mapping,
               "briefs_rewritten": rewritten, "briefs_stale": stale}
        write_json(self.path("migration.json"), mig)
        return mig


# ---------------------------------------------------------------- vault
SYNC_MARKERS = [("Mobile Documents", "iCloud Drive"), ("iCloud", "iCloud Drive"), ("Dropbox", "Dropbox"),
                ("OneDrive", "OneDrive"), ("Google Drive", "Google Drive"), ("GoogleDrive", "Google Drive"),
                ("CloudStorage", "a cloud storage provider")]


def sync_warnings(path):
    """Reasons a directory looks synced or version-controlled (decision 8)."""
    out = []
    for marker, what in SYNC_MARKERS:
        if marker.lower() in path.lower() and what not in out:
            out.append(what)
    d = path
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            out.append(f"a git repository ({d})")
            break
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    if os.path.isdir(os.path.join(path, ".obsidian", "plugins", "obsidian-git")):
        out.append("the obsidian-git plugin")
    return out


def md_cell(s):
    return clean_meta(s, 120).replace("|", "\\|")


def update_vault(slug, meta, summary):
    """Write the progress stub (only if absent) and the library row. Returns
    (vault_path, warnings). Nothing source-derived beyond title/author and
    counts reaches the vault."""
    vault = vault_root()
    warns = []
    os.makedirs(vault, exist_ok=True)
    sd = os.path.join(vault, "sources", slug)
    inside(vault, sd)  # before creating anything: a symlinked sources/ must not lead out
    os.makedirs(sd, exist_ok=True)
    inside(vault, sd)
    prog = os.path.join(sd, "progress.md")
    inside(vault, prog)
    title = clean_meta(meta.get("title") or slug)
    if not os.path.lexists(prog):
        atomic_write(prog, "\n".join([
            "---", f"source: {slug}", f"title: \"{title.replace(chr(34), '')}\"", "status: not started", "---",
            "", f"# {title}", "",
            f"Progress for this source. `/teach {slug}` fills in the unit map, quiz scores and review queue.",
            ""]), mode=0o644)
    lib = os.path.join(vault, "library.md")
    inside(vault, lib)
    rows = {}
    head = "# Library\n\nSources ingested with `/ingest`. Rows are managed; edit outside the markers.\n\n"
    tail = "\n"
    if os.path.lexists(lib):
        txt = open(lib, encoding="utf-8", errors="replace").read()
        if LIB_START in txt and LIB_END in txt:
            head, rest = txt.split(LIB_START, 1)
            block, tail = rest.split(LIB_END, 1)
            for ln in block.splitlines():
                m = re.match(r"^\| \[\[sources/([a-z0-9-]+)/progress\|", ln)
                if m:
                    rows[m.group(1)] = ln
        else:
            head = txt.rstrip("\n") + "\n\n"
    rows[slug] = (f"| [[sources/{slug}/progress|{md_cell(title)}]] | {md_cell(meta.get('author') or '')} | "
                  f"{md_cell(meta.get('type'))} | {summary.get('units', 0)} | 0% |")
    block = "\n| Source | Author | Type | Units | Progress |\n|---|---|---|---|---|\n" + \
            "\n".join(rows[k] for k in sorted(rows)) + "\n"
    atomic_write(lib, head + LIB_START + block + LIB_END + tail, mode=0o644)
    warns += sync_warnings(vault)
    return vault, warns
