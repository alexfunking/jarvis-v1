"""The local model, the history hand-off between models, and the backup Brain (N6)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_ask import FakeAgent, run
from test_brain import TOKEN, EchoAgent

from jarvis.brain import status, tasks
from jarvis.brain.app import create_app
from jarvis.brain.config import ConfigError, Settings
from jarvis.brain.conversations import ConversationStore, Turn
from jarvis.brain.local import OllamaChat

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@dataclass
class FakeLocal:
    up: bool = True
    chats: list[tuple[str, int]] = field(default_factory=list)

    def available(self) -> bool:
        return self.up

    def chat(self, prompt: str, history: Sequence[Turn]) -> tasks.RunResult:
        self.chats.append((prompt, len(history)))
        return tasks.RunResult(f"local: {prompt}", True, 0.0, model="local")


def test_the_router_may_pick_the_local_model_only_when_it_is_up() -> None:
    assert "local:" in tasks.router_prompt(True) and "local:" not in tasks.router_prompt(False)
    assert tasks.parse_route("local", local=True) == "local"
    assert tasks.parse_route("local", local=False) == tasks.DEFAULT_MODEL
    assert tasks.split_override("/local hi") == ("local", "hi")


def test_a_local_answer_keeps_the_claude_session_untouched() -> None:
    agent, local = FakeAgent(routed="local"), FakeLocal()
    store = ConversationStore()
    task = run(tasks.TaskQueue(agent, store, local=local), "rephrase this")
    assert (task.answer, task.model) == ("local: rephrase this", "local")
    assert agent.asks == [] and local.chats == [("rephrase this", 0)]
    conversation = store.get(task.conversation_id)
    assert conversation is not None and conversation.session_id == "" and conversation.claude_turns == 0


def test_local_becomes_haiku_when_no_local_model_answers() -> None:
    agent = FakeAgent(routed="local")
    queue = tasks.TaskQueue(agent, ConversationStore(), local=FakeLocal(up=False))
    assert run(queue, "rephrase this").model == "haiku"
    assert run(tasks.TaskQueue(FakeAgent(), ConversationStore()), "/local hi").model == "haiku"


def test_claude_gets_the_turns_its_session_missed() -> None:
    agent, local = FakeAgent(), FakeLocal()
    store = ConversationStore()
    queue = tasks.TaskQueue(agent, store, local=local)
    first = run(queue, "/local my name is Dana")
    run(queue, "/sonnet what is my name?", first.conversation_id)
    prompt, session, resumed, model = agent.asks[0]
    assert "User: my name is Dana" in prompt and "Jarvis: local: my name is Dana" in prompt
    assert prompt.endswith("The user's new request:\nwhat is my name?") and not resumed and model == "sonnet"
    run(queue, "/local thanks", first.conversation_id)
    run(queue, "/sonnet and again?", first.conversation_id)
    prompt, session_again, resumed, _ = agent.asks[1]
    assert resumed and session_again == session
    assert "thanks" in prompt and "Dana" not in prompt  # only what the session has not seen
    run(queue, "/sonnet plain", first.conversation_id)
    assert agent.asks[2][0] == "plain"
    conversation = store.get(first.conversation_id)
    assert conversation is not None and conversation.claude_turns == len(conversation.turns) == 10


def test_history_skips_failed_answers_and_keeps_the_newest_text() -> None:
    turns = [Turn("user", "q", 1.0), Turn("jarvis", "boom", 2.0, ok=False)]
    assert "boom" not in tasks.with_history("next", turns)
    assert tasks.with_history("next", []) == "next"
    long = [Turn("user", "x" * (tasks.MAX_HISTORY_CHARS + 50) + "END", 1.0)]
    assert "END" in tasks.with_history("next", long)


def test_ollama_chat_sends_memory_and_recent_turns(tmp_path: Path) -> None:
    memory = tmp_path / tasks.MEMORY_DIR
    memory.mkdir(parents=True)
    (memory / tasks.MEMORY_INDEX).write_text("- סטודנט", encoding="utf-8")
    sent: list[dict[str, object]] = []

    def post(url: str, body: dict[str, object], timeout: float) -> object:
        sent.append(body)
        return {"message": {"content": " שלום "}, "prompt_eval_count": 40, "eval_count": 5}

    def get(url: str, timeout: float) -> object:
        return {"models": [{"name": "gemma4:latest"}]}

    chat = OllamaChat("gemma4:latest", memory, post=post, get=get)
    assert chat.available() and not OllamaChat("other:1b", get=get).available()
    history = [Turn("user", "hi", 1.0), Turn("jarvis", "hello", 2.0), Turn("jarvis", "boom", 3.0, ok=False)]
    result = chat.chat("מה שלומך?", history)
    assert result == tasks.RunResult("שלום", True, 0.0, 40, 5, "local")
    messages = sent[0]["messages"]
    assert isinstance(messages, list)
    assert "סטודנט" in messages[0]["content"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_ollama_chat_reports_a_dead_server() -> None:
    def down(url: str, *args: object) -> object:
        raise OSError("connection refused")

    chat = OllamaChat(post=down, get=down)
    assert not chat.available()
    result = chat.chat("hi", [])
    assert not result.ok and "connection refused" in result.answer


def test_the_backup_reads_memory_but_cannot_write_it(tmp_path: Path) -> None:
    agent = tasks.ClaudeCode(workdir=Path("v"), memory=Path("v") / tasks.MEMORY_DIR, memory_writable=False, exe="c")
    args = agent.ask_args("s", False, "sonnet")
    assert args[args.index("--tools") + 1] == tasks.READ_ONLY_TOOLS and "--allowedTools" not in args
    text = tasks.system_prompt(Path(r"C:\Vault"), Path(r"C:\Projects"), backup=True)
    assert "backup" in text and "cannot" in text and not set(text) & set('"&|<>^%!')


def _env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(f"JARVIS_TOKEN={TOKEN}\n{text}", encoding="utf-8")
    return env


def test_backup_settings(tmp_path: Path) -> None:
    desktop = Settings.load(_env(tmp_path, ""), environ={})
    assert desktop.role == "desktop" and desktop.local_model == "gemma4:latest"
    assert desktop.data_dir == tmp_path / "data"
    assert desktop.cors_origins == ("http://127.0.0.1:8700", "http://localhost:8700")
    assert Settings.load(_env(tmp_path, "JARVIS_LOCAL_MODEL=off\n"), environ={}).local_model == ""
    backup = Settings.load(_env(tmp_path, "JARVIS_ROLE=backup\nJARVIS_PRIMARY_URL=http://100.1.2.3:8700/\n"),
                           environ={})
    assert backup.backup and backup.primary_url == "http://100.1.2.3:8700"
    assert backup.local_model == "" and backup.cors_origins == ()
    with pytest.raises(ConfigError):
        Settings.load(_env(tmp_path, "JARVIS_ROLE=backup\n"), environ={})
    with pytest.raises(ConfigError):
        Settings.load(_env(tmp_path, "JARVIS_ROLE=server\n"), environ={})


def _app(role: str = "desktop", queue: tasks.TaskQueue | None = None) -> TestClient:
    return TestClient(create_app(
        TOKEN, lambda: status.Status([], None, None), lambda: [], lambda key, name: False,
        queue or tasks.TaskQueue(EchoAgent(), ConversationStore()),
        role=role, primary_url="http://100.1.2.3:8700" if role == "backup" else "",
        cors_origins=("http://127.0.0.1:8700",) if role == "desktop" else (),
    ))


def test_role_and_cors() -> None:
    client = _app()
    assert client.get("/api/role").status_code == 401
    assert client.get("/api/role", headers=AUTH).json() == {"role": "desktop", "primary": ""}
    preflight = {"Origin": "http://127.0.0.1:8700", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"}
    allowed = client.options("/api/tasks", headers=preflight)
    assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:8700"
    other = client.options("/api/tasks", headers={**preflight, "Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in other.headers
    # CORS does not open the data: a cross-origin call without the token is still refused.
    assert client.get("/api/status", headers={"Origin": "http://127.0.0.1:8700"}).status_code == 401
    assert _app("backup").get("/api/role", headers=AUTH).json() == {"role": "backup", "primary": "http://100.1.2.3:8700"}


def test_backup_chats_are_imported_and_reviewed_for_memory() -> None:
    store = ConversationStore()
    client = _app(queue=tasks.TaskQueue(EchoAgent(), store))
    now = time.time()
    chat = {"title": "on the train", "created": now, "messages": [
        {"role": "user", "text": "remember I like tea", "at": now},
        {"role": "jarvis", "text": "Saved when the desktop is back.", "at": now + 1, "model": "haiku"},
    ]}
    body = client.post("/api/conversations/import", json={"chats": [chat, {**chat, "messages": []}]},
                       headers=AUTH).json()
    assert len(body["imported"]) == 1 and body["review"]["state"] in ("queued", "running", "done")
    imported = store.get(body["imported"][0])
    assert imported is not None and imported.title == "on the train" and imported.session_id == ""
    assert [t.text for t in imported.turns][0] == "remember I like tea"
    review = store.get(body["review"]["conversation_id"])
    assert review is not None and review.title == "Memory review: chats from the backup"
    bad = {**chat, "messages": [{"role": "system", "text": "x", "at": now}]}
    assert client.post("/api/conversations/import", json={"chats": [bad]}, headers=AUTH).status_code == 422
    assert _app("backup").post("/api/conversations/import", json={"chats": [chat]},
                               headers=AUTH).status_code == 404


def test_review_prompt_holds_every_chat() -> None:
    text = tasks.backup_review_prompt([("a", [Turn("user", "one", 1.0)]), ("b", [Turn("user", "two", 2.0)])])
    assert text.startswith("/sonnet ") and "one" in text and "two" in text and 'title="b"' in text


def test_a_chat_title_leaves_out_the_model_prefix() -> None:
    store = ConversationStore()
    task = run(tasks.TaskQueue(FakeAgent(), store), "/opus prove it")
    conversation = store.get(task.conversation_id)
    assert conversation is not None and conversation.title == "prove it"
