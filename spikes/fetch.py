"""Guarded URL fetch for the web adapter (spike 0e, design decision 7).

usage: python3 spikes/fetch.py <url> <out.html>   (prints a JSON summary)

- http/https only (curl --proto/--proto-redir as well).
- The host is resolved here and every address must be public: private,
  loopback, link-local, multicast, reserved and unspecified addresses
  (including 169.254.169.254 metadata) are refused. curl is pinned to the
  checked address with --resolve, so DNS can't change between check and fetch.
- Redirects are followed manually (max 5) and each hop is re-checked.
- --max-time 30, --max-filesize 20M, and the decompressed body is streamed
  with a 20 MB cap (gzip bombs stop at the cap).
- Charset from Content-Type, then <meta charset>, then UTF-8 with replacement.
"""
import ipaddress
import json
import re
import socket
import subprocess
import sys
import urllib.parse

MAX_BYTES = 20 * 1024 * 1024
MAX_REDIRS = 5
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


class FetchError(Exception):
    pass


def check_host(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise FetchError(f"scheme not allowed: {u.scheme or '(none)'}")
    if not u.hostname:
        raise FetchError("no host")
    if u.username or u.password:
        raise FetchError("credentials in URL not allowed")
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {u.hostname}: {e}")
    addrs = sorted({i[4][0] for i in infos})
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise FetchError(f"refusing non-public address {ip} for {u.hostname}")
    return u.hostname, port, addrs[0]


def fetch_once(url):
    host, port, ip = check_host(url)
    pin = f"[{ip}]" if ":" in ip else ip
    cmd = ["curl", "-sS", "--proto", "=http,https", "--proto-redir", "=http,https", "--max-redirs", "0",
           "--max-time", "30", "--max-filesize", str(MAX_BYTES), "--compressed", "-A", UA,
           "--resolve", f"{host}:{port}:{pin}", "-D", "-", "-o", "-", url]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = bytearray()
    while True:
        chunk = p.stdout.read(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_BYTES + 65536:
            p.kill()
            raise FetchError("response larger than 20 MB (after decompression)")
    err = p.stderr.read().decode("utf-8", "replace")
    if p.wait() != 0:
        raise FetchError(f"curl failed: {err.strip()[:200]}")
    # split headers (possibly several blocks for 100-continue) from body
    hdr_end = 0
    while True:
        m = re.match(rb"HTTP/[\d.]+ \d{3}.*?\r?\n\r?\n", bytes(buf[hdr_end:hdr_end + 65536]), re.S)
        if not m:
            break
        last = (hdr_end, hdr_end + m.end())
        hdr_end += m.end()
        if not re.match(rb"HTTP/[\d.]+ 1\d\d", m.group(0)):
            break
    head = bytes(buf[last[0]:last[1]]).decode("latin-1")
    body = bytes(buf[hdr_end:])
    status = int(re.match(r"HTTP/[\d.]+ (\d{3})", head).group(1))
    headers = {}
    for line in head.split("\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def fetch(url):
    hops = []
    for _ in range(MAX_REDIRS + 1):
        status, headers, body = fetch_once(url)
        hops.append({"url": url, "status": status})
        if status in (301, 302, 303, 307, 308) and "location" in headers:
            url = urllib.parse.urljoin(url, headers["location"])
            continue
        if status >= 400:
            raise FetchError(f"HTTP {status} for {url}")
        return url, headers, body, hops
    raise FetchError("too many redirects")


def decode(headers, body):
    cs = None
    m = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
    if m:
        cs = m.group(1)
    else:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        if m:
            cs = m.group(1).decode("ascii", "replace")
    try:
        return body.decode(cs or "utf-8", "replace"), cs or "utf-8"
    except LookupError:
        return body.decode("utf-8", "replace"), "utf-8 (fallback)"


def main():
    url, out = sys.argv[1], sys.argv[2]
    try:
        final, headers, body, hops = fetch(url)
    except FetchError as e:
        print(json.dumps({"ok": False, "url": url, "error": str(e)}))
        sys.exit(2)
    text, cs = decode(headers, body)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(json.dumps({"ok": True, "url": url, "final_url": final, "hops": hops, "bytes": len(body),
                      "content_type": headers.get("content-type"), "charset": cs,
                      "etag": headers.get("etag"), "last_modified": headers.get("last-modified")}))


if __name__ == "__main__":
    main()
