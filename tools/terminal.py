"""Terminal tool: run_command with timeout, cwd=workspace, output limits."""
from __future__ import annotations

import os
import subprocess

from permissions import is_dangerous


def run_command(command: str, workspace: str = ".", timeout: int = 30,
                max_output: int = 12_000,
                auto_approve_dangerous: bool = False) -> str:
    """Execute a shell command inside workspace. Capture stdout/stderr/exit code."""
    if not command or not command.strip():
        return "ERROR: empty command."
    danger = is_dangerous(command)
    if danger and not auto_approve_dangerous:
        return (
            f"BLOCKED: command looks dangerous (matched `{danger}`).\n"
            "Ask the user for confirmation, then re-issue with explicit approval, "
            "or run it manually. Command was NOT executed."
        )
    ws = os.path.abspath(workspace)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=ws, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        return _format(command, None, out, err, max_output,
                       note=f"TIMEOUT after {timeout}s (process killed).")
    except OSError as e:
        return f"ERROR: cannot execute command: {e}"
    return _format(command, proc.returncode,
                   proc.stdout or "", proc.stderr or "", max_output)


def _truncate(s: str, limit: int) -> tuple[str, bool]:
    if len(s) <= limit:
        return s, False
    # Keep head + tail so errors at the end survive.
    head = int(limit * 0.5)
    tail = limit - head
    return s[:head] + f"\n... [TRUNCATED {len(s) - limit} chars] ...\n" + s[-tail:], True


def _format(command: str, code: int | None, stdout: str, stderr: str,
            max_output: int, note: str = "") -> str:
    per_stream = max_output // 2
    out, t1 = _truncate(stdout, per_stream)
    err, t2 = _truncate(stderr, per_stream)
    lines = [f"COMMAND:\n{command}", "",
             f"EXIT CODE:\n{code}" if code is not None else "EXIT CODE:\n(killed)"]
    if note:
        lines += ["", f"NOTE:\n{note}"]
    lines += ["", "STDOUT:", out if out else "(empty)", "", "STDERR:", err if err else "(empty)"]
    if t1 or t2:
        lines += ["", "[OUTPUT TRUNCATED: showing head + tail of each stream]"]
    return "\n".join(lines)
