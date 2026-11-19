"""The Brain API: serves the web interface and the data behind its panels.

It listens on the desktop's Tailscale address only (JARVIS_HOST), so only the user's own devices reach it,
and every /api call needs the bearer token from `.env`. The page itself holds no data and needs no token:
it asks for the token once and keeps it in the browser.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from jarvis.brain import inbox, status
from jarvis.brain.config import ConfigError, Settings

ROOT = Path(__file__).resolve().parents[3]
WEB_INDEX = ROOT / "web" / "index.html"

StatusSource = Callable[[], status.Status]
InboxSource = Callable[[], list[inbox.InboxItem]]


def live_status(settings: Settings) -> StatusSource:
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


def create_app(token: str, status_source: StatusSource, inbox_source: InboxSource) -> FastAPI:
    app = FastAPI(title="Jarvis Brain", docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-store"})

    @app.get("/api/status", dependencies=[Depends(require_token)])
    def get_status() -> dict[str, object]:
        return status_source().to_json()

    @app.get("/api/inbox", dependencies=[Depends(require_token)])
    def get_inbox() -> list[dict[str, object]]:
        return inbox.to_json(inbox_source())

    return app


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
        lambda: inbox.job_matches(settings.jobfinder_dir),
    )
    print(f"Jarvis Brain on http://{settings.host}:{settings.port}/")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
