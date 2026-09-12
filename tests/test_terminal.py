"""Tests for run_command. No Ollama needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile  # noqa: E402

from tools.terminal import run_command  # noqa: E402


def test_success():
    ws = tempfile.mkdtemp()
    r = run_command("echo hello", workspace=ws)
    assert "EXIT CODE:\n0" in r, r
    assert "hello" in r


def test_failure_reports_exit_code_and_stderr():
    ws = tempfile.mkdtemp()
    r = run_command("python -c \"import sys; print('oops', file=sys.stderr); sys.exit(3)\"",
                    workspace=ws)
    assert "EXIT CODE:\n3" in r, r
    assert "oops" in r


def test_timeout():
    ws = tempfile.mkdtemp()
    r = run_command("python -c \"import time; time.sleep(30)\"", workspace=ws, timeout=2)
    assert "TIMEOUT" in r, r


def test_dangerous_blocked():
    ws = tempfile.mkdtemp()
    r = run_command("rm -rf /", workspace=ws)
    assert r.startswith("BLOCKED"), r
    r2 = run_command("git reset --hard", workspace=ws)
    assert r2.startswith("BLOCKED"), r2


def test_large_output_truncated():
    ws = tempfile.mkdtemp()
    r = run_command("python -c \"print('y'*50000)\"", workspace=ws, max_output=4000)
    assert "TRUNCATED" in r, r
    assert "EXIT CODE:\n0" in r
