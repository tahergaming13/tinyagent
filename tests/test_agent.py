"""Tests for the agent loop with a mocked Ollama client. No Ollama needed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import Agent, parse_tool_calls  # noqa: E402
from config import Config  # noqa: E402


class FakeClient:
    """Replays scripted replies, records messages it was given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen_messages = None

    def chat(self, messages, stream=False, on_token=None):
        self.seen_messages = list(messages)
        text = self.replies.pop(0)
        if on_token:
            on_token(text)
        return text


def _cfg(**kw):
    ws = tempfile.mkdtemp()
    c = Config(workspace=ws, **kw)
    return c


def test_parse_tool_call_fence():
    t = 'Let me look.\n```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```'
    calls = parse_tool_calls(t)
    assert calls == [{"name": "read_file", "arguments": {"path": "a.py"}}]


def test_parse_ignores_plain_text():
    assert parse_tool_calls("Hello, I can help with that.") == []


def test_parse_bare_tool_call_line():
    # Small models sometimes leak the call as plain text instead of a fence.
    t = "The bug is in add.\n[tool_call] read_file {'path': 'calc.py', 'start_line': 1}"
    calls = parse_tool_calls(t)
    assert calls == [{"name": "read_file",
                      "arguments": {"path": "calc.py", "start_line": 1}}]


def test_parse_single_quoted_fence():
    t = "```tool_call\n{'name': 'list_files', 'arguments': {'path': '.'}}\n```"
    assert parse_tool_calls(t) == [{"name": "list_files", "arguments": {"path": "."}}]


def test_parse_echoed_history_marker_only_when_standalone():
    lone = "[executed: run_command {'command': 'python calc.py'}]"
    assert parse_tool_calls(lone) == [
        {"name": "run_command", "arguments": {"command": "python calc.py"}}]
    # Embedded in prose -> NOT a call (it's just a summary).
    prose = "I already ran [executed: run_command {'command': 'ls'}] and it worked."
    assert parse_tool_calls(prose) == []


def test_parse_rejects_unknown_tool():
    assert parse_tool_calls('```tool_call\n{"name": "nuke", "arguments": {}}\n```') == []


def test_full_loop_tool_then_answer():
    ws = tempfile.mkdtemp()
    with open(os.path.join(ws, "a.py"), "w") as f:
        f.write("print(1)\n")
    cfg = Config(workspace=ws)
    fake = FakeClient([
        '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        "The file prints 1. Done.",
    ])
    agent = Agent(fake, cfg)
    tools_seen = []
    res = agent.ask("read a.py", on_tool=lambda n, a: tools_seen.append(n))
    assert res.stopped == "answer", res
    assert tools_seen == ["read_file"]
    assert "prints 1" in res.answer
    # tool result made it into context
    assert any("print(1)" in m.get("content", "") for m in agent.ctx.messages)


def test_empty_response_surfaced_not_silent():
    cfg = _cfg()
    fake = FakeClient(["   "])
    agent = Agent(fake, cfg)
    res = agent.ask("do something")
    assert res.stopped == "error", res
    assert "empty response" in res.answer


def test_max_iterations_stops_gracefully():
    cfg = _cfg(max_tool_calls=2)
    fake = FakeClient([
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
    ])
    agent = Agent(fake, cfg)
    res = agent.ask("list stuff")
    assert res.stopped in ("max_iterations", "repeat"), res


def test_invalid_tool_call_returned_as_error_to_model():
    cfg = _cfg()
    seen = {}

    class SpyClient(FakeClient):
        def chat(self, messages, stream=False, on_token=None):
            seen["n"] = len(messages)
            return super().chat(messages, stream=stream, on_token=on_token)

    fake = SpyClient([
        '```tool_call\n{"name": "read_file", "arguments": {"path": "missing.py"}}\n```',
        "File does not exist, noted.",
    ])
    agent = Agent(fake, cfg)
    res = agent.ask("read missing.py")
    assert res.stopped == "answer"
    assert any("not found" in m.get("content", "") for m in agent.ctx.messages)


def test_repeat_loop_broken():
    cfg = _cfg()
    fake = FakeClient([
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
        '```tool_call\n{"name": "list_files", "arguments": {"path": "."}}\n```',
        "I see a loop, stopping here.",
    ])
    agent = Agent(fake, cfg)
    res = agent.ask("list")
    assert res.stopped == "answer", res
    # The repeat warning must have been injected instead of a 3rd execution.
    assert any("already issued this exact call" in m.get("content", "")
               for m in agent.ctx.messages)
