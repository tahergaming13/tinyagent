"""Tests for edit_file, undo, grep_files, glob_files, web_fetch."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import filesystem as fs  # noqa: E402
from tools.filesystem import edit_file, read_file, undo_last_write, write_file  # noqa: E402
from tools.search import glob_files, grep_files  # noqa: E402
from tools.web import web_fetch  # noqa: E402
import tools.web as webmod  # noqa: E402


def _ws():
    return tempfile.mkdtemp()


def setup_function(_):
    fs._UNDO.clear()


def test_edit_replaces_once():
    ws = _ws()
    write_file("a.py", "x = 1\nprint(x)\n", workspace=ws)
    fs._UNDO.clear()
    r = edit_file("a.py", "x = 1", "x = 2", workspace=ws)
    assert r.startswith("OK"), r
    assert "line ~1" in r
    assert "x = 2" in read_file("a.py", workspace=ws)


def test_edit_missing_and_ambiguous():
    ws = _ws()
    write_file("a.py", "foo\nfoo\n", workspace=ws)
    assert edit_file("a.py", "zzz", "y", workspace=ws).startswith("ERROR")
    r = edit_file("a.py", "foo", "y", workspace=ws)
    assert r.startswith("ERROR") and "2 times" in r
    assert edit_file("nope.py", "a", "b", workspace=ws).startswith("ERROR")
    assert edit_file("../../evil", "a", "b", workspace=ws).startswith("ERROR")


def test_undo_restores_write_and_edit():
    ws = _ws()
    write_file("a.txt", "v1", workspace=ws)
    fs._UNDO.clear()
    write_file("a.txt", "v2", workspace=ws)
    assert "v2" in read_file("a.txt", workspace=ws)
    assert undo_last_write().startswith("Undone: restored")
    assert "v1" in read_file("a.txt", workspace=ws)


def test_undo_created_file_removes_it():
    ws = _ws()
    fs._UNDO.clear()
    write_file("new.txt", "hi", workspace=ws)
    assert undo_last_write().startswith("Undone: removed")
    assert not os.path.exists(os.path.join(ws, "new.txt"))
    assert undo_last_write() == "Nothing to undo."


def test_edit_then_undo():
    ws = _ws()
    write_file("a.py", "x = 1\n", workspace=ws)
    fs._UNDO.clear()
    edit_file("a.py", "x = 1", "x = 9", workspace=ws)
    undo_last_write()
    assert "x = 1" in read_file("a.py", workspace=ws)


def test_grep_finds_and_skips_junk():
    ws = _ws()
    write_file("app/main.py", "TODO fix this\nx = 1\n", workspace=ws)
    write_file("node_modules/dep.js", "TODO hidden\n", workspace=ws)
    out = grep_files("TODO", workspace=ws)
    assert "app/main.py:1:" in out, out
    assert "node_modules" not in out
    assert grep_files("zzz-no-match", workspace=ws).endswith("no matches.")
    assert grep_files("([bad", workspace=ws).startswith("ERROR")


def test_grep_include_filter_and_cap():
    ws = _ws()
    write_file("a.py", "needle\n", workspace=ws)
    write_file("b.txt", "needle\n", workspace=ws)
    out = grep_files("needle", workspace=ws, include="*.py")
    assert "a.py" in out and "b.txt" not in out, out


def test_glob_finds_py_files():
    ws = _ws()
    write_file("app/main.py", "x", workspace=ws)
    write_file("tests/test_x.py", "x", workspace=ws)
    write_file(".git/config", "x", workspace=ws)
    out = glob_files("**/*.py", workspace=ws)
    assert "app/main.py" in out and "tests/test_x.py" in out, out
    assert ".git" not in out


class FakeResp:
    def __init__(self, body: bytes, ctype="text/html"):
        self._body = body
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_web_fetch_strips_tags(monkeypatch):
    def fake(req, timeout=None):
        return FakeResp(b"<html><head><title>T</title></head><body>"
                        b"<script>evil()</script><h1>Hello</h1><p>World</p></body></html>")
    monkeypatch.setattr(webmod.urllib.request, "urlopen", fake)
    out = web_fetch("https://example.com")
    assert out.startswith("FETCH: https://example.com — T"), out
    assert "Hello" in out and "evil()" not in out, out


def test_web_fetch_rejects_bad_url_and_binary(monkeypatch):
    assert web_fetch("notaurl").startswith("ERROR")
    monkeypatch.setattr(webmod.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(b"x", "application/pdf"))
    assert web_fetch("https://e.com/f.pdf").startswith("ERROR")
