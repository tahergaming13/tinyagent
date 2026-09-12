"""Clean terminal interface for mini-agent."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import Agent  # noqa: E402
from config import Config  # noqa: E402
from ollama import OllamaClient  # noqa: E402
from tools import TOOLS  # noqa: E402

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
  /context  show model / context usage
  /tools    list available tools
  /model    show current model
  /quit     exit
Just type a task to run the agent. Tool calls show as → name(args)."""


def cmd_context(agent: Agent, cfg: Config) -> None:
    s = agent.ctx.stats()
    out(f"Model: {cfg.model}\n"
        f"Context: {s['tokens']:,} / {cfg.context_size:,} "
        f"(budget {cfg.max_input_tokens:,})\n"
        f"Messages: {s['messages']}  Tool calls: {s['tool_calls']}  "
        f"Pruned: {s['pruned']}")


def main() -> None:
    cfg = Config()
    os.makedirs(cfg.workspace, exist_ok=True)
    client = OllamaClient(host=cfg.ollama_host, model=cfg.model,
                          temperature=cfg.temperature,
                          context_size=cfg.context_size)
    ok, msg = client.check_connection()
    out(f"mini-agent  |  Model: {cfg.model}  |  Context: {cfg.context_size}  "
        f"|  Workspace: {os.path.abspath(cfg.workspace)}")
    out(msg, style="green" if ok else "red")
    if not ok:
        out("You can still explore /help, but tasks need Ollama running.")
    out("Type /help for commands.\n")

    agent = Agent(client, cfg, debug=cfg.debug)

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

        # --- run the agent with live display, raw tool JSON kept internal ---
        # Per-turn streaming state. A turn is one model reply: either a tool
        # call (shown as → line, raw JSON hidden) or final text (live).
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

        out("● Thinking...", style="dim")
        result = agent.ask(line, on_token=on_token, on_tool=on_tool)
        if result.stopped == "answer":
            # Flush anything held back (e.g. code fences in final answers).
            rest = state["acc"][state["printed"]:]
            if rest:
                _print_live(rest)
            out("")  # newline after live stream
        else:
            # max_iterations / repeat / error: print the returned text.
            out(result.answer, style="red" if result.stopped == "error" else "")
        s = agent.ctx.stats()
        out(f"— done. Context: ~{s['tokens']:,} / {cfg.context_size:,} tokens —",
            style="dim")


if __name__ == "__main__":
    main()
