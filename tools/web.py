"""Web search tool (stdlib only, no API key).

Strategy: DuckDuckGo HTML scrape first (best results where reachable),
Wikipedia OpenSearch fallback (bot-friendly, works almost everywhere).
Both are capped hard — search output is the fastest way to blow an 8K window.

Upgrade path: add a key-based provider (Tavily/Brave) as another
_search_* function and try it first when its env key is present.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TIMEOUT = 15
MAX_RESULTS = 5
MAX_CHARS = 2500  # hard cap on the formatted result


class _RateLimited(Exception):
    pass


_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIP_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def _clean(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _cap(out: str) -> str:
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS] + "\n... [SEARCH OUTPUT CAPPED]"
    return out


def _real_url(href: str) -> str:
    """Unwrap DDG's //duckduckgo.com/l/?uddg=<target> redirects."""
    href = html.unescape(href)
    if "uddg=" in href:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if params.get("uddg"):
            return params["uddg"][0]
    if href.startswith("//"):
        return "https:" + href
    return href


def _ddg_html(query: str, max_results: int) -> str:
    """Return formatted DDG results, '' if blocked/empty. Raises _RateLimited."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers=UA, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            page = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise _RateLimited from e
        return ""  # treat other HTTP failures as "try the fallback"
    except Exception:
        return ""  # offline / DNS / reset by peer -> fallback decides
    links = _LINK_RE.findall(page)
    if not links:  # bot challenge page, layout change, or no results
        return ""
    snips = _SNIP_RE.findall(page)
    lines = [f"WEB SEARCH: {query}"]
    for i, (href, title) in enumerate(links[:max_results]):
        snip = _clean(snips[i]) if i < len(snips) else ""
        lines.append(f"\n{i + 1}. {_clean(title)}")
        lines.append(f"   {_real_url(href)}")
        if snip:
            lines.append(f"   {snip[:300]}")
    return "\n".join(lines)


def _wikipedia(query: str, max_results: int) -> str:
    """Return formatted Wikipedia matches, '' on any failure."""
    url = ("https://en.wikipedia.org/w/api.php?action=opensearch&search="
           + urllib.parse.quote_plus(query)
           + f"&limit={max_results}&format=json")
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        items = list(zip(data[1], data[3]))[:max_results]
    except Exception:
        return ""
    if not items:
        return ""
    lines = ["[DuckDuckGo unreachable; showing Wikipedia matches instead.]",
             f"WEB SEARCH: {query}"]
    for i, (title, link) in enumerate(items):
        lines.append(f"\n{i + 1}. {title}")
        lines.append(f"   {link}")
    return "\n".join(lines)


def web_search(query: str, max_results: int = MAX_RESULTS) -> str:
    """Search the web. Returns capped title/url/snippet list, never raises."""
    query = (query or "").strip()
    if not query:
        return "ERROR: web_search needs a non-empty {query}."
    try:
        max_results = max(1, min(int(max_results), 10))
    except (TypeError, ValueError):
        max_results = MAX_RESULTS
    try:
        ddg = _ddg_html(query, max_results)
    except _RateLimited:
        return ("ERROR: DuckDuckGo rate-limited the request (HTTP 429). "
                "Wait a minute and try one narrower query.")
    if ddg:
        return _cap(ddg)
    wiki = _wikipedia(query, max_results)
    if wiki:
        return _cap(wiki)
    return (f"WEB SEARCH: {query}\n"
            "(no results — search providers unreachable from here; "
            "everything else works offline.)")
