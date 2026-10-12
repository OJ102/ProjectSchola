#!/usr/bin/env bash
# Prototype citation gate. Deterministic; exit 0 = every cite verified.
# usage: spikes/source-verify.sh <storedir> <brief.md> [--allow unit.ids]... [--json out.json] [--render]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 -I "$here/source_verify.py" "$@"
