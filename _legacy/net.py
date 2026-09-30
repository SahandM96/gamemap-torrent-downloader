#!/usr/bin/env python3
"""Torrent Desk — HTTP + magnet/size helpers (stdlib only, curl for transport).

Transport is `curl` on purpose: it speaks SOCKS5/HTTP proxies (Iran reality),
does TLS/HTTP2 properly and has sane timeouts. Cloudflare interstitials and DNS
blackholes surface as readable errors instead of empty results.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote, unquote_plus

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[0-9a-fA-F]{40}[^\"'\s<>\\}\]]*")
HASH_RE = re.compile(r"\b([0-9a-fA-F]{40})\b")
SIZE_RE = re.compile(r"([\d.,]+)\s*(TB|GB|MB|KB|TiB|GiB|MiB|KiB|B)\b", re.I)

TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://explodie.org:6969/announce",
]

_SIZE_UNITS = {
    "B": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
}


class SourceError(RuntimeError):
    """One site (=one mirror list) failed; the search continues elsewhere."""


def clean_text(raw: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", raw or "")).strip()


def parse_size(text: str) -> Optional[int]:
    match = SIZE_RE.search(text or "")
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(value * _SIZE_UNITS.get(match.group(2).upper(), 1))


def format_size(num: Optional[int]) -> str:
    if not num:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return "-"


def magnet_from_hash(info_hash: str, name: str = "", trackers: Sequence[str] = TRACKERS) -> str:
    parts = [f"magnet:?xt=urn:btih:{info_hash.upper()}"]
    if name:
        parts.append("dn=" + quote(name, safe=""))
    parts.extend("tr=" + quote(tracker, safe="") for tracker in trackers)
    return "&".join(parts)


def normalize_magnet(raw: str) -> str:
    return (raw or "").strip().replace("&amp;", "&")


def magnet_first(value: str) -> str:
    match = MAGNET_RE.search(value or "")
    return normalize_magnet(match.group(0)) if match else ""


def magnet_name(magnet: str) -> str:
    match = re.search(r"[?&]dn=([^&]+)", magnet or "")
    return unquote_plus(match.group(1)).replace("_", " ").strip() if match else ""
def _curl_failure(code: int, stderr: str) -> str:
    mapping = {
        5: "پروکسی جواب نداد",
        6: "DNS حل نشد (احتمالاً DNS-بلاک یا تحریم)",
        7: "اتصال برقرار نشد (فیلتر/شبکه)",
        28: "تایم‌اوت",
        35: "خطای TLS",
        47: "ریدایرکت بیش از حد",
        56: "خطای شبکه در دریافت داده",
    }
    if code in mapping:
        return mapping[code]
    tail = (stderr or "").strip().splitlines()
    return tail[-1] if tail else f"curl exit {code}"


def fetch(url: str, *, proxy: str = "", timeout: float = 14.0, headers: Optional[Dict[str, str]] = None) -> str:
    """GET a URL as text. Raises SourceError with a human reason on failure."""
    cmd = [
        "curl",
        "-sS",
        "-L",
        "--max-time",
        str(int(timeout)),
        "--compressed",
        "-A",
        USER_AGENT,
        "-H",
        "Accept-Language: en-US,en;q=0.9",
        "-w",
        "\n%{http_code}",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    for key, value in (headers or {}).items():
        cmd += ["-H", f"{key}: {value}"]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 8, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SourceError("تایم‌اوت") from exc
    text = proc.stdout.decode("utf-8", "replace")
    body, _, code = text.rpartition("\n")
    if proc.returncode != 0:
        raise SourceError(_curl_failure(proc.returncode, proc.stderr.decode("utf-8", "replace")))
    body = body or text
    if code and code != "200":
        raise SourceError(f"HTTP {code}")
    head = body[:4000]
    if "Just a moment" in head or "cf-chl" in head or "challenge-platform" in head:
        raise SourceError("Cloudflare چلنج — پروکسی/مرورگر لازم است")
    return body


def fetch_json(url: str, *, proxy: str = "", timeout: float = 14.0) -> Any:
    body = fetch(url, proxy=proxy, timeout=timeout)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SourceError("پاسخ JSON نبود") from exc


def fetch_many(urls: Sequence[str], *, proxy: str = "", timeout: float = 14.0, workers: int = 4) -> List[str]:
    """Fetch several detail pages in parallel; failed ones come back as ''."""
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as pool:
        futures = [pool.submit(fetch, url, proxy=proxy, timeout=timeout) for url in urls]
        out: List[str] = []
        for future in futures:
            try:
                out.append(future.result())
            except Exception:  # noqa: BLE001 - a dead detail page must not kill the search
                out.append("")
        return out


def rows_by_pattern(body: str, pattern: str, limit: int) -> List[str]:
    """Split a listing page into per-row chunks on a marker."""
    chunks = re.split(pattern, body)
    return chunks[1 : limit + 1]


def try_mirrors(mirrors: Sequence[str], call, *, attempts: int = 4) -> Any:
    """Run call(base) over mirrors until one works; raise with all reasons if none did.

    `attempts` caps how many mirrors we burn: each dead one costs a full timeout,
    so it stays small — but it must cover the whole list of a healthy source,
    otherwise a dead mirror at the top masks a working one at the bottom.
    """
    errors: List[str] = []
    for base in list(mirrors)[:attempts]:
        try:
            return call(base)
        except SourceError as exc:
            errors.append(f"{base}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface anything as a source error
            errors.append(f"{base}: {exc}")
    raise SourceError(" | ".join(errors) or "میان‌بری جواب نداد")



def magnet_infohash(magnet: str) -> str:
    match = re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", magnet or "")
    return match.group(1).lower() if match else ""


def _bdecode(data: bytes, idx: int = 0):
    if idx >= len(data):
        raise ValueError("unexpected end of bencoded data")
    tag = data[idx:idx + 1]
    if tag == b"i":
        end = data.index(b"e", idx)
        return int(data[idx + 1:end]), end + 1
    elif tag == b"l":
        idx += 1
        res = []
        while data[idx:idx + 1] != b"e":
            v, idx = _bdecode(data, idx)
            res.append(v)
        return res, idx + 1
    elif tag == b"d":
        idx += 1
        res = {}
        while data[idx:idx + 1] != b"e":
            k, idx = _bdecode(data, idx)
            v, idx = _bdecode(data, idx)
            res[k] = v
        return res, idx + 1
    elif tag.isdigit():
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise ValueError(f"unknown bencode byte at {idx}: {tag!r}")


def parse_torrent_bytes(data: bytes) -> Dict[str, Any]:
    """Extract info_hash (SHA1 of info dict), torrent SHA1, and root name."""
    import hashlib

    pos = data.find(b"4:info")
    if pos == -1:
        return {}
    start = pos + 6
    try:
        info_dict, end = _bdecode(data, start)
    except Exception:
        return {}
    raw_info = data[start:end]
    info_hash = hashlib.sha1(raw_info).hexdigest().lower()
    torrent_sha1 = hashlib.sha1(data).hexdigest().lower()
    name = info_dict.get(b"name.utf-8") or info_dict.get(b"name")
    if isinstance(name, bytes):
        name = name.decode("utf-8", errors="replace")
    return {
        "info_hash": info_hash,
        "torrent_sha1": torrent_sha1,
        "name": name if isinstance(name, str) else "",
    }
