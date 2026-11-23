from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from test_brain import TOKEN, _client

from jarvis.brain import tasks
from jarvis.brain.conversations import ConversationStore

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@dataclass
class FakeAgent:
    """Records each call; routes everything to `routed` and answers with `answer`."""

    routed: str = "haiku"
    answer: tasks.RunResult = field(default_factory=lambda: tasks.RunResult("an answer", True, 0.02, 10, 3))
    routes: list[str] = field(default_factory=list)
    asks: list[tuple[str, str, bool, str]] = field(default_factory=list)

    def route(self, prompt: str) -> str:
        self.routes.append(prompt)
        return self.routed

    def ask(self, prompt: str, session_id: str, resume: bool, model: str) -> tasks.RunResult:
        self.asks.append((prompt, session_id, resume, model))
        return self.answer


def run(queue: tasks.TaskQueue, prompt: str, conversation_id: str | None = None) -> tasks.Task:
    task = queue.submit(prompt, conversation_id, start=False)
    assert task is not None
    queue.run(task.id)
    done = queue.get(task.id)
    assert done is not None
    return done


def test_parse_claude_output() -> None:
    line = json.dumps({"result": "56", "is_error": False, "total_cost_usd": 0.01,
                       "usage": {"input_tokens": 120, "output_tokens": 4}})
    assert tasks.parse_output(line) == tasks.RunResult("56", True, 0.01, 120, 4)
    not_logged_in = tasks.parse_output('{"result": "Not logged in", "is_error": true}')
    assert not not_logged_in.ok and not_logged_in.answer == "Not logged in"
    assert not tasks.parse_output("crash").ok


def test_route_answer_and_override() -> None:
    assert tasks.parse_route("Opus.") == "opus"
    assert tasks.parse_route("I think haiku") == "haiku"
    assert tasks.parse_route("no idea") == tasks.DEFAULT_MODEL
    assert tasks.split_override("/Opus prove it") == ("opus", "prove it")
    assert tasks.split_override("/opusx hi") == (None, "/opusx hi")
    assert tasks.split_override("plain") == (None, "plain")


def test_a_conversation_starts_a_session_then_resumes_it(tmp_path: Path) -> None:
    agent = FakeAgent()
    store = ConversationStore(tmp_path / "conversations.json")
    queue = tasks.TaskQueue(agent, store)
    first = run(queue, "first question")
    second = run(queue, "follow up", first.conversation_id)
    (_, session, resumed, _), (_, session_again, resumed_again, _) = agent.asks
    assert (resumed, resumed_again) == (False, True) and session == session_again
    assert second.conversation_id == first.conversation_id and second.model == "haiku"
    # A new chat is a new session.
    run(queue, "something else")
    assert agent.asks[2][1] != session and agent.asks[2][2] is False
    # History and titles survive a restart.
    reloaded = ConversationStore(tmp_path / "conversations.json")
    conversation = reloaded.get(first.conversation_id)
    assert conversation is not None and conversation.title == "first question"
    assert [turn.role for turn in conversation.turns] == ["user", "jarvis", "user", "jarvis"]
    assert conversation.turns[1].model == "haiku"


def test_override_skips_the_router() -> None:
    agent = FakeAgent()
    task = run(tasks.TaskQueue(agent, ConversationStore()), "/opus prove it")
    assert agent.routes == [] and agent.asks[0][0] == "prove it" and task.model == "opus"


def test_a_failed_first_turn_keeps_no_session() -> None:
    agent = FakeAgent(answer=tasks.RunResult("Not logged in", False))
    store = ConversationStore()
    queue = tasks.TaskQueue(agent, store)
    task = run(queue, "hi")
    assert task.state == "failed" and task.answer == "Not logged in"
    conversation = store.get(task.conversation_id)
    assert conversation is not None and conversation.session_id == ""
    run(queue, "hi again", task.conversation_id)
    assert agent.asks[1][2] is False  # starts the session afresh


