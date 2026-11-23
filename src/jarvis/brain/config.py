"""Settings from the environment, with `.env` as a fallback. The token never has a default."""

from __future__ import annotations

import os
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
        return cls(
            token=token,
            host=host,
            port=int(port_text),
            jobfinder_dir=jobfinder_dir if jobfinder_dir.is_absolute() else (base / jobfinder_dir).resolve(),
            roadmap_path=Path(roadmap).expanduser() if roadmap else None,
            claude_oauth_token=get("CLAUDE_CODE_OAUTH_TOKEN"),
            vault_dir=vault_dir,
        )


def _vault_above(note: Path) -> Path | None:
    """The nearest folder above a vault note that holds CLAUDE_INDEX.md."""
    return next((folder for folder in note.expanduser().parents if (folder / "CLAUDE_INDEX.md").exists()), None)
