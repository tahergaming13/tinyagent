"""Full-screen terminal UI for tinyagent (Textual).

Same agent, tools, sessions, and commands as the classic CLI — OpenCode-style
layout: top bar, live transcript, `/` autocomplete dropdown, modal pickers,
status line. Launch with `tinyagent --tui`.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.panel import Panel
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, RichLog, Static

from agent import Agent
from config import Config
from main import (COMMANDS, INIT_TASK, apply_session, expand_mentions,
                  snapshot)
from ollama import OllamaClient, OllamaError
from sessions import (export_markdown, list_sessions, load_session,
                      rename_session, save_session)
from tools import TOOLS
from tools.filesystem import undo_last_write

RESULT_PREFIXES = ("OK:", "ERROR", "BLOCKED", "Nothing", "Undone")


class PickModal(ModalScreen):
    """Numbered list picker. Dismisses with the chosen string or None."""

    CSS = """
    PickModal { align: center middle; }
    PickModal > Vertical { width: 64; height: auto; max-height: 22;
                           background: $surface; border: tall $primary;
                           padding: 1; }
    """

    def __init__(self, title: str, items: list[str]) -> None:
        super().__init__()
        self._title = title
        self._items = items

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._title),
            ListView(*[ListItem(Label(i)) for i in self._items],
                     id="pick-list"),
        )

    def on_mount(self) -> None:
        lv = self.query_one("#pick-list", ListView)
        if self._items:
            lv.index = 0
        lv.focus()

    def on_list_view_selected(self, msg: ListView.Selected) -> None:
        self.dismiss(self._items[msg.list_view.index])


class AskModal(ModalScreen):
    """Single-line text prompt. Dismisses with the text ('' on empty)."""

    CSS = """
    AskModal { align: center middle; }
    AskModal > Vertical { width: 64; height: auto;
                          background: $surface; border: tall $primary;
                          padding: 1; }
    """

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._title),
            Input(placeholder=self._placeholder, id="ask-input"),
        )

    def on_mount(self) -> None:
        self.query_one("#ask-input", Input).focus()

    def on_input_submitted(self, msg: Input.Submitted) -> None:
        self.dismiss(msg.value.strip())


class TuiApp(App):
    CSS = """
    Screen { background: $background; }
    #topbar { height: 1; background: $primary-darken-3; padding: 0 1; }
    #log { height: 1fr; padding: 0 1; scrollbar-size: 1 1; }
    #suggest { height: auto; max-height: 10; border: solid $accent;
               background: $surface; }
    #suggest.hidden { display: none; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #cmd-input { height: 3; border: solid $primary; background: $surface; }
    #cmd-input:focus { border: solid $accent; }
    #cmd-input:disabled { opacity: 0.6; }
    #hintbar { height: 1; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [("ctrl+q", "quit_app", "Quit")]

    def __init__(self, agent: Agent, client, cfg: Config) -> None:
        super().__init__()
        self.agent = agent
        self.client = client
        self.cfg = cfg
        self.session = {"name": "default"}
        self.show_thinking = False
        self._busy = False
        # acc: full turn text (fence detection). pending: unflushed,
        # fence-free text. RichLog gives each write() its own visual
        # line, so tokens are batched and only complete lines flushed.
        self._turn = {"acc": "", "pending": ""}
        self._matches: list[str] = []
        self.top_text = ""
        self.mirror: list[str] = []  # plain-text transcript (tests, export)

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        yield RichLog(id="log", highlight=False, markup=False)
        yield ListView(id="suggest", classes="hidden")
        yield Static("", id="status")
        yield Input(placeholder="Task, @file, or / for commands",
                    id="cmd-input")
        yield Static("/ menu · Enter send · ↑↓ scroll log · Ctrl+Q quit",
                     id="hintbar")

    def on_mount(self) -> None:
        self._refresh_top()
        self._status("connecting to Ollama…")
        self.query_one("#cmd-input", Input).focus()
        asyncio.create_task(self._connect())

    async def _connect(self) -> None:
        try:
            ok, msg = await asyncio.to_thread(self.client.check_connection)
        except Exception as e:  # noqa: BLE001 - show, don't crash
            ok, msg = False, str(e)
        self._refresh_top()
        self._status(msg if ok else msg + " — /help works offline")
        self._hero()

    def _hero(self) -> None:
        # No model/session lines here on purpose: those change at runtime
        # (/model, /rename, /resume) and the top bar is the live indicator.
        # A static hero never goes stale.
        body = Text()
        body.append("tinyagent", style="bold cyan")
        body.append(" — your local coding agent\n", style="dim")
        body.append(f"Space  {os.path.abspath(self.cfg.workspace)}\n")
        body.append("Type a task · @file attaches a file · / opens commands",
                    style="dim")
        self.query_one("#log", RichLog).write(
            Panel(body, title="welcome", border_style="cyan",
                  padding=(0, 1)))
        self.mirror.append("welcome")

    def action_quit_app(self) -> None:
        self.exit()

    # -- transcript helpers (call from UI thread; use call_from_thread) --
    def _w(self, content) -> None:
        text = content.plain if isinstance(content, Text) else str(content)
        self.mirror.append(text)
        try:
            self.query_one("#log", RichLog).write(content)
        except Exception:
            pass  # shutting down

    def _status(self, text: str) -> None:
        try:
            self.query_one("#status", Static).update(text)
        except Exception:
            pass

    def _refresh_top(self) -> None:
        s = self.agent.ctx.stats()
        ratio = s["tokens"] / max(1, self.cfg.context_size)
        color = "green" if ratio < 0.6 else ("yellow" if ratio < 0.85
                                            else "red")
        bar = Text()
        bar.append(" tinyagent ", style="bold black on cyan")
        bar.append(f"  {self.cfg.model}  ", style="bold")
        bar.append(f"{self.session['name']}  ", style="dim")
        bar.append(f"ctx ~{s['tokens']:,}/{self.cfg.context_size:,}",
                   style=color)
        self.top_text = bar.plain
        self._status_bar(bar)

    def _status_bar(self, text: str) -> None:
        try:
            self.query_one("#topbar", Static).update(text)
        except Exception:
            pass

    # -- autocomplete dropdown ------------------------------------------
    def on_input_changed(self, msg: Input.Changed) -> None:
        if msg.input.id != "cmd-input":
            return
        value = msg.value
        lv = self.query_one("#suggest", ListView)
        if not value.startswith("/") or " " in value:
            lv.add_class("hidden")
            return
        frag = value
        matches = [(n, d) for n, d in COMMANDS if n.startswith(frag)]
        lv.clear()
        for n, d in matches:
            lv.append(ListItem(Label(f"{n}  — {d}")))
        self._matches = [n for n, _ in matches]
        lv.remove_class("hidden")

    async def on_key(self, event: events.Key) -> None:
        if event.key == "down" and self.focused \
                and getattr(self.focused, "id", "") == "cmd-input":
            lv = self.query_one("#suggest", ListView)
            if not lv.has_class("hidden"):
                lv.focus()
                event.prevent_default()

    def on_list_view_selected(self, msg: ListView.Selected) -> None:
        if msg.list_view.id != "suggest":
            return
        idx = msg.list_view.index
        if 0 <= idx < len(self._matches):
            inp = self.query_one("#cmd-input", Input)
            inp.value = self._matches[idx] + " "
            inp.focus()
        msg.list_view.add_class("hidden")

    # -- input -----------------------------------------------------------
    async def on_input_submitted(self, msg: Input.Submitted) -> None:
        if msg.input.id != "cmd-input":
            return
        line = msg.value.strip()
        msg.input.value = ""
        self.query_one("#suggest", ListView).add_class("hidden")
        if not line:
            return
        self._w(Text(f"> {line}", style="bold cyan"))
        if line.startswith("/"):
            await self._dispatch(line)
        else:
            self._submit_task(expand_mentions(line, self.cfg.workspace))

    # -- slash commands (same set as the classic CLI) --------------------
    async def _dispatch(self, line: str) -> None:
        cmd, _, rest = line.partition(" ")
        arg = rest.strip()
        if cmd == "/help":
            self._w("Commands (↑↓ in the / menu, Enter completes):")
            for n, d in COMMANDS:
                self._w(f"  {n:<10} {d}")
            self._w("")
        elif cmd == "/clear":
            self.agent.ctx.clear()
            self._w("Context cleared.")
        elif cmd == "/compact":
            await self._compact()
        elif cmd == "/init":
            self._submit_task(INIT_TASK)
        elif cmd == "/undo":
            self._w(undo_last_write())
        elif cmd == "/model":
            if arg:
                self._switch_model(arg)
            else:
                await self._model_modal()
        elif cmd == "/thinking":
            self._thinking(arg)
        elif cmd == "/sessions":
            items = list_sessions()
            self._w("(no saved sessions)" if not items else "Sessions:")
            for s in items:
                self._w(f"  {s['name']} — {s['messages']} msgs, "
                        f"{s['tool_calls']} tools, {s['updated']}")
        elif cmd == "/resume":
            if arg:
                self._resume(arg)
            else:
                names = [s["name"] for s in list_sessions()]
                if not names:
                    self._w("(no saved sessions)")
                else:
                    self.push_screen(PickModal("Resume session:",
                                               names), self._after_resume)
        elif cmd == "/export":
            self._export(arg or self.session["name"])
        elif cmd == "/rename":
            if arg:
                self._rename(arg)
            else:
                self.push_screen(AskModal("New session name:"),
                                 lambda v: v and self._rename(v))
        elif cmd == "/fork":
            if arg:
                self._fork(arg)
            else:
                self.push_screen(
                    AskModal("Fork as (Enter for default):",
                             self.session["name"] + "-fork"),
                    lambda v: self._fork(v or self.session["name"] + "-fork"))
        elif cmd == "/context":
            s = self.agent.ctx.stats()
            self._w(f"Model: {self.cfg.model}\n"
                    f"Context: {s['tokens']:,} / {self.cfg.context_size:,} "
                    f"(budget {self.cfg.max_input_tokens:,})\n"
                    f"Messages: {s['messages']}  Tool calls: {s['tool_calls']}  "
                    f"Pruned: {s['pruned']}")
        elif cmd == "/tools":
            self._w("Tools: " + ", ".join(TOOLS))
        elif cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/":
            self.push_screen(
                PickModal("Commands:",
                          [f"{n:<10} {d}" for n, d in COMMANDS]),
                self._after_command_pick)
        else:
            matches = [(n, d) for n, d in COMMANDS if n.startswith(cmd)]
            if len(matches) == 1:
                await self._dispatch(matches[0][0]
                                     + (f" {arg}" if arg else ""))
            elif matches:
                labels = [f"{n:<10} {d}" for n, d in matches]
                names = [n for n, _ in matches]
                suffix = f" {arg}" if arg else ""

                def picked(choice: str | None) -> None:
                    if choice:
                        asyncio.create_task(
                            self._dispatch(names[labels.index(choice)]
                                           + suffix))

                self.push_screen(PickModal("Did you mean:", labels), picked)
            else:
                self._w(f"Unknown command: {line}. Type / for the menu.")

    def _after_command_pick(self, choice: str | None) -> None:
        if choice:  # labels are "cmd  — desc"; recover the command token
            asyncio.create_task(self._dispatch(choice.split()[0]))

    # -- command implementations -----------------------------------------
    def _switch_model(self, name: str) -> None:
        self.cfg.model = self.client.model = name
        self._refresh_top()
        self._w(f"Switched to model: {name} "
                "(context kept — /clear for a fresh start)")

    async def _model_modal(self) -> None:
        try:
            models = await asyncio.to_thread(self.client.list_models)
        except OllamaError as e:
            self._w(Text(str(e), style="red"))
            return
        if not models:
            self._w("No local models. Pull one: ollama pull qwen3:8b")
            return
        labels = [m + ("  * current" if m == self.cfg.model else "")
                  for m in models]

        def picked(choice: str | None) -> None:
            if choice:
                self._switch_model(models[labels.index(choice)])

        self.push_screen(PickModal("Local models:", labels), picked)

    def _after_resume(self, choice: str | None) -> None:
        if choice:
            self._resume(choice)

    def _resume(self, name: str) -> None:
        try:
            data = load_session(name)
        except (FileNotFoundError, ValueError) as e:
            self._w(Text(str(e), style="red"))
            return
        apply_session(self.agent, data)
        self.session["name"] = data.get("name", name)
        s = self.agent.ctx.stats()
        self._refresh_top()
        self._w(f"Resumed '{self.session['name']}': {s['messages']} msgs, "
                f"~{s['tokens']:,} tokens.")

    def _export(self, target: str) -> None:
        try:
            if target.endswith(".md"):
                p = export_markdown(target[:-3], self.agent.ctx.messages)
            else:
                p = save_session(target,
                                 snapshot(self.agent, self.cfg, target))
            self._w(f"Exported to {p}")
        except (ValueError, OSError) as e:
            self._w(Text(f"Export failed: {e}", style="red"))

    def _rename(self, new: str) -> None:
        old = self.session["name"]
        try:
            rename_session(old, new)
        except FileNotFoundError:
            pass  # never saved — relabel only
        except ValueError as e:
            self._w(Text(str(e), style="red"))
            return
        self.session["name"] = new
        self._refresh_top()
        self._w(f"Session '{old}' → '{new}'.")

    def _fork(self, new: str) -> None:
        try:
            p = save_session(new, snapshot(self.agent, self.cfg, new))
        except (ValueError, OSError) as e:
            self._w(Text(f"Fork failed: {e}", style="red"))
            return
        self.session["name"] = new
        self._refresh_top()
        self._w(f"Forked into '{new}' ({p}). Continuing here.")

    def _thinking(self, arg: str) -> None:
        if arg.lower() in ("on", "1", "true"):
            self.show_thinking = True
            self._w("Thinking view on: raw model output incl. tool JSON.")
        elif arg.lower() in ("off", "0", "false"):
            self.show_thinking = False
            self._w("Thinking view off: clean → lines only.")
        else:
            state = "on" if self.show_thinking else "off"
            self._w(f"Thinking view is {state}. (/thinking on|off)")

    async def _compact(self) -> None:
        msgs = self.agent.ctx.messages[1:]
        if len(msgs) < 4:
            self._w("Nothing to compact yet.")
            return
        self._status("● compacting…")
        instruction = (
            "Summarize this coding session in under 400 words: current goal, "
            "files touched, key decisions, errors found, what remains. "
            "Terse bullets, no long code.")
        try:
            summary = await asyncio.to_thread(
                self.client.chat,
                msgs + [{"role": "user", "content": instruction}],
                False)
        except OllamaError as e:
            self._w(Text(f"Compact failed: {e}", style="red"))
            self._status("ready — / for commands")
            return
        self.agent.ctx.clear()
        self.agent.ctx.add_user(f"[session summary after /compact]\n{summary}")
        s = self.agent.ctx.stats()
        self._refresh_top()
        self._status("ready — / for commands")
        self._w(f"Compacted. Context: ~{s['tokens']:,} tokens.")

    # -- agent task (background thread, UI stays alive) -------------------
    def _submit_task(self, line: str) -> None:
        if self._busy:
            self._status("busy — wait for the current task (Ctrl+Q quits)")
            return
        self._busy = True
        self._turn = {"acc": "", "pending": ""}
        self.query_one("#cmd-input", Input).disabled = True
        self._status("● thinking… (Ctrl+Q quits)")
        asyncio.create_task(self._run_task(line))

    async def _run_task(self, line: str) -> None:
        tok = lambda t: self.call_from_thread(self._tok, t)  # noqa: E731
        tool = lambda n, a: self.call_from_thread(self._tool, n, a)  # noqa: E731
        res = lambda n, a, r: self.call_from_thread(self._res, n, a, r)  # noqa: E731
        try:
            result = await asyncio.to_thread(self.agent.ask, line,
                                             tok, tool, res)
        except Exception as e:  # noqa: BLE001 - never kill the UI
            result = None
            self._w(Text(f"Error: {e}", style="red"))
        # Back on the app thread here: direct widget calls again.
        if result is not None:
            if result.stopped == "answer":
                self._flush_turn()
                self._w("")
            else:
                self._w(result.answer)
        self._task_done()

    def _task_done(self) -> None:
        s = self.agent.ctx.stats()
        self._refresh_top()
        self._status(f"done · ctx ~{s['tokens']:,}/{self.cfg.context_size:,} "
                     "· / for commands")
        self._busy = False
        inp = self.query_one("#cmd-input", Input)
        inp.disabled = False
        inp.focus()

    # -- turn streaming (mirror of the classic CLI's suppression logic) --
    def _tok(self, tok: str) -> None:
        st = self._turn
        st["acc"] += tok
        if not self.show_thinking and "```" in st["acc"]:
            return  # hold back: might be an internal tool_call fence
        st["pending"] += tok
        self._flush_lines()

    def _flush_lines(self) -> None:
        """Write out complete lines (or long fragments) from the buffer."""
        st = self._turn
        pending = st["pending"]
        cut = pending.rfind("\n") + 1
        if not cut and len(pending) > 200:
            # No newline in sight: break at a word boundary so long
            # lines still stream instead of appearing all at once.
            sp = pending.rfind(" ", 0, 200)
            cut = (sp + 1) if sp > 0 else 200
        if cut:
            self._w(pending[:cut])
            st["pending"] = pending[cut:]

    def _flush_turn(self) -> None:
        st = self._turn
        if st["pending"]:
            self._w(st["pending"])
            st["pending"] = ""

    def _tool(self, name: str, args: dict) -> None:
        self._turn = {"acc": "", "pending": ""}
        brief = str(args)
        if len(brief) > 160:
            brief = brief[:160] + "..."
        self._w(Text(f"→ {name}({brief})", style="yellow"))

    def _res(self, name: str, args: dict, result: str) -> None:
        first = (result or "").splitlines()
        first = first[0] if first else ""
        if first.startswith(RESULT_PREFIXES):
            self._w(Text(f"  {first[:160]}", style="dim"))


def run_tui(model_override: str | None = None,
            workspace_override: str | None = None) -> None:
    cfg = Config()
    if model_override:
        cfg.model = model_override
    if workspace_override:
        cfg.workspace = workspace_override
    os.makedirs(cfg.workspace, exist_ok=True)
    client = OllamaClient(host=cfg.ollama_host, model=cfg.model,
                          temperature=cfg.temperature,
                          context_size=cfg.context_size)
    agent = Agent(client, cfg, debug=cfg.debug)
    TuiApp(agent, client, cfg).run()