def test_log_has_the_model_and_no_prompt_text(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    ticks = iter([100.0, 101.0, 103.5, 104.0])
    queue = tasks.TaskQueue(FakeAgent(), ConversationStore(), log, clock=lambda: next(ticks))
    task = run(queue, "secret question")
    assert task.seconds == 2.5
    entry = json.loads(log.read_text(encoding="utf-8"))
    assert (entry["model"], entry["output_tokens"], entry["prompt_chars"]) == ("haiku", 3, 15)
    assert "secret" not in log.read_text(encoding="utf-8")
    assert "prompt" not in task.to_json()


def test_system_prompt_is_safe_on_a_cmd_line() -> None:
    text = tasks.system_prompt(Path(r"C:\Vault"), Path(r"C:\Projects"))
    assert "CLAUDE_INDEX.md" in text and tasks.MEMORY_DIR in text
    assert not set(text) & set('"&|<>^%!')


def test_memory_goes_in_front_of_the_first_request_only(tmp_path: Path) -> None:
    memory = tmp_path / tasks.MEMORY_DIR
    memory.mkdir(parents=True)
    (memory / tasks.MEMORY_INDEX).write_text("## עליי\n- סטודנט", encoding="utf-8")
    seen: list[str] = []

    @dataclass(frozen=True)
    class Recording(tasks.ClaudeCode):
        def _run(self, args: Sequence[str], stdin: str, timeout: int) -> tasks.RunResult:
            seen.append(stdin)
            return tasks.RunResult("ok", True)

    agent = Recording(workdir=tmp_path, memory=memory, exe="claude.exe")
    agent.ask("first", "s1", False, "sonnet")
    agent.ask("again", "s1", True, "sonnet")
    assert "<jarvis_memory>\n## עליי" in seen[0] and seen[0].endswith("which is what you answer:\nfirst")
    assert seen[1] == "again"
    assert tasks.with_memory("q", "  ") == "q"


def test_only_the_memory_folder_is_writable() -> None:
    with_memory = tasks.ClaudeCode(workdir=Path("v"), memory=Path("v") / tasks.MEMORY_DIR, exe="c").ask_args(
        "s", False, "opus")
    assert with_memory[with_memory.index("--tools") + 1] == tasks.MEMORY_TOOLS
    assert with_memory[with_memory.index("--allowedTools") + 1] == "Edit(02_Projects/Jarvis/Memory/**)"
    assert with_memory[with_memory.index("--permission-mode") + 1] == "dontAsk"
    read_only = tasks.ClaudeCode(workdir=Path("v"), exe="c").ask_args("s", True, "haiku")
    assert read_only[read_only.index("--tools") + 1] == tasks.READ_ONLY_TOOLS
    assert "--allowedTools" not in read_only and read_only[read_only.index("--resume") + 1] == "s"


def test_ask_and_conversation_endpoints() -> None:
    client = _client()
    assert client.post("/api/tasks", json={"prompt": "hi"}).status_code == 401
    for bad in ["  ", "/opus", "/login", "/opus /clear"]:
        assert client.post("/api/tasks", json={"prompt": bad}, headers=AUTH).status_code == 400
    assert client.post("/api/tasks", json={"prompt": "hi", "conversation_id": "nope"}, headers=AUTH).status_code == 404
    task = client.post("/api/tasks", json={"prompt": "hi"}, headers=AUTH).json()
    body: dict[str, object] = {}
    for _ in range(100):
        body = client.get(f"/api/tasks/{task['id']}", headers=AUTH).json()
        if body["state"] == "done":
            break
        time.sleep(0.02)
    assert (body["answer"], body["model"]) == ("echo: hi", "sonnet")
    chats = client.get("/api/conversations", headers=AUTH).json()
    assert chats[0]["id"] == task["conversation_id"] and chats[0]["title"] == "hi"
    chat = client.get(f"/api/conversations/{task['conversation_id']}", headers=AUTH).json()
    assert [m["text"] for m in chat["messages"]] == ["hi", "echo: hi"]
    assert client.delete(f"/api/conversations/{task['conversation_id']}", headers=AUTH).json() == {"deleted": True}
    assert client.get("/api/conversations", headers=AUTH).json() == []
    assert client.get("/api/tasks/nope", headers=AUTH).status_code == 404


def test_vault_is_found_above_the_roadmap(tmp_path: Path) -> None:
    from jarvis.brain.config import Settings

    (tmp_path / "Vault" / "02_Projects" / "Jarvis").mkdir(parents=True)
    (tmp_path / "Vault" / "CLAUDE_INDEX.md").write_text("map", encoding="utf-8")
    roadmap = tmp_path / "Vault" / "02_Projects" / "Jarvis" / "Roadmap.md"
    env = tmp_path / ".env"
    env.write_text(f"JARVIS_TOKEN={TOKEN}\nROADMAP_PATH={roadmap}\n", encoding="utf-8")
    assert Settings.load(env, environ={}).vault_dir == tmp_path / "Vault"
