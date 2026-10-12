#!/bin/bash
# /ingest entry point: turn a source (PDF so far) into the normalized store at
# ${SCHOLA_SOURCES_DIR:-~/.local/share/schola/sources}/<slug>/. All logic lives in
# hooks/source/ingest.py (Python stdlib only); see its docstring for options.
# python3 -I: the source directory and the caller's environment never inject modules.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/ingest.py" "$@"
