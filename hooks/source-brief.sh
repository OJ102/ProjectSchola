#!/bin/bash
# /teach source mode helper: unit map, briefs and progress for an ingested
# source. All logic lives in hooks/source/brief.py; see its docstring.
# python3 -I: the source directory and the caller's environment never inject modules.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/brief.py" "$@"
