"""Chats as searchable Markdown files, and the review of idle chats for memory."""

from __future__ import annotations

from pathlib import Path

from test_ask import FakeAgent, run

from jarvis.brain import tasks
from jarvis.brain.app import review_check
from jarvis.brain.conversations import ConversationStore, Turn


def test_each_chat_is_a_markdown_file_that_follows_it(tmp_path: Path) -> None:
    chats = tmp_path / "chats"
    store = ConversationStore(tmp_path / "conversations.json", transcripts=chats)
    queue = tasks.TaskQueue(FakeAgent(), store)
    task = run(queue, "/sonnet plan my week")
    conversation = store.get(task.conversation_id)
    assert conversation is not None
    file = chats / conversation.transcript_name()
    text = file.read_text(encoding="utf-8")
    assert text.startswith("# plan my week") and "## User" in text and "## Jarvis (haiku" not in text
    assert "/sonnet plan my week" in text and "an answer" in text
    run(queue, "and next week?", task.conversation_id)
    assert "and next week?" in file.read_text(encoding="utf-8")
    # A missing file is written again when the store loads; a deleted chat loses its file.
    file.unlink()
    reloaded = ConversationStore(tmp_path / "conversations.json", transcripts=chats)
    assert file.exists()
    assert reloaded.delete(task.conversation_id) and not file.exists()


def test_an_empty_chat_has_no_file(tmp_path: Path) -> None:
    store = ConversationStore(transcripts=tmp_path)
    store.create("hi")
    assert list(tmp_path.iterdir()) == []


def test_idle_chats_are_reviewed_once_with_only_their_new_turns() -> None:
    now = [1000.0]
    agent = FakeAgent()
    store = ConversationStore()
    queue = tasks.TaskQueue(agent, store, clock=lambda: now[0])
    old = run(queue, "I moved to Haifa")
    assert queue.review_idle(idle_seconds=60) is None  # not idle yet: the chat's time comes from time.time()
    review = queue.review_idle(idle_seconds=0)
    assert review is not None and review.chats == 1 and review.ok and review.summary == "Memory: nothing"
    assert "I moved to Haifa" in agent.reviews[0]
    assert queue.review_idle(idle_seconds=0) is None  # nothing new
    run(queue, "my sister is Noa", old.conversation_id)
    queue.review_idle(idle_seconds=0)
    assert "my sister is Noa" in agent.reviews[1] and "I moved to Haifa" in agent.reviews[1]  # one exchange of context
    for n in range(3):
        run(queue, f"question {n}", old.conversation_id)
    queue.review_idle(idle_seconds=0)
    third = agent.reviews[2]
    assert "question 2" in third and "question 0" in third  # every new turn
    assert "User: my sister is Noa" in third and "User: I moved to Haifa" not in third  # the last reviewed one only


def test_a_failed_review_is_tried_again() -> None:
    agent = FakeAgent(review_answer=tasks.RunResult("Not logged in", False))
    store = ConversationStore()
    queue = tasks.TaskQueue(agent, store)
    run(queue, "remember my exam is on Sunday")
    review = queue.review_idle(idle_seconds=0)
    assert review is not None and not review.ok
    agent.review_answer = tasks.RunResult("Memory: added the exam", True)
    review = queue.review_idle(idle_seconds=0)
    assert review is not None and review.ok and len(agent.reviews) == 2


def test_review_log_line_has_no_chat_text(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    queue = tasks.TaskQueue(FakeAgent(), ConversationStore(), log)
    run(queue, "secret plan")
    queue.review_idle(idle_seconds=0)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert '"kind": "memory-review"' in lines[-1] and '"chats": 1' in lines[-1]
    assert "secret" not in log.read_text(encoding="utf-8")


def test_the_review_run_writes_memory_and_keeps_no_session() -> None:
    seen: list[list[str]] = []

    class Recording(tasks.ClaudeCode):
        def _run(self, args: object, stdin: str, timeout: int) -> tasks.RunResult:
            assert isinstance(args, list)
            seen.append(args)
            return tasks.RunResult("Memory: nothing", True)

    agent = Recording(workdir=Path("v"), memory=Path("v") / tasks.MEMORY_DIR, exe="c")
    assert agent.review("chats").model == tasks.REVIEW_MODEL
    args = seen[0]
    assert "--no-session-persistence" in args and "--session-id" not in args and "--resume" not in args
    assert args[args.index("--tools") + 1] == tasks.MEMORY_TOOLS
    assert args[args.index("--model") + 1] == "sonnet"


def test_system_prompt_points_to_the_chats() -> None:
    text = tasks.system_prompt(Path(r"C:\Vault"), Path(r"C:\Projects"), chats=Path(r"C:\Projects\jarvis\data\chats"))
    assert r"jarvis\data\chats" in text and not set(text) & set('"&|<>^%!')


def test_review_check_for_the_systems_panel() -> None:
    assert review_check(None).value == "none yet"
    check = review_check(tasks.Review(0.0, 2, False, "x"))
    assert not check.ok and check.value.endswith("2 chats")
    assert "Turn" in repr(Turn)


def test_chats_saved_before_the_counts_existed_are_not_handed_to_claude_again(tmp_path: Path) -> None:
    path = tmp_path / "conversations.json"
    path.write_text('[{"id": "a1", "title": "t", "created": 1.0, "updated": 2.0, "session_id": "s", "turns": ['
                    '{"role": "user", "text": "q", "at": 1.0}, {"role": "jarvis", "text": "a", "at": 2.0}]}]',
                    encoding="utf-8")
    conversation = ConversationStore(path).get("a1")
    assert conversation is not None and conversation.unseen_by_claude() == () and conversation.reviewed_turns == 0
