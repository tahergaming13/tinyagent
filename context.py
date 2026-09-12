"""Context manager for small (8K) windows: discard-first, never summarize-by-default."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """Cheap estimator: ~4 chars/token for code/English. Good enough for budgeting."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content", "") or "") + 8  # role overhead
    return total


@dataclass
class ContextManager:
    system_prompt: str
    max_input_tokens: int
    tool_calls: int = 0
    pruned_count: int = 0
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not any(m.get("role") == "system" for m in self.messages):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

    # -- accounting ------------------------------------------------------
    def estimated_tokens(self) -> int:
        return messages_tokens(self.messages)

    # -- mutation --------------------------------------------------------
    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.enforce_budget(preserve_last_user=True)

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def add_tool_result(self, tool_name: str, args: dict, result: str) -> None:
        self.tool_calls += 1
        compressed = self.compress_tool_result(tool_name, result)
        # Assistant turn records the call (compact, so repeat detection works).
        # Past-tense marker on purpose: it must NOT look like call syntax,
        # or small models mimic it instead of using the fenced format.
        self.messages.append({
            "role": "assistant",
            "content": f"[executed: {tool_name} {self._args_brief(args)}]",
        })
        self.messages.append({
            "role": "user",  # tool results go back as user-role (Ollama has no tool role)
            "content": f"[tool_result: {tool_name}]\n{compressed}",
        })
        self.enforce_budget(preserve_last_user=True)

    @staticmethod
    def _args_brief(args: dict) -> str:
        s = str(args)
        return s if len(s) <= 300 else s[:300] + "..."

    # -- compression -----------------------------------------------------
    @staticmethod
    def compress_tool_result(tool_name: str, result: str, limit: int = 6000) -> str:
        """Keep head+tail, preserve errors/exit codes, flag truncation."""
        if len(result) <= limit:
            return result
        head = limit // 2
        tail = limit - head
        note = (f"[TRUNCATED: tool result was {len(result)} chars, "
                f"showing first {head} + last {tail}. Re-read with ranges if needed.]")
        return result[:head] + f"\n... {note} ...\n" + result[-tail:]

    # -- pruning: discard-first ------------------------------------------
    def enforce_budget(self, preserve_last_user: bool = True) -> None:
        """Drop low-value middle messages until under budget.

        Keep: system[0], last user msg (+ its task), recent turns.
        Drop first: old successful run_command output, repeated reads, dupes.
        """
        if self.estimated_tokens() <= self.max_input_tokens:
            return
        system = self.messages[0]
        rest = self.messages[1:]
        if not rest:
            return

        # Identify the last user message (current task) to always keep.
        last_user_idx: int | None = None
        if preserve_last_user:
            for i in range(len(rest) - 1, -1, -1):
                if rest[i]["role"] == "user":
                    last_user_idx = i
                    break

        # Score each message: higher = drop first.
        def score(i: int, m: dict) -> tuple[int, int]:
            c = m.get("content", "") or ""
            if i == last_user_idx:
                return (-100, 0)  # never drop current task
            if i >= len(rest) - 4:
                return (-50, 0)  # keep the 2 most recent turns
            if c.startswith("[tool_result: run_command]") and "EXIT CODE:\n0" in c:
                return (30, len(c))  # old successful commands first
            if c.startswith("[executed: read_file") or c.startswith("[tool_result: read_file]"):
                return (20, len(c))
            if c.startswith("[tool_result:"):
                return (10, len(c))
            if m["role"] == "assistant" and c.startswith("[executed:"):
                return (9, len(c))
            return (0, len(c))

        order = sorted(range(len(rest)), key=lambda i: score(i, rest[i]), reverse=True)
        # Also collapse exact-duplicate tool results regardless of budget.
        seen: set[str] = set()
        for i in list(order):
            c = rest[i].get("content", "") or ""
            if c.startswith("[tool_result:") and c in seen:
                pass  # duplicate -> stays high priority for removal
            seen.add(c)

        kept = set(range(len(rest)))
        for i in order:
            if self._tokens_of([system] + [rest[j] for j in sorted(kept)]) <= self.max_input_tokens:
                break
            if i == last_user_idx or i >= len(rest) - 4:
                continue  # protected
            kept.discard(i)
            self.pruned_count += 1

        # Last resort: truncate the biggest remaining middle message.
        while (kept and self._tokens_of([system] + [rest[j] for j in sorted(kept)])
               > self.max_input_tokens):
            candidates = [j for j in sorted(kept) if j != last_user_idx and j < len(rest) - 4]
            if not candidates:
                break
            biggest = max(candidates, key=lambda j: len(rest[j].get("content", "")))
            c = rest[biggest]["content"]
            rest[biggest] = {"role": rest[biggest]["role"],
                             "content": c[:2000] + "\n... [PRUNED for context budget] ...\n" + c[-2000:]}
            self.pruned_count += 1
            break

        self.messages = [system] + [rest[j] for j in sorted(kept)]

    @staticmethod
    def _tokens_of(msgs: list[dict]) -> int:
        return messages_tokens(msgs)

    # -- repeat detection -------------------------------------------------
    def is_repeating(self, tool_name: str, args: dict, window: int = 4) -> bool:
        """True if the identical call already appears in recent assistant turns."""
        sig = f"[executed: {tool_name} {self._args_brief(args)}]"
        recent = [m.get("content", "") for m in self.messages[-window * 2:]
                  if m.get("role") == "assistant"]
        return recent.count(sig) >= 2

    # -- housekeeping -----------------------------------------------------
    def clear(self, keep_system: bool = True) -> None:
        system = self.messages[0] if self.messages else None
        self.messages = [system] if (keep_system and system) else []
        self.tool_calls = 0

    def stats(self) -> dict:
        return {
            "tokens": self.estimated_tokens(),
            "messages": len(self.messages),
            "tool_calls": self.tool_calls,
            "pruned": self.pruned_count,
        }
