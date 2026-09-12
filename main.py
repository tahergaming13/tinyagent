"""Terminal interface for tinyagent (rich if present, plain fallback)."""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent  # noqa: E402
from config import Config  # noqa: E402
from ollama import OllamaClient, OllamaError  # noqa: E402
from tools import TOOLS  # noqa: E402
from tools.filesystem import read_file, undo_last_write  # noqa: E402

try:
    from rich.console import Console
    from rich.markdown import Markdown
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None  # type: ignore


def out(text: str = "", style: str = "") -> None:
    if RICH:
        console.print(text, style=style, markup=False)
    else:
        print(text)


def out_markdown(text: str) -> None:
    if RICH:
        console.print(Markdown(text))
    else:
        print(text)


HELP = """Commands:
  /help     show this help
  /clear    clear conversation context
  /compact  summarize session into a compact state (one extra model call)
  /init     explore workspace and write AGENTS.md
  /undo     restore the last file written/edited/created
  /context  show model / context usage
  /tools    list available tools
  /model    show current model
  /quit     exit
Type a task to run the agent. @path attaches a file. Tool calls show as →."""


INIT_TASK = (
    "Explore the workspace: list files, read the README, config, and entry "
    "points. Then write AGENTS.md in the workspace root summarizing: what "
    "the project is, how to build/run/test it, key files, and conventions "
    "for future changes. Keep it under 60 lines."
)


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


def run_task(agent: Agent, cfg: Config, line: str) -> None:
    """Run one agent turn with live display; raw tool JSON stays internal."""
    state = {"acc": "", "printed": 0, "suppressed": False}

    def _print_live(s: str) -> None:
        if RICH:
            console.print(s, end="", markup=False, highlight=False)
        else:
            print(s, end="", flush=True)

    def on_token(tok: str) -> None:
        state["acc"] += tok
        if "```" in state["acc"]:
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
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
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
    out(f"tinyagent  |  Model: {cfg.model}  |  Context: {cfg.context_size}  "
        f"|  Workspace: {os.path.abspath(cfg.workspace)}")
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
                line = console.input("[bold cyan]> [/]") if RICH else input("> ")
            except (KeyboardInterrupt, EOFError):
                out("\nBye.")
                break
        except (KeyboardInterrupt, EOFError):
            out("\nBye.")
            break
        line = line.strip()
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
            run_task(agent, cfg, INIT_TASK)
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
        if line == "/model":
            out(f"Model: {cfg.model} @ {cfg.ollama_host}")
            continue
        if line.startswith("/"):
            out(f"Unknown command: {line}. Try /help.")
            continue
        run_task(agent, cfg, expand_mentions(line, cfg.workspace))


if __name__ == "__main__":
    main()
