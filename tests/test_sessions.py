"""Tests for sessions.py + snapshot/apply_session. No Ollama needed."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import sessions as S  # noqa: E402
from agent import Agent  # noqa: E402
from config import Config  # noqa: E402
from main import apply_session, snapshot  # noqa: E402


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv(S.HOME_ENV, str(tmp_path))
    return tmp_path


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    payload = {"messages": [{"role": "user", "content": "hi"}],
               "tool_calls": 2}
    p = S.save_session("work", payload)
    assert p.endswith("work.json")
    data = S.load_session("work")
    assert data["messages"] == payload["messages"]
    assert data["tool_calls"] == 2
    assert "updated" in data


def test_load_missing_and_corrupt(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        S.load_session("nope")
    bad = os.path.join(S.sessions_dir(), "bad.json")
    with open(bad, "w") as f:
        f.write("{not json")
    with pytest.raises(ValueError):
        S.load_session("bad")
    with open(bad, "w") as f:
        f.write('{"messages": "oops"}')
    with pytest.raises(ValueError):
        S.load_session("bad")


def test_list_and_rename(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert S.list_sessions() == []
    S.save_session("a", {"messages": []})
    S.save_session("b", {"messages": [{"role": "user", "content": "x"}],
                         "tool_calls": 1})
    names = [s["name"] for s in S.list_sessions()]
    assert names == ["b", "a"] or set(names) == {"a", "b"}
    S.rename_session("a", "c")
    assert {s["name"] for s in S.list_sessions()} == {"b", "c"}
    with pytest.raises(ValueError):
        S.rename_session("b", "c")  # clash
    with pytest.raises(FileNotFoundError):
        S.rename_session("ghost", "x")


def test_export_markdown_skips_system(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "done"}]
    p = S.export_markdown("notes", msgs)
    assert p.endswith("notes.md")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    assert "# Session: notes" in text
    assert "## user" in text and "do it" in text
    assert "sys" not in text


def test_snapshot_apply_roundtrip(tmp_path):
    cfg = Config(workspace=str(tmp_path))
    agent = Agent(None, cfg)
    agent.ctx.add_user("task one")
    agent.ctx.add_assistant("working")
    snap = snapshot(agent, cfg, "s1")
    assert snap["messages"][0]["role"] == "system"

    agent2 = Agent(None, cfg)
    # Simulate a newer tool list: system prompt must be rebuilt, not restored.
    snap["messages"][0] = {"role": "system", "content": "STALE SYSTEM"}
    apply_session(agent2, snap)
    assert agent2.ctx.messages[0]["content"] == agent2.ctx.system_prompt
    assert any("task one" in m.get("content", "")
               for m in agent2.ctx.messages)


def test_unsafe_names_rejected(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        S.save_session("...", {"messages": []})
