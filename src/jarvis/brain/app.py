"""The Brain API: serves the web interface and the data behind its panels.

It listens on the desktop's Tailscale address only (JARVIS_HOST), so only the user's own devices reach it,
and every /api call needs the bearer token from `.env`. The page itself holds no data and needs no token:
it asks for the token once and keeps it in the browser.

The same server runs as the backup on the laptop (JARVIS_ROLE=backup) on 127.0.0.1, for when the desktop
is off: Claude only, no job-finder data, memory read but never written. The page served there tries the
desktop first (CORS lets it), and when the desktop is back it moves the backup's chats there
(`/api/conversations/import`), where one Sonnet run reviews them for memory.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvis.brain import inbox, status, tasks
from jarvis.brain.config import ConfigError, Settings
from jarvis.brain.conversations import ConversationStore, Turn
from jarvis.brain.local import OllamaChat

ROOT = Path(__file__).resolve().parents[3]
WEB_INDEX = ROOT / "web" / "index.html"

StatusSource = Callable[[], status.Status]
InboxSource = Callable[[], list[inbox.InboxItem]]
AnswerSink = Callable[[str, str], bool]  # (job key, answer name) -> saved


class AnswerRequest(BaseModel):
    key: str
    answer: str


def live_status(settings: Settings) -> StatusSource:
    if settings.backup:
        return lambda: status.Status(
            checks=[status.Check("Laptop (backup Brain)", True, "online"), status.tailscale_check()],
            last_run=None, open_jobs=None, phases=status.roadmap_phases(status.read_text(settings.roadmap_path)),
        )

    def collect() -> status.Status:
        log = status.read_text(settings.jobfinder_dir / "data" / "logs" / "daily.log")
        return status.Status(
            checks=[
                status.Check("Desktop (Brain)", True, "online"),
                status.tailscale_check(),
                status.service_check("SSH", "sshd"),
                status.ollama_check(),
            ],
            last_run=status.last_daily_run(log),
            open_jobs=_open_jobs(settings.jobfinder_dir),
            phases=status.roadmap_phases(status.read_text(settings.roadmap_path)),
        )
    return collect


def _open_jobs(jobfinder_dir: Path) -> int | None:
    from jobfinder.storage import JobStore

    db = jobfinder_dir / "data" / "jobs.db"
    if not db.exists():
        return None
    store = JobStore.open(db)
    try:
        return store.count(include_closed=False)
    finally:
        store.close()


class AskRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None


class ImportedTurn(BaseModel):
    role: str = Field(pattern="^(user|jarvis)$")
    text: str = Field(max_length=200_000)
    at: float
    model: str = Field(default="", max_length=20)
    seconds: float | None = None
    ok: bool = True


class ImportedChat(BaseModel):
    title: str = Field(max_length=200)
    created: float
    messages: list[ImportedTurn] = Field(max_length=400)


class ImportRequest(BaseModel):
    chats: list[ImportedChat] = Field(max_length=50)


def create_app(
    token: str,
    status_source: StatusSource,
    inbox_source: InboxSource,
    answer_sink: AnswerSink,
    task_queue: tasks.TaskQueue,
    role: str = "desktop",
    primary_url: str = "",
    cors_origins: Sequence[str] = (),
    review_imports: bool = True,
) -> FastAPI:
    app = FastAPI(title="Jarvis Brain", docs_url=None, redoc_url=None, openapi_url=None)
    if cors_origins:
        # Lets the backup page on the laptop call this Brain; every /api route still needs the token.
        app.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-store"})

    # What lets a browser install the page as an app: a name and an icon, no data.
    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(WEB_INDEX.parent / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icon.svg", include_in_schema=False)
    def icon() -> FileResponse:
        return FileResponse(WEB_INDEX.parent / "icon.svg", media_type="image/svg+xml")

    @app.get("/api/role", dependencies=[Depends(require_token)])
    def get_role() -> dict[str, str]:
        return {"role": role, "primary": primary_url}

    @app.get("/api/status", dependencies=[Depends(require_token)])
    def get_status() -> dict[str, object]:
        return status_source().to_json()

    @app.get("/api/inbox", dependencies=[Depends(require_token)])
    def get_inbox() -> list[dict[str, object]]:
        return inbox.to_json(inbox_source())

    @app.post("/api/inbox/answer", dependencies=[Depends(require_token)])
    def post_answer(request: AnswerRequest) -> dict[str, bool]:
        if request.answer not in inbox.ANSWER_NAMES:
            raise HTTPException(status_code=400, detail=f"answer must be one of {sorted(inbox.ANSWER_NAMES)}")
        if not answer_sink(request.key, request.answer):
            raise HTTPException(status_code=404, detail="no such job")
        return {"saved": True}

    @app.post("/api/tasks", dependencies=[Depends(require_token)])
    def post_task(request: AskRequest) -> dict[str, object]:
        prompt = request.prompt.strip()
        if not prompt or len(prompt) > tasks.MAX_PROMPT_CHARS:
            raise HTTPException(status_code=400, detail=f"prompt must be 1 to {tasks.MAX_PROMPT_CHARS} characters")
        # Claude Code reads a leading "/" as one of its own commands; only the model choice is allowed.
        _, rest = tasks.split_override(prompt)
        if not rest.strip() or rest.lstrip().startswith("/"):
            raise HTTPException(status_code=400,
                                detail="start with /haiku, /sonnet, /opus or /local, or with no slash")
        task = task_queue.submit(prompt, request.conversation_id)
        if task is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return task.to_json()

    @app.get("/api/conversations", dependencies=[Depends(require_token)])
    def list_conversations() -> list[dict[str, object]]:
        return [conversation.summary() for conversation in task_queue.conversations.recent()]

    @app.post("/api/conversations/import", dependencies=[Depends(require_token)])
    def import_conversations(request: ImportRequest) -> dict[str, object]:
        """Chats from the backup Brain, added as new chats; then one run reviews them for memory."""
        if role != "desktop":
            raise HTTPException(status_code=404, detail="only the desktop Brain takes imported chats")
        imported: list[tuple[str, tuple[Turn, ...]]] = []
        ids = []
        for chat in request.chats:
            turns = tuple(Turn(**turn.model_dump()) for turn in chat.messages)
            if not turns:
                continue
            ids.append(task_queue.conversations.add_imported(chat.title, chat.created, turns).id)
            imported.append((chat.title, turns))
        review = None
        if imported and review_imports:
            review = task_queue.submit(tasks.backup_review_prompt(imported), title="Memory review: chats from the backup")
        return {"imported": ids, "review": review.to_json() if review else None}

    @app.get("/api/conversations/{conversation_id}", dependencies=[Depends(require_token)])
    def get_conversation(conversation_id: str) -> dict[str, object]:
        conversation = task_queue.conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return conversation.to_json()

    @app.delete("/api/conversations/{conversation_id}", dependencies=[Depends(require_token)])
    def delete_conversation(conversation_id: str) -> dict[str, bool]:
        if not task_queue.conversations.delete(conversation_id):
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"deleted": True}

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
    def get_task(task_id: str) -> dict[str, object]:
        task = task_queue.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return task.to_json()

    return app


def _task_queue(settings: Settings) -> tasks.TaskQueue:
    """Claude Code works from the vault when there is one, with the code projects added beside it."""
    projects = ROOT.parent
    vault = settings.vault_dir
    memory = vault / tasks.MEMORY_DIR if vault else None
    agent = tasks.ClaudeCode(
        workdir=vault or projects,
        extra_dirs=(projects,) if vault else (),
        instructions=tasks.system_prompt(vault, projects, backup=settings.backup),
        oauth_token=settings.claude_oauth_token,
        memory=memory,
        memory_writable=not settings.backup,
    )
    local = OllamaChat(settings.local_model, memory) if settings.local_model else None
    data = settings.data_dir
    return tasks.TaskQueue(agent, ConversationStore(data / "conversations.json"), log_path=data / "runs.jsonl",
                           local=local)


def main() -> int:
    import uvicorn

    try:
        settings = Settings.load(ROOT / ".env")
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    app = create_app(
        settings.token,
        live_status(settings),
        (lambda: []) if settings.backup else lambda: inbox.job_matches(settings.jobfinder_dir),
        (lambda key, name: False) if settings.backup else lambda key, name: inbox.answer(settings.jobfinder_dir, key, name),
        _task_queue(settings),
        role=settings.role,
        primary_url=settings.primary_url,
        cors_origins=settings.cors_origins,
        review_imports=settings.vault_dir is not None,
    )
    print(f"Jarvis Brain ({settings.role}) on http://{settings.host}:{settings.port}/")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
