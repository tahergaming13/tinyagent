"""Session persistence: save/load/list/rename/export conversations.

Plain JSON files under ~/.tinyagent/sessions (or $TINYAGENT_HOME/sessions).
No database, no cloud. Shapes are validated on load; corrupt files error out.
"""
from __future__ import annotations

import datetime
import json
import os

HOME_ENV = "TINYAGENT_HOME"


def sessions_dir() -> str:
    base = os.environ.get(HOME_ENV) or os.path.join(
        os.path.expanduser("~"), ".tinyagent")
    d = os.path.join(base, "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str, ext: str = ".json") -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name).strip("._")
    if not safe:
        raise ValueError("Session name must contain letters or numbers.")
    return os.path.join(sessions_dir(), safe + ext)


def save_session(name: str, payload: dict) -> str:
    """Write session JSON. Returns the file path."""
    if not isinstance(payload.get("messages"), list):
        raise ValueError("Session payload needs a 'messages' list.")
    data = dict(payload)
    data["name"] = name
    data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    p = _path(name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


def load_session(name: str) -> dict:
    """Read + validate session JSON. Raises FileNotFoundError / ValueError."""
    p = _path(name)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"No session named '{name}'.")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Session '{name}' is corrupt: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError(f"Session '{name}' has an invalid shape.")
    for m in data["messages"]:
        if not isinstance(m, dict) or "role" not in m or "content" not in m:
            raise ValueError(f"Session '{name}' has an invalid message.")
    return data


def list_sessions() -> list[dict]:
    """Newest first: [{name, updated, messages, tool_calls}]."""
    out: list[dict] = []
    try:
        files = os.listdir(sessions_dir())
    except OSError:
        return out
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(sessions_dir(), fn), encoding="utf-8") as f:
                data = json.load(f)
            out.append({
                "name": data.get("name", fn[:-5]),
                "updated": data.get("updated", "?"),
                "messages": len(data.get("messages", [])),
                "tool_calls": data.get("tool_calls", 0),
            })
        except (json.JSONDecodeError, OSError):
            out.append({"name": fn[:-5], "updated": "(corrupt)",
                        "messages": 0, "tool_calls": 0})
    out.sort(key=lambda s: str(s["updated"]), reverse=True)
    return out


def rename_session(old: str, new: str) -> str:
    """Rename a saved session file (+ its stored name). Returns new path."""
    src = _path(old)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"No session named '{old}'.")
    dst = _path(new)
    if os.path.isfile(dst):
        raise ValueError(f"A session named '{new}' already exists.")
    data = load_session(old)
    data["name"] = new
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.remove(src)
    return dst


def export_markdown(name: str, messages: list[dict]) -> str:
    """Write a readable transcript. Returns the file path."""
    lines = [f"# Session: {name}", ""]
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if role == "system" or not content:
            continue
        lines += [f"## {role}", "", content, ""]
    p = _path(name, ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p
