"""Settings from the environment, with `.env` as a fallback. The token never has a default."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8700


class ConfigError(Exception):
    """A required setting is missing or malformed."""


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; blank lines and `#` comments are skipped, surrounding quotes removed."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    host: str
    port: int
    jobfinder_dir: Path
    roadmap_path: Path | None
    claude_oauth_token: str = ""  # from `claude setup-token`; empty means the CLI's own login
    vault_dir: Path | None = None  # the Obsidian vault Jarvis reads; the folder that holds CLAUDE_INDEX.md
    role: str = "desktop"  # "desktop" (the main Brain) or "backup" (the laptop, used while the desktop is off)
    primary_url: str = ""  # on the backup: the desktop Brain the page tries first
    cors_origins: tuple[str, ...] = ()  # on the desktop: pages allowed to call it from another origin
    local_model: str = ""  # the Ollama model for light requests; empty turns the local model off
    data_dir: Path = Path("data")  # chats and the run log
    # The daily brief. Mail: an IMAP account with an app password, read only. Calendars: secret iCal addresses.
    brief_email_account: str = ""
    brief_email_password: str = ""
    brief_imap_host: str = "imap.gmail.com"
    brief_calendar_urls: tuple[str, ...] = ()
    brief_time: str = "07:30"
    # Telegram, for the brief; taken from job-finder's .env when not set here.
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def backup(self) -> bool:
        return self.role == "backup"

    @classmethod
    def load(cls, env_file: Path, environ: Mapping[str, str] = os.environ) -> Settings:
        file_values = read_env_file(env_file)

        def get(name: str) -> str:
            return (environ.get(name) or file_values.get(name) or "").strip()

        token = get("JARVIS_TOKEN")
        if len(token) < 24:
            raise ConfigError("JARVIS_TOKEN must be set to a random value of at least 24 characters")
        host = get("JARVIS_HOST") or "127.0.0.1"
        port_text = get("JARVIS_PORT") or str(DEFAULT_PORT)
        if not port_text.isdigit():
            raise ConfigError(f"JARVIS_PORT must be a number, got {port_text!r}")
        base = env_file.parent
        jobfinder_dir = Path(get("JOBFINDER_DIR") or "../job-finder")
        roadmap = get("ROADMAP_PATH")
        vault = get("VAULT_DIR")
        vault_dir = Path(vault).expanduser() if vault else _vault_above(Path(roadmap)) if roadmap else None
        role = get("JARVIS_ROLE") or "desktop"
        if role not in ("desktop", "backup"):
            raise ConfigError(f"JARVIS_ROLE must be desktop or backup, got {role!r}")
        primary_url = get("JARVIS_PRIMARY_URL").rstrip("/")
        if role == "backup" and not primary_url.startswith("http://"):
            raise ConfigError("JARVIS_PRIMARY_URL must be the desktop Brain, like http://100.x.y.z:8700")
        # The backup page on the laptop (http://127.0.0.1:<port>) calls the desktop from its own origin.
        origins = get("JARVIS_CORS_ORIGINS") or f"http://127.0.0.1:{port_text},http://localhost:{port_text}"
        local_model = get("JARVIS_LOCAL_MODEL") or "gemma4:latest"
        jobfinder_path = jobfinder_dir if jobfinder_dir.is_absolute() else (base / jobfinder_dir).resolve()
        jobfinder_env = read_env_file(jobfinder_path / ".env")
        brief_time = get("BRIEF_TIME") or "07:30"
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", brief_time):
            raise ConfigError(f"BRIEF_TIME must look like 07:30, got {brief_time!r}")
        return cls(
            token=token,
            host=host,
            port=int(port_text),
            jobfinder_dir=jobfinder_dir if jobfinder_dir.is_absolute() else (base / jobfinder_dir).resolve(),
            roadmap_path=Path(roadmap).expanduser() if roadmap else None,
            claude_oauth_token=get("CLAUDE_CODE_OAUTH_TOKEN"),
            vault_dir=vault_dir,
            role=role,
            primary_url=primary_url if role == "backup" else "",
            cors_origins=tuple(o.strip().rstrip("/") for o in origins.split(",") if o.strip()) if role == "desktop"
            else (),
            local_model="" if role == "backup" or local_model.lower() == "off" else local_model,
            data_dir=(base / Path(get("JARVIS_DATA_DIR") or "data")).resolve(),
            brief_email_account=get("BRIEF_EMAIL_ACCOUNT"),
            brief_email_password=get("BRIEF_EMAIL_APP_PASSWORD").replace(" ", ""),
            brief_imap_host=get("BRIEF_IMAP_HOST") or "imap.gmail.com",
            brief_calendar_urls=tuple(u.strip() for u in get("BRIEF_CALENDAR_URLS").split(",") if u.strip()),
            brief_time=brief_time,
            telegram_token=get("TELEGRAM_BOT_TOKEN") or jobfinder_env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=get("TELEGRAM_CHAT_ID") or jobfinder_env.get("TELEGRAM_CHAT_ID", ""),
        )


def _vault_above(note: Path) -> Path | None:
    """The nearest folder above a vault note that holds CLAUDE_INDEX.md."""
    return next((folder for folder in note.expanduser().parents if (folder / "CLAUDE_INDEX.md").exists()), None)
