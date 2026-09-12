"""Central tool registry. Single entry point: execute_tool()."""
from __future__ import annotations

import json
from typing import Any, Callable

from tools.filesystem import list_files, read_file, write_file
from tools.terminal import run_command

# Compact schemas shown to the model exactly once (kept tiny for 7B models).
TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "desc": "Read a text file with line numbers. Optional 1-based start_line/end_line.",
        "args": {"path": "string (required)", "start_line": "int?", "end_line": "int?"},
    },
    {
        "name": "write_file",
        "desc": "Create or replace a file in the workspace.",
        "args": {"path": "string (required)", "content": "string (required)"},
    },
    {
        "name": "list_files",
        "desc": "List files/dirs as a tree (depth-limited, respects .gitignore).",
        "args": {"path": "string, default '.'"},
    },
    {
        "name": "run_command",
        "desc": "Run a shell command in the workspace, captures stdout/stderr/exit code.",
        "args": {"command": "string (required)"},
    },
]


def _read(a: dict, ws: str, cfg) -> str:
    return read_file(
        a.get("path", ""), workspace=ws,
        max_file_size=cfg.max_file_size,
        start_line=a.get("start_line"), end_line=a.get("end_line"),
    )


def _write(a: dict, ws: str, cfg) -> str:
    if "path" not in a or "content" not in a:
        return "ERROR: write_file needs {path, content}."
    return write_file(a["path"], a["content"], workspace=ws)


def _list(a: dict, ws: str, cfg) -> str:
    return list_files(a.get("path", "."), workspace=ws)


def _run(a: dict, ws: str, cfg) -> str:
    if "command" not in a:
        return "ERROR: run_command needs {command}."
    return run_command(a["command"], workspace=ws,
                       timeout=cfg.command_timeout,
                       max_output=cfg.max_command_output)


_DISPATCH: dict[str, Callable[[dict, str, Any], str]] = {
    "read_file": _read,
    "write_file": _write,
    "list_files": _list,
    "run_command": _run,
}

TOOLS = tuple(_DISPATCH.keys())


def execute_tool(name: str, arguments: Any, workspace: str, cfg) -> str:
    """Validate + execute + catch exceptions. Never raises; returns string."""
    if name not in _DISPATCH:
        return f"ERROR: unknown tool '{name}'. Available: {', '.join(TOOLS)}."
    if not isinstance(arguments, dict):
        # Model sometimes sends a JSON string.
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return f"ERROR: arguments for '{name}' must be a JSON object, got string."
        else:
            return f"ERROR: arguments for '{name}' must be a JSON object."
    try:
        result = _DISPATCH[name](arguments, workspace, cfg)
    except Exception as e:  # noqa: BLE001 - tool must never crash the loop
        return f"ERROR: tool '{name}' raised {type(e).__name__}: {e}"
    # Hard cap so a rogue tool can't blow the 8K context.
    cap = max(cfg.max_command_output, cfg.max_file_size // 8)
    if len(result) > cap:
        head = cap // 2
        result = (result[:head]
                  + f"\n... [TOOL RESULT TRUNCATED {len(result) - cap} chars] ...\n"
                  + result[-head:])
    return result


def tools_brief() -> str:
    """One-line-per-tool summary for the system prompt."""
    return "\n".join(
        f"- {s['name']}({', '.join(s['args'].keys())}): {s['desc']}" for s in TOOL_SCHEMAS
    )
