"""Minimal Ollama native API client (stdlib only, offline-friendly)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434",
                 model: str = "qwen3:8b",
                 temperature: float = 0.2,
                 context_size: int = 8192,
                 timeout: int = 120):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.context_size = context_size
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url(path), data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}: {e.reason}. "
                "Is `ollama serve` running?"
            ) from e
        except TimeoutError as e:
            raise OllamaError(f"Ollama request timed out after {self.timeout}s.") from e

    def check_connection(self) -> tuple[bool, str]:
        """Return (ok, message). Never raises."""
        try:
            req = urllib.request.Request(self._url("/api/tags"), method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            if self.model not in models and models:
                return True, f"Connected. Model '{self.model}' not in local list {models}. Run: ollama pull {self.model}"
            return True, f"Connected. Models: {models or ['(none pulled)']}"
        except Exception as e:  # noqa: BLE001 - report any failure as message
            return False, (
                f"Cannot reach Ollama at {self.host}: {e}. "
                "Start it with `ollama serve` and pull a model, e.g. `ollama pull qwen3:8b`."
            )

    def list_models(self) -> list[str]:
        """Local model names from /api/tags. Raises OllamaError."""
        try:
            req = urllib.request.Request(self._url("/api/tags"), method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])
                    if m.get("name")]
        except Exception as e:  # noqa: BLE001 - caller reports it
            raise OllamaError(
                f"Cannot list models at {self.host}: {e}. "
                "Is `ollama serve` running?") from e

    def chat(self, messages: list[dict], stream: bool = False,
             on_token: Optional[Callable[[str], None]] = None) -> str:
        """Non-streaming by default. If stream=True, calls on_token per chunk."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_size,
            },
        }
        if not stream:
            try:
                res = self._post("/api/chat", payload)
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    body = ""
                if "model" in body.lower() and "not found" in body.lower():
                    raise OllamaError(
                        f"Model '{self.model}' not found. Run: ollama pull {self.model}"
                    ) from e
                raise OllamaError(f"Ollama error {e.code}: {body[:500]}") from e
            msg = (res.get("message") or {}).get("content", "")
            if on_token and msg:
                on_token(msg)
            return msg

        # Streaming path (NDJSON)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/chat"), data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        full: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError(f"Ollama error: {chunk['error']}")
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        full.append(token)
                        if on_token:
                            on_token(token)
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            raise OllamaError(f"Ollama error {e.code}: {body[:500]}") from e
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}: {e.reason}. Is `ollama serve` running?"
            ) from e
        return "".join(full)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """Generator version of streaming chat."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_size,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/chat"), data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError(f"Ollama error: {chunk['error']}")
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            raise OllamaError(f"Ollama error {e.code}: {body[:500]}") from e
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}: {e.reason}. Is `ollama serve` running?"
            ) from e
