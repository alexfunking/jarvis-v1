"""Ask Jarvis: each request runs headless Claude Code (`claude -p`) on the desktop, one at a time.

- Every conversation is its own Claude Code session: the first turn starts it with `--session-id`, later
  turns continue it with `--resume`, so a new chat starts clean and an old one keeps its context.
- A quick Haiku call picks the model for each request (Haiku, Sonnet or Opus); starting a request with
  `/haiku`, `/sonnet` or `/opus` picks it by hand.
- The agent reads the user's vault (its working directory) and the code projects, and writes in one place
  only: the vault's memory folder (`MEMORY_DIR`). Its tools are Read, Grep, Glob, Edit and Write, one
  allow rule covers edits under that folder, and `dontAsk` refuses every other edit without asking.
- Memory: the index note `Jarvis Memory.md` goes in front of the first request of each chat; a resumed
  chat already holds it.
- `claude.exe` is called directly (npm's `claude.cmd` only forwards to it), so no cmd.exe parses the
  arguments. Requests still go in on stdin, which has no length limit; the fixed arguments stay free of
  cmd.exe's special characters for the case where only a `.cmd` is found.
- Every finished run is appended to a JSON-lines log with its model, duration, cost and tokens, without the
  text of the request.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Final, Protocol

from jarvis.brain.conversations import ConversationStore, Turn

MODELS: Final = ("haiku", "sonnet", "opus")
DEFAULT_MODEL: Final = "sonnet"
READ_ONLY_TOOLS: Final = "Read,Grep,Glob"
MEMORY_TOOLS: Final = "Read,Grep,Glob,Edit,Write"
MEMORY_DIR: Final = "02_Projects/Jarvis/Memory"  # inside the vault
MEMORY_INDEX: Final = "Jarvis Memory.md"
MAX_MEMORY_CHARS: Final = 12000
TIMEOUT_SECONDS: Final = 600
ROUTER_TIMEOUT_SECONDS: Final = 60
MAX_PROMPT_CHARS: Final = 4000
KEEP_TASKS: Final = 50
OVERRIDE_RE: Final = re.compile(r"^/(haiku|sonnet|opus)\b\s*", re.IGNORECASE)

ROUTER_PROMPT: Final = """\
You choose which Claude model answers a request sent to a personal assistant. Reply with one word only.
haiku: greetings, quick facts, short lookups, a status question answered from one file.
sonnet: most work: explaining, summarizing, reading several notes or files, writing, planning, ordinary code questions,
and any request to remember, update or forget something.
opus: hard problems: math or proofs, deep technical or design decisions, debugging tricky code, long multi-step analysis.
When unsure between two, choose the stronger one.

Request:
"""


def system_prompt(vault: Path | None, projects: Path) -> str:
    """Jarvis's standing instructions. No quotes, ampersands or percent signs: it goes on a cmd.exe line."""
    parts = [
        "You are Jarvis, the personal assistant of the user, answering a request typed into the Jarvis interface.",
        "You run on the user's home desktop. You cannot run commands, and you change no file outside your memory.",
    ]
    if vault is not None:
        parts.append(
            f"The user's Obsidian vault is {vault}. Its file CLAUDE_INDEX.md is the map of the vault and the rules "
            "for working with it: read it before answering anything about the user's notes, studies, projects or "
            "plans, then open only the notes you need. Quote note names so the user can find them."
        )
        parts.append(
            f"Your long-term memory is the folder {MEMORY_DIR} in the vault, the only place you may write. "
            f"Its note {MEMORY_INDEX} is a short index given to you at the start of each chat. "
            "Write to memory when the user asks you to remember something, or when a lasting fact, preference "
            "or decision about the user comes up. Keep the index under about 60 lines, one line per item under "
            "its heading. Longer material goes in a topic note in the same folder, linked from the index with a "
            "wiki link. Write in Hebrew and follow the vault rules: YAML frontmatter with type, tags and created, "
            "callouts and wiki links, and set updated to the current date. Change or remove an item that turns "
            "out wrong instead of adding a contradicting one. Never store passwords, tokens or other secrets. "
            "When you changed memory, end the answer with one line starting with Memory and saying what changed."
        )
    parts.append(
        f"The code projects are in {projects}: job-finder is the job search radar, jarvis is this assistant."
    )
    parts.append(
        "Answer in the language of the request. Be concise. Plain text, short paragraphs or lists, no tables."
    )
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class RunResult:
    answer: str
    ok: bool
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""


