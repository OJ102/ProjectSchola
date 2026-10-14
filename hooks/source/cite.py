"""Span citation locator lookup.

usage: cite.py <slug_or_store_dir> <span_id> [--quote] [--format FORMAT] [--parens]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as st  # noqa: E402
import verify as sv  # noqa: E402
from srcnorm import display_safe  # noqa: E402


def resolve_store(slug_or_dir):
    """Resolve a slug or directory to a store path containing spans.jsonl."""
    if not slug_or_dir:
        return None
    cand = os.path.expanduser(slug_or_dir)
    if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "spans.jsonl")):
        return os.path.realpath(cand)
    # Check if slug
    try:
        slug = st.check_slug(slug_or_dir)
        root = st.sources_root()
        cand = os.path.join(root, slug)
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "spans.jsonl")):
            return os.path.realpath(cand)
    except Exception:
        pass
    return None


def find_span(store, span_id):
    """Find a span dictionary by id in spans.jsonl."""
    spans_path = os.path.join(store, "spans.jsonl")
    if not os.path.isfile(spans_path):
        return None
    with open(spans_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                s = json.loads(line)
                if s.get("id") == span_id:
                    return s
            except json.JSONDecodeError:
                continue
    return None


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.stderr.write(f"usage: {sys.argv[0]} <slug_or_store_dir> <span_id> [--quote] [--format FORMAT]\n")
        sys.exit(2)

    slug_or_dir, span_id = args[0], args[1]
    rest = args[2:]

    include_quote = "--quote" in rest
    quote_only = "--quote-only" in rest or "--text" in rest
    parens = "--parens" in rest or "-p" in rest or ("--format" in rest and rest[rest.index("--format") + 1] in ("markdown", "parens"))
    as_json = "--format" in rest and rest[rest.index("--format") + 1] == "json"

    store = resolve_store(slug_or_dir)
    if not store:
        sys.stderr.write(f"error: source store not found for {slug_or_dir!r}\n")
        sys.exit(1)

    span = find_span(store, span_id)
    if not span:
        sys.stderr.write(f"error: span {span_id!r} not found in store {store}\n")
        sys.exit(1)

    loc = sv.locator_text(store, span)
    text = display_safe(span.get("text", ""))

    if as_json:
        res = {"id": span_id, "locator": loc, "segment": span.get("segment")}
        if include_quote or quote_only:
            res["text"] = text
        print(json.dumps(res, ensure_ascii=False))
        return

    if quote_only:
        print(text)
        return

    if include_quote:
        out = f"{loc}: “{text}”"
        if parens:
            out = f"({out})"
        print(out)
        return

    if parens:
        print(f"({loc})")
        return

    print(loc)


if __name__ == "__main__":
    main()
