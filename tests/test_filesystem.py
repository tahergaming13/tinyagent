"""Tests for filesystem tools. No Ollama needed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.filesystem import list_files, read_file, write_file  # noqa: E402


def _ws():
    d = tempfile.mkdtemp()
    return d


def test_write_and_read():
    ws = _ws()
    r = write_file("app/main.py", 'from fastapi import FastAPI\napp = FastAPI()\n', workspace=ws)
    assert r.startswith("OK"), r
    out = read_file("app/main.py", workspace=ws)
    assert "1 | from fastapi import FastAPI" in out, out
    assert "FILE: app/main.py" in out


def test_read_missing():
    ws = _ws()
    out = read_file("nope.py", workspace=ws)
    assert out.startswith("ERROR"), out


def test_path_traversal_blocked():
    ws = _ws()
    assert read_file("../../Windows/win.ini", workspace=ws).startswith("ERROR")
    assert write_file("../../evil.txt", "x", workspace=ws).startswith("ERROR")
    assert list_files("..", workspace=ws).startswith("ERROR")


def test_read_range():
    ws = _ws()
    write_file("big.py", "\n".join(f"line{i}" for i in range(1, 51)), workspace=ws)
    out = read_file("big.py", workspace=ws, start_line=10, end_line=12)
    assert "10 | line10" in out and "12 | line12" in out, out
    assert "9 | line9" not in out


def test_large_file_truncated_flag():
    ws = _ws()
    write_file("huge.txt", "x" * 5000, workspace=ws)
    out = read_file("huge.txt", workspace=ws, max_file_size=1000)
    assert "TRUNCATED" in out, out


def test_list_hides_junk_and_respects_depth():
    ws = _ws()
    write_file("app/a.py", "x", workspace=ws)
    write_file(".git/config", "x", workspace=ws)
    write_file("node_modules/dep/index.js", "x", workspace=ws)
    out = list_files(".", workspace=ws)
    assert "a.py" in out, out
    assert ".git" not in out and "node_modules" not in out, out
