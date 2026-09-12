"""Tests for list_models, command menu, and pickers. No Ollama needed."""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as M  # noqa: E402
import sessions as S  # noqa: E402
from ollama import OllamaClient, OllamaError  # noqa: E402
import ollama as ollamamod  # noqa: E402
from config import Config  # noqa: E402


class FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_list_models_parses_names(monkeypatch):
    body = json.dumps({"models": [{"name": "a:1"}, {"name": "b:2"}, {}]}).encode()
    monkeypatch.setattr(ollamamod.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(body))
    assert OllamaClient().list_models() == ["a:1", "b:2"]


def test_list_models_failure_raises(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(ollamamod.urllib.request, "urlopen", boom)
    try:
        OllamaClient().list_models()
        raise AssertionError("should have raised")
    except OllamaError as e:
        assert "ollama serve" in str(e)


class FakeClient:
    def __init__(self, models):
        self._models = models

    def list_models(self):
        return self._models


def _cfg():
    import tempfile
    return Config(workspace=tempfile.mkdtemp())


def _prompt_script(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(M, "prompt", lambda *a: next(it))


def test_slash_menu_picks_by_number(monkeypatch):
    _prompt_script(monkeypatch, ["2"])
    assert M.resolve_command("/", _cfg(), FakeClient([])) == "/clear"


def test_slash_menu_cancel(monkeypatch):
    _prompt_script(monkeypatch, [""])
    assert M.resolve_command("/", _cfg(), FakeClient([])) is None


def test_partial_command_expands(monkeypatch):
    _prompt_script(monkeypatch, ["1"])  # only /tools matches "/to"
    assert M.resolve_command("/to", _cfg(), FakeClient([])) == "/tools"


def test_partial_model_flows_into_picker(monkeypatch):
    _prompt_script(monkeypatch, ["1", "2"])  # pick /model, then 2nd model
    got = M.resolve_command("/mod", _cfg(), FakeClient(["a:1", "b:2"]))
    assert got == "/model b:2"


def test_unknown_command_reprompts(monkeypatch):
    assert M.resolve_command("/zzz", _cfg(), FakeClient([])) is None


def test_plain_task_passes_through(monkeypatch):
    assert M.resolve_command("fix it", _cfg(), FakeClient([])) == "fix it"
    assert M.resolve_command("/tools", _cfg(), FakeClient([])) == "/tools"


def test_model_picker_switches(monkeypatch):
    _prompt_script(monkeypatch, ["2"])
    got = M.resolve_command("/model", _cfg(),
                            FakeClient(["a:1", "b:2"]))
    assert got == "/model b:2"


def test_model_picker_empty_and_cancel(monkeypatch):
    assert M.resolve_command("/model", _cfg(), FakeClient([])) is None
    _prompt_script(monkeypatch, [""])
    assert M.resolve_command("/model", _cfg(), FakeClient(["a:1"])) is None


def test_resume_picker(monkeypatch, tmp_path):
    monkeypatch.setenv(S.HOME_ENV, str(tmp_path))
    assert M.resolve_command("/resume", _cfg(), FakeClient([])) is None
    S.save_session("demo", {"messages": []})
    _prompt_script(monkeypatch, ["1"])
    assert M.resolve_command("/resume", _cfg(), FakeClient([])) == "/resume demo"


def test_rename_prompts_for_name(monkeypatch):
    _prompt_script(monkeypatch, ["newname"])
    assert M.resolve_command("/rename", _cfg(), FakeClient([])) == "/rename newname"
    _prompt_script(monkeypatch, ["   "])
    assert M.resolve_command("/rename", _cfg(), FakeClient([])) is None
