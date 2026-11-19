from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.brain import status
from jarvis.brain.app import create_app
from jarvis.brain.config import ConfigError, Settings
from jarvis.brain.inbox import InboxItem

TOKEN = "t" * 32

LOG = """==== 19/11/2026  7:12:03.45 ====
Boards: 56 scanned, 0 failed
Digest: 0 answers saved, 6 jobs sent, 0 waiting
exit: scan 0, alerts 0, extract 0, digest 0
==== 20/11/2026  7:10:00.00 ====
Boards: 56 scanned, 2 failed
Digest: 1 answers saved, 3 jobs sent, 0 waiting
exit: scan 1, alerts 0, extract 0, digest 0
"""

ROADMAP = """
### Phase 2 — דירוג מדיד · שבועות 2–3 · ⏱ 10–14 שעות · ✅ הושלם 2.11
### Phase 3 — דיג'סט בטלגרם · שבוע 4 · 🟡 נבנה, G3 בבדיקה
### Phase R — גישה מרחוק לשרת הביתי · שבועות 4–5 (16–29.11) · ⏱ 3–5 שעות
### Phase 9+ — תפקידי עסק ופיצ'רים · משבוע 14
"""


def runner(output: str) -> status.Runner:
    def run(args: Sequence[str]) -> str:
        return output
    return run


def test_last_daily_run_reads_the_latest_run() -> None:
    run = status.last_daily_run(LOG)
    assert run is not None
    assert run.started == "20/11/2026  7:10:00.00"
    assert run.finished and not run.ok
    assert (run.boards_scanned, run.boards_failed, run.jobs_sent) == (56, 2, 3)


def test_a_run_cut_off_before_its_exit_line_is_unfinished() -> None:
    run = status.last_daily_run(LOG + "==== 20/11/2026  0:07:42.25 ====\n^C\n")
    assert run is not None and not run.finished and not run.ok
    assert status.last_daily_run("") is None


def test_roadmap_phases() -> None:
    phases = status.roadmap_phases(ROADMAP)
    assert [(p.id, p.state) for p in phases] == [("2", "done"), ("3", "active"), ("R", "planned"), ("9+", "planned")]
    assert phases[2].name == "גישה מרחוק לשרת הביתי"


def test_tailscale_check() -> None:
    data = {"BackendState": "Running", "Peer": {"a": {"Online": True}, "b": {"Online": False}}}
    assert status.tailscale_check(runner(json.dumps(data))) == status.Check("Tailscale", True, "2 of 3 online")
    assert not status.tailscale_check(runner("")).ok
    assert not status.tailscale_check(runner('{"BackendState": "Stopped"}')).ok


def test_run_command_decodes_utf8() -> None:
    import sys
    out = status.run_command([sys.executable, "-c", "import sys; sys.stdout.buffer.write('דסקטופ ✓'.encode())"])
    assert out == "דסקטופ ✓"
    assert status.run_command(["no-such-command-xyz"]) == ""


def test_service_check() -> None:
    assert status.service_check("SSH", "sshd", runner("STATE : 4  RUNNING")).ok
    assert status.service_check("SSH", "sshd", runner("STATE : 1  STOPPED")).value == "stopped"
    assert status.service_check("SSH", "sshd", runner("")).value == "not installed"


def test_ollama_check() -> None:
    assert status.ollama_check(lambda url: {"models": [{}, {}]}).value == "2 models"

    def down(url: str) -> object:
        raise OSError("refused")
    assert not status.ollama_check(down).ok


def test_settings_require_a_long_token(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("JARVIS_TOKEN=short\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="JARVIS_TOKEN"):
        Settings.load(env, environ={})
    env.write_text(f"JARVIS_TOKEN={TOKEN}\nJARVIS_HOST=100.1.2.3\nJOBFINDER_DIR=../jf\n", encoding="utf-8")
    settings = Settings.load(env, environ={})
    assert (settings.host, settings.port) == ("100.1.2.3", 8700)
    assert settings.jobfinder_dir == (tmp_path / ".." / "jf").resolve()


def _client() -> TestClient:
    item = InboxItem("greenhouse:acme:1", "Software Engineer Intern", "Acme", "Tel Aviv", 95, "https://example.test/1")
    app = create_app(
        TOKEN,
        lambda: status.Status([status.Check("Desktop (Brain)", True, "online")], None, 10),
        lambda: [item],
    )
    return TestClient(app)


def test_api_needs_the_token() -> None:
    client = _client()
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/inbox", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_api_with_token() -> None:
    client = _client()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    body = client.get("/api/status", headers=auth).json()
    assert body["checks"][0] == {"name": "Desktop (Brain)", "ok": True, "value": "online"}
    assert body["open_jobs"] == 10
    assert client.get("/api/inbox", headers=auth).json()[0]["points"] == 95


def test_page_needs_no_token() -> None:
    response = _client().get("/")
    assert response.status_code == 200
    assert "Jarvis" in response.text
