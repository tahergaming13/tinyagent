"""Copy agent output to the OS clipboard.

The fullscreen TUI captures the mouse, so terminal text selection doesn't
reach it — /copy is the keyboard-only way answers out. Paste (Ctrl+V /
right-click) is handled by the terminal via bracketed paste and needs
nothing from us.
"""
from __future__ import annotations

EXECUTED_PREFIX = "[executed:"


def assistant_answers(messages: list[dict]) -> list[str]:
    """Final answers only: skips system prompt, tool-call markers, results."""
    out: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = (m.get("content") or "").strip()
        if not content or content.startswith(EXECUTED_PREFIX):
            continue
        out.append(content)
    return out


def format_transcript(messages: list[dict]) -> str:
    lines: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        content = (m.get("content") or "").strip()
        if content:
            lines += [f"## {m.get('role', '?')}", "", content, ""]
    return "\n".join(lines).strip()


def copy_to_clipboard(text: str) -> None:
    """Raises RuntimeError with a fix-it hint when unavailable."""
    if not text.strip():
        raise RuntimeError("Nothing to copy.")
    try:
        import pyperclip
    except ImportError as e:
        raise RuntimeError(
            "Clipboard needs one extra package: "
            "`pip install pyperclip` (or `pipx inject tinyagent pyperclip`)."
        ) from e
    try:
        pyperclip.copy(text)
    except Exception as e:  # noqa: BLE001 - missing xclip etc.
        raise RuntimeError(
            f"Clipboard unavailable ({e}). "
            "Linux needs xclip, xsel, or wl-clipboard installed."
        ) from e


def describe_copy(messages: list[dict], arg: str) -> tuple[bool, str]:
    """Shared /copy logic. Returns (ok, message for the user)."""
    if arg == "all":
        text, label = format_transcript(messages), "transcript"
    else:
        n = 1
        if arg:
            if not arg.isdigit() or int(arg) < 1:
                return False, "Usage: /copy [N|all]"
            n = int(arg)
        answers = assistant_answers(messages)
        if len(answers) < n:
            return False, "Nothing to copy yet — no answers."
        text = answers[-n]
        label = f"answer #{len(answers) - n + 1}"
    try:
        copy_to_clipboard(text)
    except RuntimeError as e:
        return False, str(e)
    return True, f"Copied {label} ({len(text)} chars)."
