"""The local model: a chat model served by Ollama on the desktop's GPU, for light requests.

It has no tools: it cannot read the vault or write memory, so the router sends it only requests that need
neither. It gets Jarvis's memory index and the recent turns of the chat, and nothing leaves the desktop.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from jarvis.brain.conversations import Turn
from jarvis.brain.tasks import MAX_MEMORY_CHARS, MEMORY_INDEX, RunResult

OLLAMA_URL: Final = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL: Final = "gemma4:latest"
CHAT_TIMEOUT_SECONDS: Final = 300
PROBE_TIMEOUT_SECONDS: Final = 2
HISTORY_TURNS: Final = 12
KEEP_ALIVE: Final = "30m"  # keeps the model loaded between requests; the first one loads it (~1 minute)

INSTRUCTIONS: Final = (
    "You are Jarvis, the personal assistant of the user, running as a local model on the user's home desktop. "
    "You have no tools: you cannot read files, notes or the web, and you cannot change your memory. "
    "If a request needs any of that, say so in one line and suggest asking again starting with /sonnet. "
    "Answer in the language of the request. Be concise: plain text, short paragraphs or lists, no tables, "
    "and one answer rather than several options unless the user asks for options."
)

PostJson = Callable[[str, dict[str, object], float], object]
GetJson = Callable[[str, float], object]


def post_json(url: str, body: dict[str, object], timeout: float) -> object:
    request = urllib.request.Request(
        url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed: object = json.loads(response.read())
        return parsed


def get_json(url: str, timeout: float) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        parsed: object = json.loads(response.read())
        return parsed


@dataclass(frozen=True, slots=True)
class OllamaChat:
    model: str = DEFAULT_LOCAL_MODEL
    memory: Path | None = None  # the memory folder; its index goes in the system message
    url: str = OLLAMA_URL
    post: PostJson = field(default=post_json)
    get: GetJson = field(default=get_json)

    def available(self) -> bool:
        """Ollama answers and has the model."""
        try:
            data = self.get(f"{self.url}/api/tags", PROBE_TIMEOUT_SECONDS)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return False
        models = data.get("models") if isinstance(data, dict) else None
        names = {m.get("name") for m in models if isinstance(m, dict)} if isinstance(models, list) else set()
        return self.model in names

    def messages(self, prompt: str, history: Sequence[Turn]) -> list[dict[str, str]]:
        system = INSTRUCTIONS
        memory = self._read_memory().strip()
        if memory:
            system += f"\n\nYour memory note about the user:\n<jarvis_memory>\n{memory[:MAX_MEMORY_CHARS]}\n</jarvis_memory>"
        recent = [turn for turn in history if turn.role == "user" or turn.ok][-HISTORY_TURNS:]
        return [
            {"role": "system", "content": system},
            *({"role": "user" if turn.role == "user" else "assistant", "content": turn.text} for turn in recent),
            {"role": "user", "content": prompt},
        ]

    def chat(self, prompt: str, history: Sequence[Turn]) -> RunResult:
        body: dict[str, object] = {
            "model": self.model, "stream": False, "keep_alive": KEEP_ALIVE,
            "messages": self.messages(prompt, history),
        }
        try:
            data = self.post(f"{self.url}/api/chat", body, CHAT_TIMEOUT_SECONDS)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            return RunResult(f"The local model did not answer: {exc}", ok=False, model="local")
        if not isinstance(data, dict):
            return RunResult("The local model gave no readable answer.", ok=False, model="local")
        message = data.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            error = data.get("error")
            return RunResult(f"The local model gave no answer. {error or ''}".strip(), ok=False, model="local")
        return RunResult(
            answer=text.strip(), ok=True, cost_usd=0.0,
            input_tokens=_int(data.get("prompt_eval_count")), output_tokens=_int(data.get("eval_count")),
            model="local",
        )

    def _read_memory(self) -> str:
        if self.memory is None:
            return ""
        try:
            return (self.memory / MEMORY_INDEX).read_text(encoding="utf-8")
        except OSError:
            return ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None
