"""Terminal interface for tinyagent (rich if present, plain fallback)."""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent  # noqa: E402
from clipboard import describe_copy  # noqa: E402
from config import Config  # noqa: E402
from ollama import OllamaClient, OllamaError  # noqa: E402
from tools import TOOLS  # noqa: E402
from tools.filesystem import read_file, undo_last_write  # noqa: E402
from sessions import (export_markdown, list_sessions, load_session,  # noqa: E402
                      rename_session, save_session)

try:
    from rich.console import Console
    from rich.markdown import Markdown
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None  # type: ignore


_GLYPH_FALLBACK = str.maketrans({
    "→": "->", "●": "*", "—": "-", "–": "-",
    "│": "|", "├": "|", "└": "\\", "─": "-",
    "✓": "ok", "✗": "x",
})


def out(text: str = "", style: str = "") -> None:
    if RICH:
        try:
            console.print(text, style=style, markup=False)
            return
        except UnicodeEncodeError:
            text = text.translate(_GLYPH_FALLBACK)  # piped output (cp1252)
    print(text)


def prompt(msg: str = "[bold cyan]> [/]") -> str:
    if RICH:
        try:
            return console.input(msg, markup=False)
        except UnicodeEncodeError:
            pass  # piped output on Windows (cp1252) — fall through
    return input(re.sub(r"\[.*?\]", "", msg))


def out_markdown(text: str) -> None:
    if RICH:
        console.print(Markdown(text))
    else:
        print(text)


COMMANDS = [
    ("/help", "show this help"),
    ("/clear", "clear conversation context"),
    ("/compact", "summarize session into a compact state"),
    ("/init", "explore workspace and write AGENTS.md"),
    ("/undo", "restore the last written/edited file"),
    ("/model", "pick a local model from a list (or /model <name>)"),
    ("/thinking", "toggle raw model output incl. tool JSON"),
    ("/sessions", "list saved sessions"),
    ("/resume", "pick a saved session to load"),
    ("/export", "save session as JSON (or .md transcript)"),
    ("/rename", "rename the current session"),
    ("/fork", "branch this session and continue in the copy"),
    ("/copy", "copy last answer to clipboard (N|all)"),
    ("/context", "show model / context usage"),
    ("/tools", "list available tools"),
    ("/quit", "exit"),
]

HELP = ("Commands (type / for a pickable menu):\n"
        + "\n".join(f"  {n:<10} {d}" for n, d in COMMANDS)
        + "\nType a task to run the agent. @path attaches a file.")


INIT_TASK = (
    "Explore the workspace: list files, read the README, config, and entry "
    "points. Then write AGENTS.md in the workspace root summarizing: what "
    "the project is, how to build/run/test it, key files, and conventions "
    "for future changes. Keep it under 60 lines."
)


