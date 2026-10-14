#!/bin/bash
# Lookup locator text (and optional quote) for a span in an ingested source store.
# Usage: hooks/source-cite.sh <slug_or_store_dir> <span_id> [--quote] [--format ...]
# python3 -I: the source directory and the caller's environment never inject modules.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/cite.py" "$@"
