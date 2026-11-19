"""What the Systems panel shows: each check reads one local source and never raises.

The commands and the HTTP call sit behind small callables so tests feed canned output instead of
touching the machine.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

TAILSCALE_EXE: Final = r"C:\Program Files\Tailscale\tailscale.exe"
OLLAMA_TAGS_URL: Final = "http://127.0.0.1:11434/api/tags"
RUN_HEADER_RE: Final = re.compile(r"^==== (?P<when>.+?) ====\s*$", re.MULTILINE)
EXIT_RE: Final = re.compile(r"^exit: (?P<steps>.+?)\s*$", re.MULTILINE)
BOARDS_RE: Final = re.compile(r"^Boards:\s+(?P<scanned>\d+) scanned, (?P<failed>\d+) failed", re.MULTILINE)
DIGEST_RE: Final = re.compile(r"^Digest: .*?(?P<sent>\d+) jobs sent", re.MULTILINE)
PHASE_RE: Final = re.compile(r"^### Phase (?P<id>[\w+]+) — (?P<name>[^·\n]+?)\s*(?:·(?P<rest>.*))?$", re.MULTILINE)

Runner = Callable[[Sequence[str]], str]
Fetch = Callable[[str], object]


def run_command(args: Sequence[str]) -> str:
    """stdout of a short local command; an empty string when it cannot run."""
    try:
        done = subprocess.run(list(args), capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    # Decoded here, not by subprocess: the Windows default code page fails on the UTF-8 that
    # tools like tailscale print, and that failure happens in a reader thread nobody catches.
    return done.stdout.decode("utf-8", errors="replace")


def fetch_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=3) as response:
        parsed: object = json.loads(response.read())
        return parsed


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    value: str


def tailscale_check(run: Runner = run_command) -> Check:
    try:
        data = json.loads(run([TAILSCALE_EXE, "status", "--json"]) or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or data.get("BackendState") != "Running":
        return Check("Tailscale", False, "not running")
    peers = data.get("Peer") or {}
    peers = peers if isinstance(peers, dict) else {}
    online = sum(1 for peer in peers.values() if isinstance(peer, dict) and peer.get("Online"))
    return Check("Tailscale", True, f"{online + 1} of {len(peers) + 1} online")


def service_check(label: str, service: str, run: Runner = run_command) -> Check:
    out = run(["sc", "query", service])
    if "RUNNING" in out:
        return Check(label, True, "running")
    return Check(label, False, "stopped" if "STATE" in out else "not installed")


def ollama_check(fetch: Fetch = fetch_json) -> Check:
    try:
        data = fetch(OLLAMA_TAGS_URL)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return Check("Ollama", False, "not running")
    models = data.get("models") if isinstance(data, dict) else None
    count = len(models) if isinstance(models, list) else 0
    return Check("Ollama", True, f"{count} models")


@dataclass(frozen=True, slots=True)
class LastRun:
    """The latest daily run in job-finder's log. `finished` is False for a run cut off before its exit line."""

    started: str
    finished: bool
    ok: bool
    exit_line: str
    boards_scanned: int | None = None
    boards_failed: int | None = None
    jobs_sent: int | None = None


def last_daily_run(log_text: str) -> LastRun | None:
    headers = list(RUN_HEADER_RE.finditer(log_text))
    if not headers:
        return None
    last = headers[-1]
    body = log_text[last.end():]
    exit_match = EXIT_RE.search(body)
    if exit_match is None:
        return LastRun(started=last["when"].strip(), finished=False, ok=False, exit_line="")
    codes = re.findall(r"\b\w+ (-?\d+)", exit_match["steps"])
    boards = BOARDS_RE.search(body)
    digest = DIGEST_RE.search(body)
    return LastRun(
        started=last["when"].strip(),
        finished=True,
        ok=bool(codes) and all(code == "0" for code in codes),
        exit_line=exit_match["steps"],
        boards_scanned=int(boards["scanned"]) if boards else None,
        boards_failed=int(boards["failed"]) if boards else None,
        jobs_sent=int(digest["sent"]) if digest else None,
    )


@dataclass(frozen=True, slots=True)
class Phase:
    id: str
    name: str
    state: str  # "done", "active" or "planned"


def roadmap_phases(markdown: str) -> list[Phase]:
    """Phase headings of the vault roadmap: ✅ means done, 🟡 means in progress."""
    phases: list[Phase] = []
    for match in PHASE_RE.finditer(markdown):
        rest = match["rest"] or ""
        state = "done" if "✅" in rest else "active" if "🟡" in rest else "planned"
        phases.append(Phase(match["id"], match["name"].strip(), state))
    return phases


@dataclass(frozen=True, slots=True)
class Status:
    checks: list[Check]
    last_run: LastRun | None
    open_jobs: int | None
    phases: list[Phase] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
