"""Tests for clipboard helpers. No Ollama needed."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipboard import (assistant_answers, copy_to_clipboard, describe_copy,  # noqa: E402
                       format_transcript)


def _msgs():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "[executed: read_file {...}]"},
        {"role": "user", "content": "[tool_result: read_file]\n1 | x"},
        {"role": "assistant", "content": "First answer."},
        {"role": "assistant", "content": "Second answer."},
    ]


def test_assistant_answers_skips_markers():
    assert assistant_answers(_msgs()) == ["First answer.", "Second answer."]
    assert assistant_answers([]) == []


def test_format_transcript_skips_system():
    out = format_transcript(_msgs())
    assert "## user" in out and "First answer." in out
    assert "sys" not in out


def _stub_pyperclip(monkeypatch, fail=False):
    seen = {}
    mod = types.ModuleType("pyperclip")

    def fake_copy(text):
        if fail:
            raise RuntimeError("no xclip")
        seen["text"] = text

    mod.copy = fake_copy
    monkeypatch.setitem(sys.modules, "pyperclip", mod)
    return seen


def test_describe_copy_last_nth_all(monkeypatch):
    seen = _stub_pyperclip(monkeypatch)
    ok, msg = describe_copy(_msgs(), "")
    assert ok and "answer #2" in msg and seen["text"] == "Second answer."
    ok, msg = describe_copy(_msgs(), "1")
    assert ok and seen["text"] == "Second answer."
    ok, msg = describe_copy(_msgs(), "2")
    assert ok and seen["text"] == "First answer."
    ok, msg = describe_copy(_msgs(), "all")
    assert ok and "transcript" in msg and "Second answer." in seen["text"]


def test_describe_copy_errors(monkeypatch):
    ok, msg = describe_copy([], "")
    assert not ok and "Nothing to copy" in msg
    ok, msg = describe_copy(_msgs(), "9")
    assert not ok and "Nothing to copy" in msg
    ok, msg = describe_copy(_msgs(), "abc")
    assert not ok and "Usage" in msg
    _stub_pyperclip(monkeypatch, fail=True)
    ok, msg = describe_copy(_msgs(), "")
    assert not ok and "xclip" in msg


def test_copy_empty_rejected(monkeypatch):
    _stub_pyperclip(monkeypatch)
    try:
        copy_to_clipboard("   ")
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "Nothing to copy" in str(e)
