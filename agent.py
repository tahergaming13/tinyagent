"""Agent loop: ReAct-like, one central loop, prompt-based tool calls."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from context import ContextManager
from ollama import OllamaClient, OllamaError
from tools import TOOLS, execute_tool, tools_brief

SYSTEM_PROMPT = """You are a local coding agent.

Solve the user's task using the available tools.

Inspect files before modifying them when necessary.
Use tools instead of merely describing actions.
Run tests or commands when useful.
Do not expose private reasoning.
Be concise.

Available tools:
{tools}

To call a tool, emit exactly one fenced block:

```tool_call
{{"name": "<tool>", "arguments": {{...}}}}
```

Tool names: {names}. One tool call per turn. Otherwise reply normally.
History marks past calls as [executed: ...] — never write that marker yourself; to act, always emit the fenced block."""

FENCE_RE = re.compile(r"```(?:tool_call|json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(tools=tools_brief(), names=", ".join(TOOLS))


def parse_tool_calls(text: str) -> list[dict]:
    """Extract tool calls from fenced blocks, bare lines, or raw JSON.

    Tolerant on purpose: small models leak variants like
    `[tool_call] read_file {"path": "a.py"}` as plain text. Never raises.
    """
    found: list[dict] = []
    for m in FENCE_RE.finditer(text):
        body = m.group(1).strip()
        call = _as_call(body)
        if call:
            found.append(call)
    if not found:
        for line in text.splitlines():
            call = _as_bare_call(line)
            if call:
                found.append(call)
                if len(found) >= 3:
                    break
    if not found:
        call = _as_call(text.strip())
        if call:
            found.append(call)
    if not found:
        # Last resort: the reply IS just an echoed history marker
        # ("[executed: name {...}]") with no other text — treat as call
        # intent. Repeat detection still guards against infinite regress.
        call = _as_echoed_call(text.strip())
        if call:
            found.append(call)
    return found[:3]  # guard: max 3 calls per turn


def _as_bare_call(line: str) -> dict | None:
    m = re.match(r"\[tool_call\]\s+(\w+)\s+(\{.*\})\s*$", line.strip())
    if not m:
        return None
    return _call_from_parts(m.group(1), m.group(2))


def _as_echoed_call(text: str) -> dict | None:
    m = re.match(r"\[executed:\s+(\w+)\s+(\{.*\})\s*\]\s*$", text, re.DOTALL)
    if not m:
        return None
    return _call_from_parts(m.group(1), m.group(2))


def _call_from_parts(name: str, raw_args: str) -> dict | None:
    if name not in TOOLS:
        return None
    try:
        args = json.loads(raw_args)
    except (json.JSONDecodeError, ValueError):
        try:
            args = ast.literal_eval(raw_args)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(args, dict):
        return None
    return {"name": name, "arguments": args}


def _as_call(body: str) -> dict | None:
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        try:
            obj = ast.literal_eval(body)  # models often use single quotes
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool")
    args = obj.get("arguments", obj.get("args", {}))
    if not isinstance(name, str) or name not in TOOLS:
        return None
    if not isinstance(args, dict):
        return None
    return {"name": name, "arguments": args}


@dataclass
class AgentResult:
    answer: str
    tool_calls: int
    stopped: str  # "answer" | "max_iterations" | "repeat" | "error"


class Agent:
    def __init__(self, client: OllamaClient, config,
                 debug: bool = False,
                 log: Optional[Callable[[str], None]] = None):
        self.client = client
        self.cfg = config
        self.debug = debug or getattr(config, "debug", False)
        self._log = log or (lambda s: print(s) if self.debug else None)
        self.ctx = ContextManager(
            system_prompt=build_system_prompt(),
            max_input_tokens=config.max_input_tokens,
        )

    def _dbg(self, msg: str) -> None:
        self._log(f"[DEBUG] {msg}")

    def ask(self, user_text: str,
            on_token: Optional[Callable[[str], None]] = None,
            on_tool: Optional[Callable[[str, dict], None]] = None,
            on_result: Optional[Callable[[str, dict, str], None]] = None) -> AgentResult:
        """Run the tool loop until a final answer (no tool call) or a stop."""
        self.ctx.add_user(user_text)
        self._dbg(f"Estimated context: {self.ctx.estimated_tokens()} tokens")
        calls = 0

        while True:
            if calls >= self.cfg.max_tool_calls:
                msg = (f"Stopped after {self.cfg.max_tool_calls} tool calls. "
                       "Summarize progress concisely and ask the user how to proceed.")
                self.ctx.add_assistant(msg)
                return AgentResult(msg, calls, "max_iterations")
            try:
                # Stream for UX, but accumulate full text for parsing.
                chunks: list[str] = []
                reply = self.client.chat(
                    self.ctx.messages, stream=True,
                    on_token=lambda t: (chunks.append(t),
                                        on_token(t) if on_token else None),
                )
                # chat() with on_token already returns full text; chunks unused
                # when client is mocked — fall back to reply.
                text = reply if reply else "".join(chunks)
            except OllamaError as e:
                err = f"Model error: {e}"
                self._dbg(err)
                return AgentResult(err, calls, "error")

            if not text.strip():
                # Some models answer refusals/edge cases with zero tokens.
                # Say so explicitly instead of showing a silent blank.
                self._dbg("Model returned an empty response.")
                msg = ("[empty response from model — it produced no text. "
                       "Rephrase the task or try again.]")
                self.ctx.add_assistant(msg)
                return AgentResult(msg, calls, "error")

            tool_calls = parse_tool_calls(text)
            if not tool_calls:
                self.ctx.add_assistant(text)
                return AgentResult(text, calls, "answer")

            # Execute calls in order (usually just one).
            for call in tool_calls:
                name, args = call["name"], call["arguments"]
                calls += 1
                if on_tool:
                    on_tool(name, args)
                self._dbg(f"Tool call: {name} ({self.ctx.estimated_tokens()} tok ctx)")
                if self.ctx.is_repeating(name, args):
                    warn = (f"[tool_result: {name}]\n"
                            "ERROR: you already issued this exact call twice. "
                            "Try different arguments, read the previous result, "
                            "or give your final answer.")
                    self.ctx.messages.append({"role": "assistant",
                                              "content": f"[executed: {name} {args}]"})
                    self.ctx.messages.append({"role": "user", "content": warn})
                    self._dbg("Repeat detected — warned model instead of executing.")
                    break
                result = execute_tool(name, args, self.cfg.workspace, self.cfg)
                if on_result:
                    on_result(name, args, result)
                self._dbg(f"Tool result: {len(result) // 4} tok; "
                          f"ctx now {self.ctx.estimated_tokens()} tok (pre-add)")
                self.ctx.add_tool_result(name, args, result)
                self._dbg(f"Context after tool result: {self.ctx.estimated_tokens()} tokens")
            # loop back to model with fresh tool results
