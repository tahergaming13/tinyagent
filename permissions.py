"""Path validation + dangerous-command gate. Deterministic, no LLM involved."""
from __future__ import annotations

import os
import re

# Substrings that mark a command as needing explicit confirmation.
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b", r"\brmdir\b", r"\bdel\b", r"\bformat\b",
    r"\bshutdown\b", r"\brestart\b", r"\bdiskpart\b",
    r"git\s+reset\s+--hard", r"git\s+clean\s+-f",
    r":\(\)\s*\{",  # fork bomb
    r"\bmkfs\b", r"\bdd\b.*of=/dev/",
]


def resolve_in_workspace(workspace: str, path: str) -> str:
    """Resolve path against workspace; raise ValueError on traversal.

    Returns absolute normalized path guaranteed to be inside workspace.
    """
    ws = os.path.abspath(workspace)
    # Expand user vars, handle both relative and absolute input.
    candidate = path.strip().strip('"').strip("'")
    if not os.path.isabs(candidate):
        candidate = os.path.join(ws, candidate)
    real = os.path.abspath(candidate)
    # Allow the workspace root itself; otherwise require prefix + separator.
    if real != ws and not real.startswith(ws + os.sep):
        raise ValueError(f"Path escapes workspace: {path!r} (workspace: {ws})")
    return real


def is_dangerous(command: str) -> str | None:
    """Return matching pattern description if command looks dangerous, else None."""
    low = command.lower()
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, low):
            return pat
    return None
