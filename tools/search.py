"""Code search tools: grep_files (content), glob_files (names)."""
from __future__ import annotations

import fnmatch
import os
import re

from permissions import resolve_in_workspace
from tools.filesystem import HIDDEN_DIRS, _ignored, _read_gitignore

MAX_MATCHES = 50
MAX_FILE_BYTES = 200_000  # skip bigger files silently (binaries, bundles)


def grep_files(pattern: str, path: str = ".", workspace: str = ".",
               include: str = "*", max_matches: int = MAX_MATCHES) -> str:
    """Regex search over file contents. Returns file:line: match lines."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex {pattern!r}: {e}"
    try:
        real = resolve_in_workspace(workspace, path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not os.path.isdir(real):
        return f"ERROR: not a directory: {path}"
    ws = os.path.abspath(workspace)
    ig = _read_gitignore(workspace)
    hits: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(real):
        # Prune junk dirs in place (also respects .gitignore).
        dirnames[:] = [d for d in dirnames
                       if d not in HIDDEN_DIRS
                       and not _ignored(os.path.relpath(os.path.join(dirpath, d), ws), ig)]
        dirnames.sort()
        for name in sorted(filenames):
            if len(hits) >= max_matches:
                truncated = True
                break
            if not fnmatch.fnmatch(name, include):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ws).replace(os.sep, "/")
            if _ignored(rel, ig):
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8", errors="strict") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                            if len(hits) >= max_matches:
                                truncated = True
                                break
            except (OSError, UnicodeDecodeError):
                continue
        if truncated:
            break
    if not hits:
        return f"GREP: {pattern!r} — no matches."
    out = [f"GREP: {pattern!r} ({len(hits)} matches)"] + hits
    if truncated:
        out.append(f"... [TRUNCATED at {max_matches} matches; narrow the pattern or path]")
    return "\n".join(out)


def glob_files(pattern: str = "**/*.py", path: str = ".",
               workspace: str = ".", max_results: int = 100) -> str:
    """Find files by name pattern (e.g. **/*.py, tests/*.py)."""
    try:
        real = resolve_in_workspace(workspace, path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not os.path.isdir(real):
        return f"ERROR: not a directory: {path}"
    ws = os.path.abspath(workspace)
    ig = _read_gitignore(workspace)
    pat = pattern.replace("\\", "/").lstrip("./")
    found: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(real):
        dirnames[:] = [d for d in dirnames
                       if d not in HIDDEN_DIRS
                       and not _ignored(os.path.relpath(os.path.join(dirpath, d), ws), ig)]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ws).replace(os.sep, "/")
            base = os.path.relpath(full, real).replace(os.sep, "/")
            if _ignored(rel, ig):
                continue
            if (fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(rel, pat)
                    or fnmatch.fnmatch(name, pat)):
                found.append(rel)
                if len(found) >= max_results:
                    truncated = True
                    break
        if truncated:
            break
    found.sort()
    if not found:
        return f"GLOB: {pattern} — no files."
    out = [f"GLOB: {pattern} ({len(found)} files)"] + found
    if truncated:
        out.append(f"... [TRUNCATED at {max_results}; narrow the pattern]")
    return "\n".join(out)
