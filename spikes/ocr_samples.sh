#!/usr/bin/env bash
# Spike 0a: build the mixed sample set into spikes/out/ocr/samples (gitignored).
# usage: spikes/ocr_samples.sh "<book.pdf>"
# Book-derived samples: prose page, math page, chart, diagram, table page, and a
# simulated scan of the prose page (ground truth = the page's text layer).
# Synthetic samples: a beamer slide and a chalkboard keyframe (ground truth known).
set -euo pipefail
book="$1"
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/out/ocr/samples"; mkdir -p "$out"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

page() { pdftoppm -r 150 -png -singlefile -f "$1" -l "$1" "$book" "$out/$2"; pdftotext -f "$1" -l "$1" "$book" "$out/$2.truth.txt"; }
page 102 prose
page 111 math
page 81 table
# figures: the embedded raster (largest image on the page)
fig() {
  pdfimages -png -f "$1" -l "$1" "$book" "$tmp/f$1"
  cp "$(ls -S "$tmp"/f"$1"-*.png | head -1)" "$out/$2.png"
  pdftotext -f "$1" -l "$1" "$book" - | grep -A2 -m1 "^Figure" > "$out/$2.caption.txt" || true
}
fig 122 chart
fig 103 diagram

# simulated scan: prose page, slight rotation, blur, noise, grayscale JPEG
ffmpeg -loglevel error -y -i "$out/prose.png" -vf "rotate=0.8*PI/180:fillcolor=white,gblur=sigma=0.6,noise=alls=12:allf=t,format=gray" -q:v 18 "$out/scan.jpg"
cp "$out/prose.truth.txt" "$out/scan.truth.txt"

# synthetic slide (beamer) and chalkboard keyframe (LaTeX on a dark green
# board; this ffmpeg has no drawtext), then rotation, blur, noise and JPEG
for t in slide board; do cp "$here/fixtures-src/$t.tex" "$tmp/"; (cd "$tmp" && pdflatex -interaction=nonstopmode "$t.tex" >/dev/null); done
pdftoppm -r 110 -png -singlefile "$tmp/slide.pdf" "$out/slide"
printf '%s\n' "Gradient Descent in Three Steps" \
  "Compute the gradient of the loss with respect to the weights." \
  'Step against the gradient: w <- w - eta grad L(w).' \
  "Repeat until the validation loss stops improving." > "$out/slide.truth.txt"
pdftoppm -r 150 -png -singlefile "$tmp/board.pdf" "$tmp/board"
ffmpeg -loglevel error -y -i "$tmp/board.png" -vf "rotate=-1.5*PI/180:fillcolor=0x2f4f3a,gblur=sigma=1.2,noise=alls=18:allf=t" -q:v 12 "$out/board.jpg"
printf '%s\n' "f(x) = x^2 + 3x - 4" "f'(x) = 2x + 3" "minimum at x = -1.5" > "$out/board.truth.txt"
ls -la "$out"