def parse_output(stdout: str) -> RunResult:
    """The result line of `claude -p --output-format json`."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        text = stdout.strip()[-500:]
        return RunResult(f"Claude Code gave no readable answer. {text}".strip(), ok=False)
    if not isinstance(data, dict):
        return RunResult("Claude Code gave no readable answer.", ok=False)
    raw_usage = data.get("usage")
    usage: dict[str, object] = raw_usage if isinstance(raw_usage, dict) else {}
    cost = data.get("total_cost_usd")
    return RunResult(
        answer=str(data.get("result") or "").strip(),
        ok=not data.get("is_error", False),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
    )


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def split_override(prompt: str) -> tuple[str | None, str]:
    """`/opus why…` -> ("opus", "why…"); a request without a prefix -> (None, request)."""
    match = OVERRIDE_RE.match(prompt)
    if match is None:
        return None, prompt
    return match[1].lower(), prompt[match.end():]


def parse_route(answer: str) -> str:
    words = re.findall(r"[a-z]+", answer.lower())
    return next((word for word in words if word in MODELS), DEFAULT_MODEL)


def find_claude() -> str | None:
    """The `claude` executable; for npm's `claude.cmd` shim, the `claude.exe` it forwards to."""
    found = shutil.which("claude")
    if found and found.lower().endswith(".cmd"):
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return found


def with_memory(prompt: str, memory: str) -> str:
    """The first request of a chat, with the memory index in front of it."""
    if not memory.strip():
        return prompt
    return (
        "Background from your memory note. Use it, but do not reply to it:\n"
        f"<jarvis_memory>\n{memory.strip()[:MAX_MEMORY_CHARS]}\n</jarvis_memory>\n\n"
        f"The user's request, which is what you answer:\n{prompt}"
    )


