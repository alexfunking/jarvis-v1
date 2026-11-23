"""Conversations with Jarvis: each one is its own Claude Code session, continued with `--resume`.

`claude_turns` counts the turns that session has seen. Turns it missed (answered by the local model, or
imported from the backup Brain, whose sessions stay on the laptop) are handed to Claude with the next
request, so the conversation reads the same whichever model answered.

The turns are also kept here, in one JSON file, so the interface can list the conversations and show
their history after a restart. The file holds what the user asked, so it lives under the ignored
`data/` folder and never in git.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Final

TITLE_CHARS: Final = 60


@dataclass(frozen=True, slots=True)
class Turn:
    role: str  # "user" or "jarvis"
    text: str
    at: float
    model: str = ""
    seconds: float | None = None
    ok: bool = True


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    title: str
    created: float
    updated: float
    session_id: str = ""
    turns: tuple[Turn, ...] = field(default_factory=tuple)
    claude_turns: int = 0  # how many of `turns` the Claude Code session has seen

    def unseen_by_claude(self) -> tuple[Turn, ...]:
        """The turns the Claude session has not seen; all of them when there is no session yet."""
        return self.turns[self.claude_turns:] if self.session_id else self.turns

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "updated": self.updated, "turns": len(self.turns)}

    def to_json(self) -> dict[str, object]:
        return {**self.summary(), "messages": [asdict(turn) for turn in self.turns]}


def title_for(prompt: str) -> str:
    line = " ".join(prompt.split())
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


class ConversationStore:
    """All conversations in memory, written to `path` after every change (None keeps them in memory only)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._items: dict[str, Conversation] = self._load()

    def _load(self) -> dict[str, Conversation]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        items: dict[str, Conversation] = {}
        for entry in raw if isinstance(raw, list) else []:
            try:
                turns = tuple(Turn(**turn) for turn in entry.pop("turns", []))
                conversation = Conversation(**entry, turns=turns)
            except TypeError:
                continue
            items[conversation.id] = conversation
        return items

    def _save(self) -> None:
        if self._path is None:
            return
        data = [asdict(conversation) for conversation in self._items.values()]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def create(self, first_prompt: str = "") -> Conversation:
        now = time.time()
        conversation = Conversation(uuid.uuid4().hex[:12], title_for(first_prompt) or "New chat", now, now)
        with self._lock:
            self._items[conversation.id] = conversation
            self._save()
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            return self._items.get(conversation_id)

    def recent(self, limit: int = 30) -> list[Conversation]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda c: c.updated, reverse=True)
        return items[:limit]

    def add_exchange(
        self, conversation_id: str, question: Turn, answer: Turn, session_id: str, claude_saw_all: bool = False,
    ) -> None:
        """Append a question and its answer; `claude_saw_all` marks every turn, these two too, as seen."""
        with self._lock:
            conversation = self._items.get(conversation_id)
            if conversation is None:
                return
            title = conversation.title
            if not conversation.turns and title == "New chat":
                title = title_for(question.text)
            turns = (*conversation.turns, question, answer)
            self._items[conversation_id] = replace(
                conversation, title=title, updated=answer.at, session_id=session_id or conversation.session_id,
                turns=turns, claude_turns=len(turns) if claude_saw_all else conversation.claude_turns,
            )
            self._save()

    def add_imported(self, title: str, created: float, turns: tuple[Turn, ...]) -> Conversation:
        """A conversation held elsewhere (the backup Brain), with no Claude session here."""
        updated = max((turn.at for turn in turns), default=created)
        conversation = Conversation(uuid.uuid4().hex[:12], title_for(title) or "Imported chat", created, updated,
                                    turns=turns)
        with self._lock:
            self._items[conversation.id] = conversation
            self._save()
        return conversation

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            found = self._items.pop(conversation_id, None) is not None
            if found:
                self._save()
            return found
