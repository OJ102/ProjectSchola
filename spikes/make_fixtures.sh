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
# >= 20 pages, no /PageLabels, one lying footer (page 12 prints 99): keeps
# the fit/correction path covered now that short sources fall back
{
  printf '%s\n' '\documentclass{article}\usepackage[paperheight=4in,paperwidth=4in,margin=0.5in]{geometry}' \
    '\usepackage{fancyhdr}\usepackage[pdfpagelabels=false]{hyperref}\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0pt}' \
    '\begin{document}'
  for i in $(seq 1 24); do
    f='\thepage'; [ "$i" = 12 ] && f=99
    printf '%s\n' "\\fancyfoot[C]{$f}Body text of page $i.\\newpage"
  done
  printf '%s\n' '\end{document}'
} > "$tmp/book20.tex"
(cd "$tmp" && pdflatex -interaction=nonstopmode book20.tex >/dev/null)
cp "$tmp/book20.pdf" "$out/book20-lying.pdf"
# EPUB with nav + page-list (pandoc), from synthetic markdown
pandoc "$here/fixtures-src/epub.md" -o "$out/sample.epub" --metadata title="Fixture EPUB" 2>/dev/null
cp "$here/fixtures-src/lecture.vtt" "$out/lecture.vtt"
ls -la "$out"
