"""Tests for ContextManager. No Ollama needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context import ContextManager, estimate_tokens  # noqa: E402


def _ctx(budget=2000):
    return ContextManager(system_prompt="sys", max_input_tokens=budget)


def test_token_estimation_scales():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 0


def test_budget_enforced_discards_old_first():
    c = _ctx(budget=300)
    c.add_user("current task: fix bug")
    for i in range(20):
        c.messages.append({"role": "assistant", "content": f"[tool_call] read_file {{'path': 'f{i}.py'}}"})
        c.messages.append({"role": "user", "content": f"[tool_result: read_file]\n{'x' * 400} #{i}"})
    c.enforce_budget()
    assert c.estimated_tokens() <= 300, c.estimated_tokens()
    # current task preserved
    assert any("current task" in m.get("content", "") for m in c.messages)
    assert c.pruned_count > 0


def test_old_successful_commands_dropped_first():
    c = _ctx(budget=400)
    c.add_user("task")
    c.messages.append({"role": "user", "content": "[tool_result: run_command]\nCOMMAND:\nls\nEXIT CODE:\n0\n" + "y" * 1200})
    # several newer turns push the old success outside the protected window
    for i in range(3):
        c.messages.append({"role": "assistant", "content": f"[tool_call] read_file {{'path': 'n{i}.py'}}"})
        c.messages.append({"role": "user", "content": f"[tool_result: read_file]\n{i} | recent"})
    assert c.estimated_tokens() > 400  # precondition: actually over budget
    c.enforce_budget()
    assert c.estimated_tokens() <= 400, c.estimated_tokens()
    texts = [m.get("content", "") for m in c.messages]
    assert not any("COMMAND:\nls" in t for t in texts), texts
    # recent turns survive
    assert any("recent" in t for t in texts)


def test_compression_keeps_head_and_tail():
    big = "A" * 5000 + "MID" + "B" * 5000
    out = ContextManager.compress_tool_result("run_command", big, limit=1000)
    assert "TRUNCATED" in out
    assert out.startswith("A" * 100)
    assert out.endswith("B" * 100)


def test_repeat_detection():
    c = _ctx()
    c.messages.append({"role": "assistant", "content": "[executed: read_file {'path': 'a.py'}]"})
    c.messages.append({"role": "user", "content": "[tool_result: read_file]\n1 | x"})
    c.messages.append({"role": "assistant", "content": "[executed: read_file {'path': 'a.py'}]"})
    c.messages.append({"role": "user", "content": "[tool_result: read_file]\n1 | x"})
    assert c.is_repeating("read_file", {"path": "a.py"})
    assert not c.is_repeating("read_file", {"path": "b.py"})
