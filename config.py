"""Configuration for mini-agent. Env vars > .env file > defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULTS = {
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen3:8b",
    "OLLAMA_CONTEXT": "8192",
    "CONTEXT_SIZE": "8192",
    "RESERVED_OUTPUT": "2048",
    "TEMPERATURE": "0.2",
    "MAX_TOOL_CALLS": "20",
    "COMMAND_TIMEOUT": "30",
    "WORKSPACE": "./workspace",
    "MAX_FILE_SIZE": "100000",
    "MAX_COMMAND_OUTPUT": "12000",
    "DEBUG": "0",
}


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (stdlib only). Does not override real env vars."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()


def _get(name: str) -> str:
    return os.environ.get(name, DEFAULTS[name])


@dataclass
class Config:
    ollama_host: str = field(default_factory=lambda: _get("OLLAMA_HOST").rstrip("/"))
    model: str = field(default_factory=lambda: _get("OLLAMA_MODEL"))
    context_size: int = field(default_factory=lambda: int(_get("CONTEXT_SIZE") or _get("OLLAMA_CONTEXT")))
    reserved_output: int = field(default_factory=lambda: int(_get("RESERVED_OUTPUT")))
    temperature: float = field(default_factory=lambda: float(_get("TEMPERATURE")))
    max_tool_calls: int = field(default_factory=lambda: int(_get("MAX_TOOL_CALLS")))
    command_timeout: int = field(default_factory=lambda: int(_get("COMMAND_TIMEOUT")))
    workspace: str = field(default_factory=lambda: _get("WORKSPACE"))
    max_file_size: int = field(default_factory=lambda: int(_get("MAX_FILE_SIZE")))
    max_command_output: int = field(default_factory=lambda: int(_get("MAX_COMMAND_OUTPUT")))
    debug: bool = field(default_factory=lambda: _get("DEBUG") in ("1", "true", "True"))

    @property
    def max_input_tokens(self) -> int:
        return max(1024, self.context_size - self.reserved_output)

    @classmethod
    def reload(cls) -> "Config":
        _load_dotenv()
        return cls()
