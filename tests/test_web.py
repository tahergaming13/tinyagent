"""Tests for web_search. All network access is mocked."""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.web import _real_url, web_search  # noqa: E402
import tools.web as webmod  # noqa: E402


DDG_PAGE = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">First &amp; Result</a>
<a class="result__snippet" href="x">Snippet <b>one</b> here.</a>
<a class="result__a" href="https://example.com/b">Second</a>
<a class="result__snippet" href="x">Snippet two.</a>
</body></html>
"""

WIKI_JSON = json.dumps(["FastAPI", ["FastAPI", "FastAPI tutorial"],
                        ["", ""], ["https://en.wikipedia.org/wiki/FastAPI",
                                   "https://en.wikipedia.org/wiki/T"]])


class FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _patch(monkeypatch, script):
    """script: ordered list of bytes (response) or Exception per urlopen call."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        item = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)

    monkeypatch.setattr(webmod.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_ddg_parses_and_unwraps_redirect(monkeypatch):
    calls = _patch(monkeypatch, [DDG_PAGE.encode()])
    out = web_search("test query")
    assert out.startswith("WEB SEARCH: test query"), out
    assert "DuckDuckGo unreachable" not in out
    assert "1. First & Result" in out, out
    assert "https://example.com/a" in out, out  # uddg unwrapped
    assert "Snippet one here." in out, out  # tags stripped
    assert calls["n"] == 1  # fallback never hit


def test_wikipedia_fallback_when_ddg_blocked(monkeypatch):
    calls = _patch(monkeypatch, [urllib.error.URLError("reset by peer"),
                                 WIKI_JSON.encode()])
    out = web_search("FastAPI")
    assert "Wikipedia matches instead" in out, out
    assert "https://en.wikipedia.org/wiki/FastAPI" in out, out
    assert calls["n"] == 2


def test_empty_query_rejected():
    assert web_search("").startswith("ERROR")
    assert web_search("   ").startswith("ERROR")


def test_no_results_when_all_providers_fail(monkeypatch):
    _patch(monkeypatch, [b"<html><body>nothing</body></html>",
                         urllib.error.URLError("down")])
    out = web_search("zzz unlikely query")
    assert "(no results" in out, out


def test_total_failure_returns_error_not_raise(monkeypatch):
    _patch(monkeypatch, [urllib.error.URLError("no route")])
    out = web_search("hello")
    assert "(no results" in out, out  # fallback also fails on same mock
    assert "offline" in out


def test_rate_limit_message(monkeypatch):
    err = urllib.error.HTTPError("url", 429, "Too Many", {}, None)
    calls = _patch(monkeypatch, [err])
    assert "rate-limited" in web_search("hello")
    assert calls["n"] == 1  # no fallback hammering on 429


def test_output_capped(monkeypatch):
    big_snip = "z" * 5000
    page = (f'<a class="result__a" href="https://e.com">T</a>'
            f'<a class="result__snippet" href="x">{big_snip}</a>')
    _patch(monkeypatch, [page.encode()])
    out = web_search("x")
    assert len(out) <= 2600, len(out)


def test_real_url_passthrough():
    assert _real_url("https://example.com/x") == "https://example.com/x"
    assert _real_url("//example.com/x") == "https://example.com/x"


def test_registered_in_tool_registry(monkeypatch, tmp_path):
    from config import Config
    from tools import TOOLS, execute_tool, tools_brief
    assert "web_search" in TOOLS
    assert "web_search" in tools_brief()
    _patch(monkeypatch, [DDG_PAGE.encode()])
    cfg = Config(workspace=str(tmp_path))
    out = execute_tool("web_search", {"query": "hi"}, str(tmp_path), cfg)
    assert out.startswith("WEB SEARCH: hi"), out
    assert execute_tool("web_search", {}, str(tmp_path), cfg).startswith("ERROR")