def pick(title: str, items: list[str],
         notes: list[str] | None = None) -> str | None:
    """Numbered menu. Returns the chosen item, or None on cancel/empty."""
    out(title)
    for i, item in enumerate(items, 1):
        extra = f"  — {notes[i - 1]}" if notes else ""
        out(f"  {i}. {item}{extra}")
    try:
        sel = prompt("Choose [number, name, or Enter to cancel]: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if not sel:
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(items):
        return items[int(sel) - 1]
    if sel in items:
        return sel
    prefixed = [it for it in items if it.startswith(sel)]
    return prefixed[0] if len(prefixed) == 1 else None


def resolve_command(line: str, cfg: Config, client: OllamaClient) -> str | None:
    """Expand menu invocations into concrete commands. None = reprompt."""
    names = [n for n, _ in COMMANDS]
    if line == "/":
        choice = pick("Commands:", names, [d for _, d in COMMANDS])
        # Recurse so bare picks ("/model", "/resume", "/rename") hit
        # their pickers instead of falling through to the branches.
        return resolve_command(choice, cfg, client) if choice else None
    if line == "/model":
        try:
            models = client.list_models()
        except OllamaError as e:
            out(str(e), style="red")
            return None
        if not models:
            out("No local models. Pull one first: ollama pull qwen3:8b")
            return None
        labels = [m + ("  * current" if m == cfg.model else "") for m in models]
        choice = pick("Local models:", labels)
        if not choice:
            return None
        return "/model " + models[labels.index(choice)]
    if line == "/resume":
        session_names = [s["name"] for s in list_sessions()]
        if not session_names:
            out("(no saved sessions)")
            return None
        choice = pick("Sessions:", session_names)
        return f"/resume {choice}" if choice else None
    if line == "/rename":
        try:
            new = prompt("New session name: ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        return f"/rename {new}" if new else None
    if line.startswith("/"):
        first = line.split(None, 1)[0]
        if first not in set(names) | {"/exit", "/q"}:
            cands = [(n, d) for n, d in COMMANDS if n.startswith(first)]
            if not cands:
                out(f"Unknown command: {line}. Type / for the list.")
                return None
            choice = pick("Did you mean:",
                          [n for n, _ in cands], [d for _, d in cands])
            if not choice:
                return None
            return resolve_command(choice + line[len(first):], cfg, client)
    return line


def cmd_context(agent: Agent, cfg: Config) -> None:
    s = agent.ctx.stats()
    out(f"Model: {cfg.model}\n"
        f"Context: {s['tokens']:,} / {cfg.context_size:,} "
        f"(budget {cfg.max_input_tokens:,})\n"
        f"Messages: {s['messages']}  Tool calls: {s['tool_calls']}  "
        f"Pruned: {s['pruned']}")


def cmd_compact(agent: Agent) -> None:
    msgs = agent.ctx.messages[1:]  # skip system prompt
    if len(msgs) < 4:
        out("Nothing to compact yet.")
        return
    out("● Compacting...", style="dim")
    instruction = (
        "Summarize this coding session in under 400 words: current goal, "
        "files touched, key decisions, errors found, what remains. "
        "Terse bullets, no long code.")
    try:
        summary = agent.client.chat(
            msgs + [{"role": "user", "content": instruction}], stream=False)
    except OllamaError as e:
        out(f"Compact failed: {e}", style="red")
        return
    agent.ctx.clear()
    agent.ctx.add_user(f"[session summary after /compact]\n{summary}")
    s = agent.ctx.stats()
    out(f"Compacted. Context: ~{s['tokens']:,} tokens.")


MENTION_RE = re.compile(r"@([^\s`'\"]+)")


def expand_mentions(line: str, workspace: str) -> str:
    """Replace @path with the file's contents (capped). Unknown paths stay literal."""

    def sub(m: re.Match) -> str:
        content = read_file(m.group(1), workspace=workspace, max_file_size=8000)
        if content.startswith("ERROR"):
            return m.group(0)
        return f"\n[attached file {m.group(1)}]\n{content}\n"

    return MENTION_RE.sub(sub, line)


def snapshot(agent: Agent, cfg: Config, name: str) -> dict:
    return {"name": name, "model": cfg.model,
            "workspace": os.path.abspath(cfg.workspace),
            "messages": agent.ctx.messages,
            "tool_calls": agent.ctx.tool_calls,
            "pruned": agent.ctx.pruned_count}


def apply_session(agent: Agent, data: dict) -> None:
    """Load messages; rebuild the system prompt fresh (tool list may have grown)."""
    rest = [m for m in data["messages"] if m.get("role") != "system"]
    agent.ctx.messages = ([{"role": "system", "content": agent.ctx.system_prompt}]
                          + rest)
    agent.ctx.tool_calls = int(data.get("tool_calls", 0))
    agent.ctx.pruned_count = int(data.get("pruned", 0))


def run_task(agent: Agent, cfg: Config, line: str,
             show_thinking: bool = False) -> None:
    """Run one agent turn with live display; raw tool JSON stays internal."""
    state = {"acc": "", "printed": 0, "suppressed": False}

    def _print_live(s: str) -> None:
        if RICH:
            try:
                console.print(s, end="", markup=False, highlight=False)
                return
            except UnicodeEncodeError:
                s = s.translate(_GLYPH_FALLBACK)
                print(s, end="", flush=True)
                return
        print(s, end="", flush=True)

    def on_token(tok: str) -> None:
        state["acc"] += tok
        if not show_thinking and "```" in state["acc"]:
            state["suppressed"] = True
            return  # hold back: might be an internal tool_call fence
        _print_live(tok)
        state["printed"] += len(tok)

    def on_tool(name: str, args: dict) -> None:
        state.update(acc="", printed=0, suppressed=False)
        brief = str(args)
        if len(brief) > 160:
            brief = brief[:160] + "..."
        out(f"→ {name}({brief})", style="yellow")

    def on_result(name: str, args: dict, result: str) -> None:
        first = (result or "").splitlines()
        first = first[0] if first else ""
        if first.startswith(("OK:", "ERROR", "BLOCKED", "Nothing", "Undone")):
            out(f"  {first[:160]}", style="dim")

    out("● Thinking...", style="dim")
    result = agent.ask(line, on_token=on_token, on_tool=on_tool,
                       on_result=on_result)
    if result.stopped == "answer":
        rest = state["acc"][state["printed"]:]  # held-back fences, if any
        if rest:
            _print_live(rest)
        out("")
    else:
        out(result.answer, style="red" if result.stopped == "error" else "")
    s = agent.ctx.stats()
    out(f"— done. Context: ~{s['tokens']:,} / {cfg.context_size:,} tokens —",
        style="dim")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tinyagent",
        description="Minimal local Ollama coding agent.")
    p.add_argument("task", nargs="*", help="one-shot task (non-interactive)")
    p.add_argument("-m", "--model", help="override OLLAMA_MODEL for this run")
    p.add_argument("-w", "--workspace", help="override WORKSPACE for this run")
    p.add_argument("--tui", action="store_true",
                   help="launch the full-screen terminal UI")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.tui:
        from tui import run_tui
        run_tui(model_override=args.model,
                workspace_override=args.workspace)
        return
    cfg = Config()
    if args.model:
        cfg.model = args.model
    if args.workspace:
        cfg.workspace = args.workspace
    os.makedirs(cfg.workspace, exist_ok=True)
    client = OllamaClient(host=cfg.ollama_host, model=cfg.model,
                          temperature=cfg.temperature,
                          context_size=cfg.context_size)
    ok, msg = client.check_connection()
    session = {"name": "default", "thinking": False}
    out(f"tinyagent  |  Model: {cfg.model}  |  Context: {cfg.context_size}  "
        f"|  Workspace: {os.path.abspath(cfg.workspace)}  "
        f"|  Session: {session['name']}")
    out(msg, style="green" if ok else "red")
    if not ok:
        out("You can still explore /help, but tasks need Ollama running.")
    agent = Agent(client, cfg, debug=cfg.debug)

    if args.task:  # one-shot: tinyagent "fix the bug" -m model
        run_task(agent, cfg, " ".join(args.task))
        return

    out("Type /help for commands.\n")
    while True:
        try:
            try:
                line = prompt()
            except (KeyboardInterrupt, EOFError):
                out("\nBye.")
                break
        except (KeyboardInterrupt, EOFError):
            out("\nBye.")
            break
        line = line.strip()
        if not line:
            continue
        line = resolve_command(line, cfg, client)
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            out("Bye.")
            break
        if line == "/help":
            out(HELP)
            continue
        if line == "/clear":
            agent.ctx.clear()
            out("Context cleared.")
            continue
        if line == "/compact":
            cmd_compact(agent)
            continue
        if line == "/init":
            run_task(agent, cfg, INIT_TASK,
                     show_thinking=session["thinking"])
            continue
        if line == "/undo":
            out(undo_last_write())
            continue
        if line == "/context":
            cmd_context(agent, cfg)
            continue
        if line == "/tools":
            out("Tools: " + ", ".join(TOOLS))
            continue
        if line == "/model" or line.startswith("/model "):
            parts = line.split(None, 1)
            if len(parts) == 1:
                out(f"Model: {cfg.model} @ {cfg.ollama_host}")
            else:
                cfg.model = client.model = parts[1].strip()
                out(f"Switched to model: {cfg.model} "
                    f"(context kept — /clear for a fresh start)")
            continue
        if line == "/thinking" or line.startswith("/thinking "):
            parts = line.split(None, 1)
            if len(parts) == 1:
                state = "on" if session["thinking"] else "off"
                out(f"Thinking view is {state}. (/thinking on|off)")
            elif parts[1].strip().lower() in ("on", "1", "true"):
                session["thinking"] = True
                out("Thinking view on: raw model output incl. tool JSON.")
            elif parts[1].strip().lower() in ("off", "0", "false"):
                session["thinking"] = False
                out("Thinking view off: clean → lines only.")
            else:
                out("Usage: /thinking [on|off]")
            continue
        if line == "/sessions":
            items = list_sessions()
            if not items:
                out("(no saved sessions)")
            for s in items:
                out(f"{s['name']} — {s['messages']} msgs, "
                    f"{s['tool_calls']} tools, updated {s['updated']}")
            continue
        if line.startswith("/resume "):
            name = line.split(None, 1)[1].strip()
            try:
                data = load_session(name)
            except (FileNotFoundError, ValueError) as e:
                out(str(e), style="red")
                continue
            apply_session(agent, data)
            session["name"] = data.get("name", name)
            s = agent.ctx.stats()
            out(f"Resumed '{session['name']}': {s['messages']} msgs, "
                f"~{s['tokens']:,} tokens.")
            continue
        if line.startswith("/export"):
            parts = line.split(None, 1)
            target = parts[1].strip() if len(parts) > 1 else session["name"]
            try:
                if target.endswith(".md"):
                    p = export_markdown(target[:-3], agent.ctx.messages)
                else:
                    p = save_session(target, snapshot(agent, cfg, target))
                out(f"Exported to {p}")
            except (ValueError, OSError) as e:
                out(f"Export failed: {e}", style="red")
            continue
        if line.startswith("/rename "):
            new = line.split(None, 1)[1].strip()
            old = session["name"]
            try:
                rename_session(old, new)
            except (FileNotFoundError, ValueError) as e:
                # Never-saved session, or name clash on disk: relabel anyway
                # unless the new name is taken.
                if isinstance(e, ValueError):
                    out(str(e), style="red")
                    continue
            session["name"] = new
            out(f"Session '{old}' → '{new}'.")
            continue
        if line == "/fork" or line.startswith("/fork "):
            parts = line.split(None, 1)
            new = parts[1].strip() if len(parts) > 1 else session["name"] + "-fork"
            try:
                p = save_session(new, snapshot(agent, cfg, new))
            except (ValueError, OSError) as e:
                out(f"Fork failed: {e}", style="red")
                continue
            session["name"] = new
            out(f"Forked into '{new}' ({p}). Continuing here.")
            continue
        if line == "/copy" or line.startswith("/copy "):
            parts = line.split(None, 1)
            ok, msg = describe_copy(agent.ctx.messages,
                                    parts[1].strip() if len(parts) > 1 else "")
            out(msg, style="" if ok else "red")
            continue
        if line.startswith("/"):
            out(f"Unknown command: {line}. Try /help.")
            continue
        run_task(agent, cfg, expand_mentions(line, cfg.workspace),
                 show_thinking=session["thinking"])


if __name__ == "__main__":
    main()
