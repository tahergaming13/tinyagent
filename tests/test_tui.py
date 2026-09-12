"""Pilot (headless) tests for the Textual TUI. No Ollama needed."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import Input, RichLog  # noqa: E402

from agent import Agent  # noqa: E402
from config import Config  # noqa: E402
from tools import filesystem as fs  # noqa: E402
from tui import TuiApp  # noqa: E402


class FakeClient:
    model = "fake-model"
    host = "http://localhost:11434"

    def __init__(self, replies=None, models=None):
        self.replies = list(replies or [])
        self._models = models if models is not None else ["fake-model"]

    def check_connection(self):
        return True, "Fake connected."

    def list_models(self):
        return list(self._models)

    def chat(self, messages, stream=False, on_token=None):
        text = self.replies.pop(0) if self.replies else "canned answer"
        if on_token:
            on_token(text)
        return text


def _app(replies=None, models=None, monkeypatch=None):
    if monkeypatch is not None:
        # Hermetic: ignore the developer's real .env file.
        monkeypatch.setenv("OLLAMA_MODEL", "fake-model")
    cfg = Config(workspace=tempfile.mkdtemp())
    client = FakeClient(replies=replies, models=models)
    return TuiApp(Agent(client, cfg), client, cfg)


def _text(app: TuiApp) -> str:
    return "\n".join(app.mirror)


def setup_function(_):
    fs._UNDO.clear()  # undo stack is process-global; isolate tests


async def test_mounts_with_banner_and_help(monkeypatch):
    app = _app(monkeypatch=monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "tinyagent" in app.top_text
        assert "fake-model" in app.top_text
        assert "welcome" in _text(app)


async def test_slash_dropdown_filters():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#cmd-input", Input)
        inp.value = "/mod"
        await pilot.pause()
        lv = app.query_one("#suggest")
        assert not lv.has_class("hidden")
        assert app._matches == ["/model"]
        inp.value = "/"
        await pilot.pause()
        assert len(app._matches) == 15


async def test_tools_command_prints():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/tools", "enter")
        await pilot.pause()
        assert "read_file" in _text(app) and "web_fetch" in _text(app)


async def test_unknown_command_hint():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/zzz", "enter")
        await pilot.pause()
        assert "Unknown command" in _text(app)


async def test_task_streams_answer():
    app = _app(replies=["CANNED-REPLY-42"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"hello", "enter")
        for _ in range(200):
            await asyncio.sleep(0.05)
            if "CANNED-REPLY-42" in _text(app):
                break
        assert "> hello" in _text(app)  # user input echoed
        assert "CANNED-REPLY-42" in _text(app)
        assert "ctx ~" in app.top_text  # header meter updated


async def test_model_modal_switches():
    app = _app(models=["fake-model", "other-model"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/model", "enter")
        await pilot.pause()
        from tui import PickModal
        assert isinstance(app.screen, PickModal)
        await pilot.press("down", "enter")  # highlight 2nd, select
        await pilot.pause()
        assert app.cfg.model == "other-model"
        assert "Switched to model: other-model" in _text(app)


async def test_partial_command_runs_single_match():
    from tui import PickModal
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/mod", "enter")
        await pilot.pause()
        # single match (/model) → model picker modal, not "Unknown command"
        assert isinstance(app.screen, PickModal)
        assert "Unknown command" not in _text(app)


async def test_slash_menu_dispatches_choice():
    from tui import PickModal
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/", "enter")
        await pilot.pause()
        assert isinstance(app.screen, PickModal)
        await pilot.press("enter")  # first item: /help
        for _ in range(100):
            await asyncio.sleep(0.05)
            if "pick a local model" in _text(app):
                break
        assert "pick a local model" in _text(app)  # /help output shown


async def test_partial_multi_match_offers_modal():
    from tui import PickModal
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/t", "enter")
        await pilot.pause()
        assert isinstance(app.screen, PickModal)  # /thinking//tools/…


async def test_undo_and_context_commands():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"/undo", "enter")
        await pilot.pause()
        assert "Nothing to undo." in _text(app)
        await pilot.press(*"/context", "enter")
        await pilot.pause()
        assert "Context:" in _text(app)