@dataclass(frozen=True, slots=True)
class ClaudeCode:
    """The installed `claude` CLI, run headless. With `memory` set, the agent may write in that folder only."""

    workdir: Path
    extra_dirs: tuple[Path, ...] = ()
    instructions: str = ""
    oauth_token: str = ""
    memory: Path | None = None  # the memory folder; must be workdir / MEMORY_DIR
    exe: str | None = field(default_factory=find_claude)

    def ask_args(self, session_id: str, resume: bool, model: str) -> list[str]:
        args = ["--model", model, "--permission-mode", "dontAsk"]
        if self.memory is not None:
            args += ["--tools", MEMORY_TOOLS, "--allowedTools", f"Edit({MEMORY_DIR}/**)"]
        else:
            args += ["--tools", READ_ONLY_TOOLS]
        args += ["--resume", session_id] if resume else ["--session-id", session_id]
        if self.instructions:
            args += ["--append-system-prompt", self.instructions]
        for directory in self.extra_dirs:
            args += ["--add-dir", str(directory)]
        return args

    def read_memory(self) -> str:
        if self.memory is None:
            return ""
        try:
            return (self.memory / MEMORY_INDEX).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _run(self, args: Sequence[str], stdin: str, timeout: int) -> RunResult:
        if self.exe is None:
            return RunResult("The claude command is not installed on the desktop.", ok=False)
        env = dict(os.environ)
        if self.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        try:
            done = subprocess.run(
                [self.exe, "-p", "--output-format", "json", *args], input=stdin.encode("utf-8"),
                capture_output=True, cwd=self.workdir, env=env, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return RunResult(f"No answer within {timeout // 60} minutes; the request was stopped.", ok=False)
        except OSError as exc:
            return RunResult(f"Claude Code could not start: {exc}", ok=False)
        return parse_output(done.stdout.decode("utf-8", errors="replace"))

    def route(self, prompt: str) -> str:
        """The model for a request, chosen by Haiku with no tools; Sonnet when that call fails."""
        result = self._run(
            ["--model", "haiku", "--tools", "", "--no-session-persistence"],
            ROUTER_PROMPT + prompt[:2000], ROUTER_TIMEOUT_SECONDS,
        )
        return parse_route(result.answer) if result.ok else DEFAULT_MODEL

    def ask(self, prompt: str, session_id: str, resume: bool, model: str) -> RunResult:
        stdin = prompt if resume else with_memory(prompt, self.read_memory())
        return replace(self._run(self.ask_args(session_id, resume, model), stdin, TIMEOUT_SECONDS), model=model)


class Agent(Protocol):
    """What a task needs: a model choice and a run inside a conversation's session. ClaudeCode is one."""

    def route(self, prompt: str) -> str: ...

    def ask(self, prompt: str, session_id: str, resume: bool, model: str) -> RunResult: ...


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    conversation_id: str
    prompt: str
    state: str  # "queued", "running", "done" or "failed"
    created: float
    answer: str = ""
    model: str = ""
    seconds: float | None = None

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        del data["prompt"]
        return data


class TaskQueue:
    """Requests run one at a time on worker threads; the newest KEEP_TASKS stay readable."""

    def __init__(
        self,
        agent: Agent,
        conversations: ConversationStore,
        log_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._agent = agent
        self.conversations = conversations
        self._log_path = log_path
        self._clock = clock
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._lock = threading.Lock()
        self._turn = threading.Lock()  # held by the task that is running

    def submit(self, prompt: str, conversation_id: str | None = None, start: bool = True) -> Task | None:
        """Queue a request; a missing conversation id starts a new conversation, an unknown one gives None."""
        if conversation_id is None:
            conversation_id = self.conversations.create(prompt).id
        elif self.conversations.get(conversation_id) is None:
            return None
        task = Task(uuid.uuid4().hex[:12], conversation_id, prompt, "queued", self._clock())
        with self._lock:
            self._tasks[task.id] = task
            while len(self._tasks) > KEEP_TASKS:
                self._tasks.popitem(last=False)
        if start:
            threading.Thread(target=self.run, args=(task.id,), daemon=True).start()
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _set(self, task: Task) -> Task:
        with self._lock:
            if task.id in self._tasks:
                self._tasks[task.id] = task
        return task

    def run(self, task_id: str) -> None:
        with self._turn:
            task = self.get(task_id)
            conversation = self.conversations.get(task.conversation_id) if task else None
            if task is None or conversation is None:
                return
            task = self._set(replace(task, state="running"))
            started = self._clock()
            model, prompt = split_override(task.prompt)
            if model is None:
                model = self._agent.route(prompt)
            session_id = conversation.session_id or str(uuid.uuid4())
            result = self._agent.ask(prompt or task.prompt, session_id, bool(conversation.session_id), model)
            seconds = round(self._clock() - started, 1)
            answer = result.answer or ("Done, with no text in the answer." if result.ok else "Failed with no message.")
            self._set(replace(task, state="done" if result.ok else "failed", answer=answer, model=model,
                              seconds=seconds))
            self.conversations.add_exchange(
                conversation.id,
                Turn("user", task.prompt, task.created),
                Turn("jarvis", answer, self._clock(), model=model, seconds=seconds, ok=result.ok),
                # A failed first turn may not have created the session, so it is only kept after a success.
                session_id if result.ok else conversation.session_id,
            )
            self._log(task, result, model, seconds)

    def _log(self, task: Task, result: RunResult, model: str, seconds: float) -> None:
        if self._log_path is None:
            return
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(task.created)),
            "model": model, "ok": result.ok, "seconds": seconds, "cost_usd": result.cost_usd,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "prompt_chars": len(task.prompt),
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(entry) + "\n")
        except OSError:
            pass
