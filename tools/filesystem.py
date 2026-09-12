"""Filesystem tools: read_file, write_file, list_files."""
from __future__ import annotations

import fnmatch
import os

from permissions import resolve_in_workspace

HIDDEN_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
               ".next", "dist", "build", ".idea", ".vscode", "target"}

MAX_LINES_DEFAULT = 2000


def _read_gitignore(workspace: str) -> list[str]:
    p = os.path.join(os.path.abspath(workspace), ".gitignore")
    patterns: list[str] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        pass
    return patterns


def _ignored(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace(os.sep, "/")
    for pat in patterns:
        pat = pat.strip().strip("/")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*"):
            return True
        if "/" not in pat and pat in rel.split("/"):
            return True
    return False


def read_file(path: str, workspace: str = ".", max_file_size: int = 100_000,
              start_line: int | None = None, end_line: int | None = None) -> str:
    """Read text file with line numbers. Supports optional 1-based range."""
    try:
        real = resolve_in_workspace(workspace, path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not os.path.isfile(real):
        return f"ERROR: file not found: {path}"
    try:
        size = os.path.getsize(real)
    except OSError as e:
        return f"ERROR: cannot stat file: {e}"
    # Read with graceful encoding fallback.
    text: str | None = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(real, "r", encoding=enc) as f:
                text = f.read(max_file_size + 1)
            break
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return f"ERROR: cannot read file: {e}"
    if text is None:
        return f"ERROR: cannot decode file (tried utf-8, latin-1): {path}"
    truncated = len(text) > max_file_size
    if truncated:
        text = text[:max_file_size]
    lines = text.splitlines()
    total = len(lines)
    # Apply optional range (1-based inclusive).
    s = (start_line or 1) - 1
    e = end_line if end_line is not None else total
    s = max(0, s)
    e = min(total, e)
    if s >= e and total > 0:
        return f"ERROR: invalid range {start_line}-{end_line} for file with {total} lines."
    shown = lines[s:e]
    out = [f"FILE: {path}  ({total} lines; 'N |' prefixes are display-only)"]
    for i, ln in enumerate(shown, start=s + 1):
        out.append(f"{i} | {ln}")
    if truncated:
        out.append(f"... [TRUNCATED at {max_file_size} chars; file is {size} bytes. Re-read with start_line/end_line.]")
    elif (s > 0 or e < total):
        out.append(f"... [showing lines {s + 1}-{e} of {total}]")
    return "\n".join(out)


def write_file(path: str, content: str, workspace: str = ".") -> str:
    """Create or replace a file inside the workspace."""
    try:
        real = resolve_in_workspace(workspace, path)
    except ValueError as e:
        return f"ERROR: {e}"
    try:
        parent = os.path.dirname(real)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = content.encode("utf-8")
        with open(real, "wb") as f:
            f.write(data)
        nlines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"OK: wrote {path} ({len(data)} bytes, {nlines} lines)"
    except OSError as e:
        return f"ERROR: cannot write file: {e}"


def list_files(path: str = ".", workspace: str = ".",
               max_depth: int = 3, max_entries: int = 200) -> str:
    """Tree listing, respects .gitignore, hides junk dirs, depth-limited."""
    try:
        real = resolve_in_workspace(workspace, path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not os.path.isdir(real):
        return f"ERROR: not a directory: {path}"
    ws = os.path.abspath(workspace)
    patterns = _read_gitignore(workspace)
    entries: list[str] = []
    base_label = os.path.basename(os.path.abspath(real)) or real
    entries.append(f"{base_label}/")

    def walk(dirpath: str, prefix: str, depth: int) -> bool:
        """Return False when entry budget exhausted."""
        try:
            names = sorted(os.listdir(dirpath))
        except OSError as e:
            entries.append(f"{prefix} [ERROR: {e}]")
            return True
        # dirs first for readable tree
        names.sort(key=lambda n: (not os.path.isdir(os.path.join(dirpath, n)), n.lower()))
        shown = 0
        for i, name in enumerate(names):
            if len(entries) >= max_entries:
                entries.append(f"{prefix}... [TRUNCATED: >{max_entries} entries]")
                return False
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ws)
            if name in HIDDEN_DIRS or _ignored(rel, patterns):
                continue
            last = (i == len(names) - 1)
            branch = "└── " if last else "├── "
            entries.append(f"{prefix}{branch}{name}{'/' if os.path.isdir(full) else ''}")
            if os.path.isdir(full) and depth < max_depth:
                ext = "    " if last else "│   "
                if not walk(full, prefix + ext, depth + 1):
                    return False
            shown += 1
            if shown >= max_entries:
                entries.append(f"{prefix}... [TRUNCATED]")
                return False
        return True

    walk(real, "", 1)
    return "\n".join(entries)
