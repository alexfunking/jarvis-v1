"""Conversations with Jarvis: each one is its own Claude Code session, continued with `--resume`.

`claude_turns` counts the turns that session has seen. Turns it missed (answered by the local model, or
imported from the backup Brain, whose sessions stay on the laptop) are handed to Claude with the next
request, so the conversation reads the same whichever model answered. `reviewed_turns` counts the turns
already reviewed for memory (see `tasks.TaskQueue.review_idle`).

The turns are kept in one JSON file, so the interface can list the conversations and show their history
after a restart. With `transcripts` set, every chat is also written there as a Markdown file that Jarvis
can search when the user refers to an earlier conversation. Both hold what the user asked, so they live
under the ignored `data/` folder and never in git.
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
    reviewed_turns: int = 0  # how many of `turns` were reviewed for memory

    def unseen_by_claude(self) -> tuple[Turn, ...]:
        """The turns the Claude session has not seen; all of them when there is no session yet."""
        return self.turns[self.claude_turns:] if self.session_id else self.turns

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "title": self.title, "updated": self.updated, "turns": len(self.turns)}

    def to_json(self) -> dict[str, object]:
        return {**self.summary(), "messages": [asdict(turn) for turn in self.turns]}

    def transcript_name(self) -> str:
        return f"{time.strftime('%Y-%m-%d', time.localtime(self.created))} {self.id}.md"

    def to_markdown(self) -> str:
        day = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created))
        lines = [f"# {self.title}", "", f"Started {day}, chat id {self.id}.", ""]
        for turn in self.turns:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(turn.at))
            who = "User" if turn.role == "user" else f"Jarvis ({turn.model})" if turn.model else "Jarvis"
            failed = " (failed)" if not turn.ok else ""
            lines += [f"## {who}, {when}{failed}", "", turn.text, ""]
        return "\n".join(lines)


def title_for(prompt: str) -> str:
    line = " ".join(prompt.split())
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


class ConversationStore:
    """All conversations in memory, written to `path` after every change (None keeps them in memory only)."""

    def __init__(self, path: Path | None = None, transcripts: Path | None = None) -> None:
        self._path = path
        self.transcripts = transcripts
        self._lock = threading.Lock()
        self._items: dict[str, Conversation] = self._load()
        if transcripts is not None:
            for conversation in self._items.values():
                if not (transcripts / conversation.transcript_name()).exists():
                    self._write_transcript(conversation)

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
                if "claude_turns" not in entry and entry.get("session_id"):
                    entry["claude_turns"] = len(turns)  # saved before the count existed: its session saw them all
                conversation = Conversation(**entry, turns=turns)
            except TypeError:
                continue
            items[conversation.id] = conversation
        return items

    def _save(self, changed: Conversation | None = None) -> None:
        if changed is not None:
            self._write_transcript(changed)
        if self._path is None:
            return
        data = [asdict(conversation) for conversation in self._items.values()]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def _write_transcript(self, conversation: Conversation) -> None:
        if self.transcripts is None or not conversation.turns:
            return
        try:
            self.transcripts.mkdir(parents=True, exist_ok=True)
            (self.transcripts / conversation.transcript_name()).write_text(conversation.to_markdown(),
                                                                          encoding="utf-8")
        except OSError:
            pass

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
            updated = replace(
                conversation, title=title, updated=answer.at, session_id=session_id or conversation.session_id,
                turns=turns, claude_turns=len(turns) if claude_saw_all else conversation.claude_turns,
            )
            self._items[conversation_id] = updated
            self._save(updated)

    def add_imported(self, title: str, created: float, turns: tuple[Turn, ...]) -> Conversation:
        """A conversation held elsewhere (the backup Brain), with no Claude session here."""
        updated = max((turn.at for turn in turns), default=created)
        conversation = Conversation(uuid.uuid4().hex[:12], title_for(title) or "Imported chat", created, updated,
                                    turns=turns)
        with self._lock:
            self._items[conversation.id] = conversation
            self._save(conversation)
        return conversation

    def unreviewed(self, idle_since: float) -> list[Conversation]:
        """Chats with turns not yet reviewed for memory and no new turn after `idle_since`, oldest first."""
        with self._lock:
            items = [c for c in self._items.values() if len(c.turns) > c.reviewed_turns and c.updated <= idle_since]
        return sorted(items, key=lambda c: c.updated)

    def mark_reviewed(self, conversation_id: str, turns: int) -> None:
        with self._lock:
            conversation = self._items.get(conversation_id)
            if conversation is None:
                return
            self._items[conversation_id] = replace(conversation, reviewed_turns=max(turns, conversation.reviewed_turns))
            self._save()

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            found = self._items.pop(conversation_id, None)
            if found is not None:
                self._save()
                if self.transcripts is not None:
                    (self.transcripts / found.transcript_name()).unlink(missing_ok=True)
            return found is not None
