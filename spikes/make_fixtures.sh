#!/usr/bin/env bash
# Generate tiny synthetic fixtures for the source-mode spikes into tests/fixtures/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../tests/fixtures"; mkdir -p "$out"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for v in true false; do
  sed "s/PAGELABELS/$v/" "$here/fixtures-src/book.tex" > "$tmp/book.tex"
  (cd "$tmp" && pdflatex -interaction=nonstopmode book.tex >/dev/null && pdflatex -interaction=nonstopmode book.tex >/dev/null)
  name=book-labels; [ "$v" = false ] && name=book-nolabels
  cp "$tmp/book.pdf" "$out/$name.pdf"
done
# EPUB with nav + page-list (pandoc), from synthetic markdown
pandoc "$here/fixtures-src/epub.md" -o "$out/sample.epub" --metadata title="Fixture EPUB" 2>/dev/null
cp "$here/fixtures-src/lecture.vtt" "$out/lecture.vtt"
ls -la "$out"
