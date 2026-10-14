#!/bin/bash
# Citation gate for source briefs. Deterministic: no model calls.
# Usage: hooks/source-verify.sh <storedir> <brief.md> [options]
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/source/verify.py" "$@"
