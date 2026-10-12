#!/usr/bin/env python3
"""Tester's independent printed-footer extractor (does not share code with spikes/).

Usage: footer_labels.py <book.pdf>  -> JSON {pdf_page: {"label": str, "form": str}} on stdout
Only pages whose text layer carries a footer are included. Forms:
  pipe-left   "N | Chapter ..."      (number, then "|", then running head)
  pipe-right  "Section ... | N"
  bare        a lone number/roman as the very last line (chapter openers)
"""
import json, re, subprocess, sys

NUM = re.compile(r'^(\d{1,4}|[ivxlcdm]{1,8})$')


def page_lines(pdf, p):
    out = subprocess.run(['pdftotext', '-f', str(p), '-l', str(p), pdf, '-'],
                         capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def main(pdf):
    info = subprocess.run(['pdfinfo', pdf], capture_output=True, text=True).stdout
    n = int(re.search(r'^Pages:\s+(\d+)', info, re.M).group(1))
    res = {}
    for p in range(1, n + 1):
        L = page_lines(pdf, p)
        if len(L) >= 3 and L[-2] == '|' and NUM.match(L[-3]):
            res[p] = {'label': L[-3], 'form': 'pipe-left'}
        elif len(L) >= 2 and L[-1].startswith('| ') and NUM.match(L[-2]):
            res[p] = {'label': L[-2], 'form': 'pipe-left'}
        elif len(L) >= 2 and L[-2].endswith('|') and NUM.match(L[-1]):
            res[p] = {'label': L[-1], 'form': 'pipe-right'}
        elif L and NUM.match(L[-1]):
            res[p] = {'label': L[-1], 'form': 'bare'}
        else:
            # table/index pages: the footer appears out of reading order
            pipe = lambda s: s == '|' or s.startswith('| ') or s.endswith(' |')
            for k, l in enumerate(L):
                if NUM.match(l) and any(pipe(x) for x in L[max(0, k - 1):k + 2]):
                    res[p] = {'label': l, 'form': 'pipe-any'}
                    break
    json.dump(res, sys.stdout, indent=0)


if __name__ == '__main__':
    main(sys.argv[1])
