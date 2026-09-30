#!/usr/bin/env python3
"""Torrent Desk — search adapters for the 10 Kickass Torrents alternatives.

Sites are the ones listed in the bitbrowser.com roundup (2026): The Pirate Bay,
1337x, YTS, EZTV, TorrentDownloads, LimeTorrents, Nyaa, Internet Archive,
FitGirl Repacks, TorrentGalaxy.

Every source is best-effort: it tries its mirrors in order, turns a failure into
a readable per-source status (DNS block, Cloudflare, timeout, ...) and never
breaks the other sources. JSON APIs are used where the site offers one,
otherwise the listing HTML is scraped.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from net import (
    HASH_RE,
    MAGNET_RE,
    SourceError,
    clean_text,
    fetch,
    fetch_json,
    fetch_many,
    magnet_first,
    magnet_from_hash,
    magnet_infohash,
    magnet_name,
    normalize_magnet,
    parse_size,
    rows_by_pattern,
    try_mirrors,
)

DEFAULT_TIMEOUT = 14.0
# Whole-search wall budget; must stay under the Rust bridge kill (75s).
SEARCH_BUDGET = 45.0


@dataclass
class Ctx:
    proxy: str = ""
    timeout: float = DEFAULT_TIMEOUT
    detail_limit: int = 6  # detail pages a scraper may open per search
    eztv_pages: int = 4  # EZTV has no search API: scan the newest N*100 items


@dataclass
class Result:
    source: str
    name: str
    magnet: str = ""
    torrent_url: str = ""
    page_url: str = ""
    size_bytes: Optional[int] = None
    seeds: Optional[int] = None
    leechers: Optional[int] = None
    added: str = ""
    category: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Source:
    id: str
    label: str
    group: str
    homepage: str
    mirrors: List[str]
    probe: str
    note: str
    fn: Callable[[str, int, Ctx], List[Result]] = field(repr=False)
    strict: bool = False  # drop rows that do not match the query (site ignored it)


def _n(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str:
    num = _n(value)
    if not num:
        return ""
    try:
        return datetime.fromtimestamp(num, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _tidy(title: str) -> str:
    return re.sub(r"\s+", " ", clean_text(title).lstrip(" -–—|")).strip()


def _infohash_in(page: str) -> str:
    match = re.search(r"(?:Infohash|Info Hash|infohash)[^0-9a-fA-F]{0,20}([0-9a-fA-F]{40})", page or "")
    if match:
        return match.group(1)
    fallback = HASH_RE.search(page or "")
    return fallback.group(1) if fallback else ""

# --------------------------------------------------------------- adapters
TPB_CATEGORY = {
    "101": "Music", "102": "Music", "103": "Music", "104": "Music",
    "201": "Movies", "202": "Movies", "203": "Movies", "204": "Movies",
    "205": "TV", "206": "TV", "207": "HD Movies", "208": "HD Movies", "209": "3D",
    "301": "Software", "302": "Software", "303": "Software", "304": "Software",
    "401": "Games", "402": "Games", "403": "Games", "404": "Games",
    "601": "Books", "602": "Books", "603": "Books", "604": "Books",
    "701": "Other", "702": "Other", "703": "Other", "704": "Other",
}

NYAA_CATEGORY = {
    "1_0": "Anime", "1_1": "Anime", "1_2": "Anime", "2_0": "Audio",
    "3_0": "Literature", "4_0": "Live Action", "5_0": "Pictures", "6_0": "Software",
}


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _thepiratebay(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def call(base: str) -> List[Result]:
        data = fetch_json(f"{base}/q.php?q={_q(query)}", proxy=ctx.proxy, timeout=ctx.timeout)
        if not isinstance(data, list):
            raise SourceError("پاسخ نامعتبر")
        rows: List[Result] = []
        for raw in data:
            if str(raw.get("id")) == "0":
                continue
            info_hash = str(raw.get("info_hash") or "")
            if len(info_hash) != 40:
                continue
            name = _tidy(str(raw.get("name") or ""))
            rows.append(
                Result(
                    source="thepiratebay", name=name,
                    magnet=magnet_from_hash(info_hash, name),
                    page_url=f"https://thepiratebay.org/description.php?id={raw.get('id')}",
                    size_bytes=_n(raw.get("size")), seeds=_n(raw.get("seeders")),
                    leechers=_n(raw.get("leechers")), added=_date(raw.get("added")),
                    category=TPB_CATEGORY.get(str(raw.get("category")), ""),
                )
            )
            if len(rows) >= limit:
                break
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    return try_mirrors(mirrors, call)


def _yts(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    """YTS: prefer JSON API; fall back to yts.do ajax + movie HTML (API often 404 from Iran)."""

    def from_api(base: str) -> List[Result]:
        url = (
            f"{base}/api/v2/list_movies.json?limit={min(max(limit, 5), 30)}"
            f"&query_term={_q(query)}&sort_by=seeds"
        )
        data = fetch_json(url, proxy=ctx.proxy, timeout=ctx.timeout)
        movies = ((data or {}).get("data") or {}).get("movies") or []
        rows: List[Result] = []
        for movie in movies:
            title = f"{movie.get('title')} ({movie.get('year')})"
            for item in movie.get("torrents") or []:
                info_hash = str(item.get("hash") or "")
                if len(info_hash) != 40:
                    continue
                label = f"{title} [{item.get('quality')}] [{item.get('type')}]"
                rows.append(
                    Result(
                        source="yts", name=label, magnet=magnet_from_hash(info_hash, label),
                        page_url=str(movie.get("url") or ""),
                        size_bytes=parse_size(str(item.get("size") or "")),
                        seeds=_n(item.get("seeds")), leechers=_n(item.get("peers")),
                        category="Movies",
                    )
                )
                if len(rows) >= limit:
                    return rows
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    def from_html(base: str) -> List[Result]:
        # yts.do exposes autocomplete JSON; movie pages carry magnets + P/S.
        data = fetch_json(f"{base}/ajax/search?query={_q(query)}", proxy=ctx.proxy, timeout=ctx.timeout)
        movies = (data or {}).get("data") or []
        if not movies:
            raise SourceError("نتیجه‌ای نداشت")
        picked = movies[: max(1, min(limit, ctx.detail_limit))]
        urls = []
        for movie in picked:
            path = str(movie.get("url") or "")
            if path.startswith("/"):
                urls.append(base + path)
            elif path.startswith("http"):
                urls.append(path)
            else:
                urls.append("")
        pages = fetch_many(urls, proxy=ctx.proxy, timeout=ctx.timeout)
        rows: List[Result] = []
        ps_re = re.compile(r'tech-peers-seeds">P/S</span>\s*(\d+)\s*/\s*(\d+)', re.I)
        for movie, page_url, page in zip(picked, urls, pages):
            if not page:
                continue
            title = f"{movie.get('title')} ({movie.get('year')})"
            # P/S lives in the per-quality tech block; map infohash -> (peers, seeds) by order.
            page_ps = [(_n(p), _n(s)) for p, s in ps_re.findall(page)]
            seen_hash: set = set()
            for block in re.split(r"modal-torrent", page)[1:]:
                magnet = magnet_first(block)
                if not magnet:
                    continue
                ih = magnet_infohash(magnet)
                if ih and ih in seen_hash:
                    continue
                if ih:
                    seen_hash.add(ih)
                own = ps_re.search(block)
                if own:
                    leech, seeds = _n(own.group(1)), _n(own.group(2))
                elif len(page_ps) > len(seen_hash) - 1:
                    leech, seeds = page_ps[len(seen_hash) - 1]
                else:
                    leech, seeds = (page_ps[0] if page_ps else (None, None))
                qual = re.search(r"modal-quality[^>]*>\s*<span>([^<]+)", block)
                sizes = re.findall(r'quality-size">([^<]+)', block)
                size_str = sizes[-1] if sizes else ""
                kind = sizes[0] if len(sizes) >= 2 else ""
                label = title
                if qual:
                    label += f" [{qual.group(1).strip()}]"
                if kind:
                    label += f" [{kind.strip()}]"
                rows.append(
                    Result(
                        source="yts", name=label, magnet=magnet, page_url=page_url,
                        size_bytes=parse_size(size_str), seeds=seeds, leechers=leech,
                        category="Movies",
                    )
                )
                if len(rows) >= limit:
                    return rows
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    def call(base: str) -> List[Result]:
        try:
            return from_api(base)
        except SourceError:
            return from_html(base)

    return try_mirrors(mirrors, call, attempts=max(4, len(list(mirrors))))


def _eztv(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    """EZTV has no search API — scan the newest pages of get-torrents and filter."""
    tokens = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2]

    def call(base: str) -> List[Result]:
        rows: List[Result] = []
        for page in range(1, max(1, ctx.eztv_pages) + 1):
            url = f"{base}/api/get-torrents?limit=100&page={page}"
            try:
                data = fetch_json(url, proxy=ctx.proxy, timeout=ctx.timeout)
            except SourceError:
                if page == 1:
                    raise
                break
            items = (data or {}).get("torrents") or []
            if not items:
                break
            for item in items:
                name = str(item.get("filename") or "")
                if tokens and not all(token in name.lower() for token in tokens):
                    continue
                magnet = normalize_magnet(str(item.get("magnet_url") or ""))
                info_hash = str(item.get("hash") or "")
                if not magnet and len(info_hash) == 40:
                    magnet = magnet_from_hash(info_hash, name)
                if not magnet:
                    continue
                clean = re.sub(r"\[(?:eztvx?[^\]]*|EZTV[^\]]*)\]", "", name).strip()
                rows.append(
                    Result(
                        source="eztv", name=clean, magnet=magnet,
                        page_url=f"{base}/ep/{item.get('id')}",
                        size_bytes=_n(item.get("size_bytes")), seeds=_n(item.get("seeds")),
                        leechers=_n(item.get("peers")),
                        added=_date(item.get("date_released_unix")), category="TV",
                    )
                )
                if len(rows) >= limit:
                    return rows
        if not rows:
            raise SourceError(f"در {ctx.eztv_pages * 100} عنوان آخر چیزی مطابق «{query}» نبود")
        return rows

    return try_mirrors(mirrors, call)


def _nyaa(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def from_rss(body: str) -> List[Result]:
        rows: List[Result] = []
        for item in re.split(r"<item[ >]", body)[1 : limit + 1]:

            def tag(name: str) -> str:
                match = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", item, re.S)
                return clean_text(match.group(1)) if match else ""

            magnet = magnet_first(item)
            if not magnet:
                continue
            rows.append(
                Result(
                    source="nyaa", name=tag("title"), magnet=magnet,
                    page_url=tag("guid"), size_bytes=parse_size(tag("nyaa:size")),
                    seeds=_n(tag("nyaa:seeders")), leechers=_n(tag("nyaa:leechers")),
                    added=tag("pubDate")[:16],
                    category=NYAA_CATEGORY.get(tag("nyaa:categoryId"), "Anime"),
                )
            )
        return rows

    def from_html(body: str) -> List[Result]:
        rows: List[Result] = []
        for block in rows_by_pattern(body, r'<tr class="(?:default|success|danger|warning)"', limit):
            magnet = magnet_first(block)
            title = re.search(r'href="/view/\d+"[^>]*title="([^"]+)"', block) or re.search(
                r'href="/view/\d+"[^>]*>([^<]+)<', block
            )
            if not magnet or not title:
                continue
            numbers = re.findall(r">\s*(\d+)\s*</td>", block)
            link = re.search(r'href="(/view/\d+)"', block)
            rows.append(
                Result(
                    source="nyaa", name=_tidy(title.group(1)), magnet=magnet,
                    page_url=link.group(1) if link else "", size_bytes=parse_size(block),
                    seeds=_n(numbers[-2]) if len(numbers) >= 2 else None,
                    leechers=_n(numbers[-1]) if len(numbers) >= 2 else None,
                    category="Anime",
                )
            )
        # The HTML path only appears when the mirror ignored ?q= — prove it matched.
        tokens = _tokens(query)
        if tokens:
            kept = [row for row in rows if _matches_query(row.name, tokens)]
            if not kept:
                raise SourceError("این آینه جست‌وجوی nyaa را پشتیبانی نمی‌کند (فقط صفحه اول)")
            return kept
        return rows

    def call(base: str) -> List[Result]:
        # Some mirrors only honour the RSS endpoint, others only the HTML `?q=`
        # search. Try RSS first, then fall back to the HTML search on the same
        # mirror (the RSS URL silently degrades to the front page, which the
        # strict query filter then rejects).
        try:
            body = fetch(f"{base}/?page=rss&q={_q(query)}&c=0_0&f=0", proxy=ctx.proxy, timeout=ctx.timeout)
            rows = from_rss(body) if "<item" in body else []
            if rows:
                return rows[:limit]
        except SourceError:
            pass
        body = fetch(f"{base}/?q={_q(query)}&c=0_0&f=0", proxy=ctx.proxy, timeout=ctx.timeout)
        rows = from_rss(body) if "<item" in body else from_html(body)
        if not rows:
            raise SourceError("نتیجه‌ای نداشت (یا این میان‌بر جست‌وجو را پشتیبانی نمی‌کند)")
        return rows[:limit]

    return try_mirrors(mirrors, call)


def _archive(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def call(base: str) -> List[Result]:
        search = f"({query}) AND mediatype:(movies OR audio OR texts OR software OR etree)"
        url = (
            f"{base}/advancedsearch.php?q={_q(search)}"
            "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=year&fl%5B%5D=mediatype&fl%5B%5D=downloads"
            f"&sort%5B%5D=downloads+desc&rows={min(max(limit, 5), 30)}&page=1&output=json"
        )
        data = fetch_json(url, proxy=ctx.proxy, timeout=ctx.timeout)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        rows: List[Result] = []
        for doc in docs:
            identifier = str(doc.get("identifier") or "")
            if not identifier:
                continue
            year = doc.get("year")
            rows.append(
                Result(
                    source="archive",
                    name=str(doc.get("title") or identifier) + (f" ({year})" if year else ""),
                    torrent_url=f"{base}/download/{identifier}/{identifier}_archive.torrent",
                    page_url=f"https://archive.org/details/{identifier}",
                    leechers=_n(doc.get("downloads")),
                    category=str(doc.get("mediatype") or "archive"),
                )
            )
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows[:limit]

    return try_mirrors(mirrors, call)


# ------------------------------------------- listing + detail scraper helper
def _scrape(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str], *, source: str,
            list_url: str, row_marker: str, extract) -> List[Result]:
    """Generic two-step scraper: listing page -> rows -> detail pages -> magnet."""
    def call(base: str) -> List[Result]:
        body = fetch(list_url.format(base=base, q=_q(query)), proxy=ctx.proxy, timeout=ctx.timeout)
        found: List[Dict[str, Any]] = []
        wanted = max(limit, ctx.detail_limit)
        for block in rows_by_pattern(body, row_marker, 400):
            row = extract(block, base)
            if row:
                found.append(row)
            if len(found) >= wanted:
                break
        if not found:
            raise SourceError("نتیجه‌ای نداشت")
        pages = fetch_many([row["detail"] for row in found[: ctx.detail_limit]],
                           proxy=ctx.proxy, timeout=ctx.timeout, workers=4)
        rows: List[Result] = []
        for row, page in zip(found, pages):
            magnet = magnet_first(page)
            if not magnet:
                info_hash = _infohash_in(page)
                if len(info_hash) == 40:
                    magnet = magnet_from_hash(info_hash, row["name"])
            if not magnet:
                continue
            rows.append(
                Result(
                    source=source, name=row["name"], magnet=magnet, page_url=row["detail"],
                    size_bytes=row.get("size_bytes"), seeds=row.get("seeds"),
                    leechers=row.get("leechers"), category=row.get("category", ""),
                )
            )
        if not rows:
            raise SourceError("در صفحه‌های جزئیات مغناطیس پیدا نشد")
        return rows[:limit]

    return try_mirrors(mirrors, call)

def _torrentdownloads(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def extract(block: str, base: str) -> Optional[Dict[str, Any]]:
        match = re.search(r'<a href="(/torrent/[^"]+?)"[^>]*title="View torrent info : ([^"]*)"', block)
        if not match:
            return None
        spans = [clean_text(s) for s in re.findall(r"<span[^>]*>([^<]*)</span>", block)]
        numbers = [int(s) for s in spans if s.strip().isdigit()]
        size = next((parse_size(s) for s in spans if parse_size(s)), None)
        return {
            "detail": base + match.group(1).split("#")[0], "name": _tidy(match.group(2)),
            "size_bytes": size, "seeds": numbers[0] if numbers else None,
            "leechers": numbers[1] if len(numbers) > 1 else None,
        }

    return _scrape(
        query, limit, ctx, mirrors, source="torrentdownloads",
        list_url="{base}/search/?search={q}", row_marker=r'<div class="grey_bar3', extract=extract,
    )


def _limetorrents(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def extract(block: str, base: str) -> Optional[Dict[str, Any]]:
        # The name cell holds TWO anchors: a direct .torrent link (no text) and
        # the title link ending in `-torrent-<id>.html`. Anchoring on the whole
        # cell keeps the title link even when the direct link comes first.
        cell = re.search(r'<div class="tt-name">(.*?)</div>', block, re.S)
        inner = cell.group(1) if cell else block
        match = re.search(r'href="((?:https?:)?//[^"]+?-torrent-\d+\.html)"[^>]*>([^<]{3,300})<', inner)
        if not match:
            match = re.search(r'href="(/[^"]+?-torrent-\d+\.html)"[^>]*>([^<]{3,300})<', inner)
        if not match:
            return None
        href = match.group(1)
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = base + href
        numbers = [int(x) for x in re.findall(r'class="td(?:seed|leech)"[^>]*>\s*(\d+)', block)]
        return {
            "detail": href, "name": _tidy(match.group(2)), "size_bytes": parse_size(block),
            "seeds": numbers[0] if numbers else None,
            "leechers": numbers[1] if len(numbers) > 1 else None,
        }

    return _scrape(
        query, limit, ctx, mirrors, source="limetorrents",
        list_url="{base}/search/all/{q}/", row_marker=r"<tr[^>]*>", extract=extract,
    )


def _1337x(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def extract(block: str, base: str) -> Optional[Dict[str, Any]]:
        match = re.search(r'<a href="(/torrent/\d+/[^"]+)"[^>]*>([^<]{3,300})</a>', block)
        if not match:
            return None
        name = _tidy(match.group(2))
        if not name or name.lower() in {"download", "magnet download"}:
            return None
        seeds = re.search(r'class="coll-2 seeds">\s*(\d+)', block)
        leeches = re.search(r'class="coll-3 leeches">\s*(\d+)', block)
        size = re.search(r'class="coll-4 size[^"]*"[^>]*>\s*([\d.]+\s*[KMGT]i?B)', block)
        return {
            "detail": base + match.group(1), "name": name,
            "seeds": _n(seeds.group(1)) if seeds else None,
            "leechers": _n(leeches.group(1)) if leeches else None,
            "size_bytes": parse_size(size.group(1)) if size else None,
            "category": "Movies/TV",
        }

    return _scrape(
        query, limit, ctx, mirrors, source="1337x",
        list_url="{base}/search/{q}/1/", row_marker=r"<tr>", extract=extract,
    )


def _torrentgalaxy(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    """TorrentGalaxy — listing via /get-posts/keywords:; magnets live on detail pages."""

    def call(base: str) -> List[Result]:
        body = ""
        last_err: Optional[Exception] = None
        for path in (
            f"/get-posts/keywords:{_q(query)}/",
            f"/?search={_q(query)}",
            f"/torrents.php?search={_q(query)}",
        ):
            try:
                body = fetch(base + path, proxy=ctx.proxy, timeout=ctx.timeout)
                if "post-detail" in body or "tgxtablerow" in body:
                    break
            except SourceError as exc:
                last_err = exc
                body = ""
        if not body:
            raise last_err or SourceError("نتیجه‌ای نداشت")

        wanted = max(limit, ctx.detail_limit)
        found: List[Dict[str, Any]] = []
        for block in rows_by_pattern(body, r'class="tgxtablerow', wanted):
            link = re.search(r'data-href="(/post-detail/[^"]+)"', block) or re.search(
                r'href="(/post-detail/[^"]+)"', block
            )
            if not link:
                continue
            title = re.search(
                r'<a class="txlight" title="([^"]{3,300})"\s+href="/post-detail/', block
            ) or re.search(r'href="/post-detail/[^"]+"[^>]*>\s*<span[^>]*>\s*<b>([^<]{3,300})</b>', block)
            size = re.search(r'badge[^>]*>\s*([^<]+)', block)
            seedleech = re.search(
                r'title="Seeders/Leechers">.*?<b>(\d+)</b>.*?<b>(\d+)</b>', block, re.S
            )
            found.append(
                {
                    "path": link.group(1),
                    "name": _tidy(title.group(1)) if title else "",
                    "size": parse_size(size.group(1)) if size else None,
                    "seeds": _n(seedleech.group(1)) if seedleech else None,
                    "leech": _n(seedleech.group(2)) if seedleech else None,
                    "magnet": magnet_first(block) or "",
                }
            )
            if len(found) >= wanted:
                break

        if not found:
            raise SourceError("نتیجه‌ای نداشت")

        need = [row for row in found if not row["magnet"]][: ctx.detail_limit]
        pages = fetch_many(
            [base + row["path"] for row in need], proxy=ctx.proxy, timeout=ctx.timeout
        )
        by_path = {row["path"]: page for row, page in zip(need, pages)}

        rows: List[Result] = []
        for meta in found:
            magnet = meta["magnet"] or magnet_first(by_path.get(meta["path"], ""))
            if not magnet:
                continue
            name = meta["name"] or magnet_name(magnet) or meta["path"]
            rows.append(
                Result(
                    source="torrentgalaxy",
                    name=name,
                    magnet=magnet,
                    page_url=base + meta["path"],
                    size_bytes=meta["size"],
                    seeds=meta["seeds"],
                    leechers=meta["leech"],
                    category="Movies/TV",
                )
            )
            if len(rows) >= limit:
                break
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    return try_mirrors(mirrors, call, attempts=max(4, len(list(mirrors))))


def _fitgirl(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    def call(base: str) -> List[Result]:
        body = fetch(f"{base}/?s={_q(query)}", proxy=ctx.proxy, timeout=ctx.timeout)
        posts = re.findall(
            r'<h[123][^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            body, re.S,
        )
        if not posts:
            raise SourceError("نتیجه‌ای نداشت")
        picked = posts[: ctx.detail_limit]
        pages = fetch_many([post[0] for post in picked], proxy=ctx.proxy, timeout=ctx.timeout)
        rows: List[Result] = []
        seen = set()
        for (url, title), page in zip(picked, pages):
            label = _tidy(title)
            for match in MAGNET_RE.finditer(page):
                magnet = normalize_magnet(match.group(0))
                key = magnet_infohash(magnet)
                if not key or key in seen:
                    continue
                seen.add(key)
                extra = magnet_name(magnet)
                name = f"{label} — {extra}" if extra and extra.lower() not in label.lower() else label
                rows.append(
                    Result(source="fitgirl", name=name, magnet=magnet, page_url=url, category="Games")
                )
                if len(rows) >= limit:
                    return rows
        if not rows:
            raise SourceError("در پست‌ها مغناطیسی نبود (FitGirl بیشتر به سایت‌های دیگر لینک می‌دهد)")
        return rows

    return try_mirrors(mirrors, call)


def _torrents_csv(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    """Public torrents-csv.com JSON API — works well from Iran, includes seeders."""

    def call(base: str) -> List[Result]:
        url = f"{base}/service/search?q={_q(query)}&size={min(max(limit, 5), 50)}"
        data = fetch_json(url, proxy=ctx.proxy, timeout=ctx.timeout)
        items = (data or {}).get("torrents") or []
        rows: List[Result] = []
        for item in items:
            info_hash = str(item.get("infohash") or "")
            if len(info_hash) != 40:
                continue
            name = _tidy(str(item.get("name") or info_hash))
            rows.append(
                Result(
                    source="torrents_csv",
                    name=name,
                    magnet=magnet_from_hash(info_hash, name),
                    page_url=f"https://torrents-csv.com/#/search?q={_q(query)}",
                    size_bytes=_n(item.get("size_bytes")),
                    seeds=_n(item.get("seeders")),
                    leechers=_n(item.get("leechers")),
                    added=_date(item.get("created_unix")),
                    category="",
                )
            )
            if len(rows) >= limit:
                break
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    return try_mirrors(mirrors, call, attempts=3)


def _knaben(query: str, limit: int, ctx: Ctx, mirrors: Sequence[str]) -> List[Result]:
    """Knaben aggregator HTML — magnets + nearby seed/size columns when present."""

    def call(base: str) -> List[Result]:
        body = fetch(f"{base}/search/{_q(query)}", proxy=ctx.proxy, timeout=ctx.timeout)
        rows: List[Result] = []
        seen = set()
        for block in re.split(r"<tr[\s>]", body, flags=re.I)[1:]:
            magnet = magnet_first(block)
            if not magnet:
                continue
            ih = magnet_infohash(magnet)
            if not ih or ih in seen:
                continue
            seen.add(ih)
            cells = [
                re.sub(r"\s+", " ", clean_text(re.sub(r"<[^>]+>", " ", td))).strip()
                for td in re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)
            ]
            # Prefer magnet-link title= (real release name); skip badge tooltips.
            title = ""
            mag_title = re.search(
                r'<a[^>]+href="magnet:[^"]+"[^>]*title="([^"]{3,200})"', block, re.I
            ) or re.search(
                r'title="([^"]{3,200})"[^>]*href="magnet:', block, re.I
            )
            if mag_title:
                title = _tidy(mag_title.group(1))
            if not title and len(cells) >= 2:
                title = _tidy(cells[1])
            if not title:
                title = magnet_name(magnet) or ih
            if title.lower().startswith("this torrent was checked"):
                title = magnet_name(magnet) or (cells[1] if len(cells) >= 2 else ih)
            size_bytes = parse_size(cells[2]) if len(cells) >= 3 else parse_size(block)
            seeds = _n(cells[4]) if len(cells) >= 5 else None
            leech = _n(cells[5]) if len(cells) >= 6 else None
            rows.append(
                Result(
                    source="knaben",
                    name=title,
                    magnet=magnet,
                    page_url=f"{base}/search/{_q(query)}",
                    size_bytes=size_bytes,
                    seeds=seeds,
                    leechers=leech,
                    category=cells[0] if cells else "",
                )
            )
            if len(rows) >= limit:
                break
        if not rows:
            raise SourceError("نتیجه‌ای نداشت")
        return rows

    return try_mirrors(mirrors, call, attempts=3)


# ---------------------------------------------------------------- registry
SOURCES: List[Source] = [
    Source(
        id="thepiratebay", label="The Pirate Bay", group="general",
        homepage="https://thepiratebay.org", mirrors=["https://apibay.org"],
        probe="/q.php?q=arch",
        note="از API عمومی apibay استفاده می‌کند. دامنه اصلی TPB از ایران DNS-بلاک است.",
        fn=_thepiratebay,
    ),
    Source(
        id="torrents_csv", label="Torrents CSV", group="general",
        homepage="https://torrents-csv.com",
        mirrors=["https://torrents-csv.com"],
        probe="/service/search?q=matrix&size=1",
        note="ایندکس عمومی با سیچر/حجم — از ایران معمولاً بدون پروکسی کار می‌کند.",
        fn=_torrents_csv,
    ),
    Source(
        id="knaben", label="Knaben", group="general",
        homepage="https://knaben.org",
        mirrors=["https://knaben.org", "https://knaben.eu"],
        probe="/search/matrix",
        note="تجمیع‌کننده چند منبع؛ سیچر را از جدول استخراج می‌کند.",
        fn=_knaben,
    ),
    Source(
        id="1337x", label="1337x", group="movies",
        homepage="https://1337x.to",
        mirrors=["https://1337x.to", "https://1337x.st", "https://x1337x.ws", "https://x1337x.eu", "https://www.1377x.to"],
        probe="/search/matrix/1/",
        note="⚠ مسدود/Cloudflare از ایران — پروکسی لازم است.",
        fn=_1337x,
    ),
    Source(
        id="yts", label="YTS", group="movies",
        homepage="https://yts.do",
        mirrors=[
            "https://yts.do",
            "https://yts.mx",
            "https://yts.lt",
            "https://yts.rs",
            "https://yts.ag",
        ],
        probe="/ajax/search?query=matrix",
        note="از yts.do (ajax + صفحه فیلم) استفاده می‌کند؛ API رسمی از ایران اغلب ۵۰۰/تایم‌اوت است.",
        fn=_yts,
    ),
    Source(
        id="eztv", label="EZTV", group="tv",
        homepage="https://eztv.re",
        mirrors=["https://eztvx.to", "https://eztv.re", "https://eztv.yt", "https://eztv.ch", "https://eztv.ag"],
        probe="/api/get-torrents?limit=1",
        note="سریال؛ API سرچ ندارد، فقط عناوین اخیر اسکن می‌شود — برای فیلم قدیمی مثل Snowden نتیجه ندارد.",
        fn=_eztv,
    ),
    Source(
        id="torrentdownloads", label="TorrentDownloads", group="general",
        homepage="https://www.torrentdownloads.pro",
        mirrors=["https://www.torrentdownloads.pro", "https://www.torrentdownloads.cc"],
        probe="/search/?search=matrix",
        note="برای محتوای کمیاب و قدیمی خوب است.",
        fn=_torrentdownloads,
    ),
    Source(
        id="limetorrents", label="LimeTorrents", group="general",
        homepage="https://limetorrents.info",
        mirrors=[
            "https://www.limetorrents.fun", "https://www.limetorrents.info",
            "https://www.limetorrents.cc",
            "https://www.limetorrents.asia",
        ],
        probe="/search/all/matrix/",
        note="انتشارهای جدید؛ اگر آینه عوض شد در mirrors.json به‌روز کنید.",
        fn=_limetorrents,
    ),
    Source(
        id="nyaa", label="Nyaa", group="anime",
        homepage="https://nyaa.si",
        mirrors=["https://nyaa.iss.one", "https://nyaa.ink", "https://nyaa.digital", "https://nyaa.si"],
        probe="/?page=rss&q=matrix",
        note="⚠ از ایران اغلب ۵۰۲/مسدود. برای انیمه پروکسی لازم است.",
        fn=_nyaa,
    ),
    Source(
        id="archive", label="Internet Archive", group="legal",
        homepage="https://archive.org", mirrors=["https://archive.org"],
        probe="/advancedsearch.php?q=matrix&rows=1&output=json",
        note="کاملاً قانونی؛ دانلود با فایل .torrent (نه مغناطیس).",
        fn=_archive,
    ),
    Source(
        id="fitgirl", label="FitGirl Repacks", group="games",
        homepage="https://fitgirl-repacks.site", mirrors=["https://fitgirl-repacks.site"],
        probe="/?s=matrix",
        note="⚠ از ایران مسدود. برای بازی از Torrents CSV / TorrentDownloads یا پروکسی استفاده کن.",
        fn=_fitgirl,
    ),
    Source(
        id="torrentgalaxy", label="TorrentGalaxy", group="movies",
        homepage="https://en.torrentgalaxy-official.is",
        mirrors=[
            "https://torrentgalaxy.one",
            "https://en.torrentgalaxy-official.is",
            "https://torrentgalaxy-official.is",
            "https://torrentgalaxy.info",
        ],
        probe="/get-posts/keywords:matrix/",
        note="آینهٔ torrentgalaxy.one از ایران معمولاً کار می‌کند.",
        fn=_torrentgalaxy, strict=True,
    ),
]

SOURCE_MAP: Dict[str, Source] = {source.id: source for source in SOURCES}


def _apply_mirror_overrides() -> None:
    """Optional `mirrors.json` = {"<source id>": ["https://new-mirror", ...]}.

    Torrent domains rot constantly; this lets you patch them without touching code.
    """
    path = Path(__file__).resolve().parent / "mirrors.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    for source_id, mirrors in data.items():
        source = SOURCE_MAP.get(str(source_id))
        if source and isinstance(mirrors, list):
            cleaned = [str(m).rstrip("/") for m in mirrors if str(m).startswith("http")]
            if cleaned:
                source.mirrors = cleaned


_apply_mirror_overrides()


def source_meta() -> List[Dict[str, Any]]:
    return [
        {
            "id": source.id, "label": source.label, "group": source.group,
            "homepage": source.homepage, "mirrors": source.mirrors, "note": source.note,
        }
        for source in SOURCES
    ]


def _tokens(query: str) -> List[str]:
    return [re.sub(r"[^a-z0-9]+", "", w) for w in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(w) >= 2]


def _matches_query(name: str, tokens: Sequence[str]) -> bool:
    if not tokens:
        return True
    haystack = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return all(token in haystack for token in tokens)


def search_all(query: str, source_ids: Sequence[str], limit: int, ctx: Ctx) -> Dict[str, Any]:
    """Search several sites at once; one broken source never kills the rest."""
    selected = [SOURCE_MAP[sid] for sid in source_ids if sid in SOURCE_MAP] or SOURCES
    cap = min(max(limit, 5), 60)
    tokens = _tokens(query)

    def run(source: Source) -> Any:
        try:
            rows = source.fn(query, cap, ctx, source.mirrors)
            if source.strict:
                kept = [row for row in rows if _matches_query(row.name, tokens)]
                if not kept:
                    raise SourceError(
                        "عبارت جست‌وجو اعمال نشد و هیچ نتیجه‌ای مطابق آن نبود "
                        "(این آینه فقط صفحه اول را سرو می‌کند)"
                    )
                rows = kept
            return {"id": source.id, "label": source.label, "ok": True, "count": len(rows),
                    "error": ""}, rows
        except SourceError as exc:
            return {"id": source.id, "label": source.label, "ok": False, "count": 0,
                    "error": str(exc)}, []
        except Exception as exc:  # noqa: BLE001 - keep the other sources alive
            return {"id": source.id, "label": source.label, "ok": False, "count": 0,
                    "error": f"خطای غیرمنتظره: {exc}"}, []

    rows: List[Result] = []
    status: List[Dict[str, Any]] = []
    # ponytail: sources past the budget are abandoned (threads keep running until
    # curl's --max-time); the caller must exit hard (os._exit) after printing.
    pool = ThreadPoolExecutor(max_workers=max(1, min(8, len(selected))))
    futures = [(pool.submit(run, source), source) for source in selected]
    done, _ = wait([f for f, _ in futures], timeout=SEARCH_BUDGET)
    for future, source in futures:
        if future in done:
            status_row, source_rows = future.result()
        else:
            status_row, source_rows = {
                "id": source.id, "label": source.label, "ok": False, "count": 0,
                "error": f"تایم‌اوت — بیش از {int(SEARCH_BUDGET)} ثانیه جواب نداد",
            }, []
        status.append(status_row)
        rows.extend(source_rows)
    pool.shutdown(wait=False, cancel_futures=True)

    def sort_key(item: Result) -> Any:
        return (-(item.seeds or 0), -(item.size_bytes or 0))

    return {
        "query": query,
        "sources": status,
        "results": [row.as_dict() for row in sorted(rows, key=sort_key)[:400]],
    }


def probe_all(ctx: Ctx) -> List[Dict[str, Any]]:
    """Is this site reachable from *this* network right now?"""
    def check(source: Source) -> Dict[str, Any]:
        last = ""
        for base in source.mirrors[:3]:
            try:
                fetch(base + source.probe, proxy=ctx.proxy, timeout=min(ctx.timeout, 10))
                return {"id": source.id, "label": source.label, "ok": True, "url": base, "error": ""}
            except SourceError as exc:
                last = f"{base}: {exc}"
            except Exception as exc:  # noqa: BLE001
                last = f"{base}: {exc}"
        return {"id": source.id, "label": source.label, "ok": False, "url": "", "error": last}

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(SOURCES)))) as pool:
        return list(pool.map(check, SOURCES))



