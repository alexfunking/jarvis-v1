# Jarvis

A personal assistant that runs on a home desktop and is used from any of the owner's devices over Tailscale.

The **Brain** is a small FastAPI server on the desktop. It serves a HUD-style web interface (`web/index.html`) and the data behind its panels:

- **Systems:** Tailscale, the SSH server and Ollama on the desktop.
- **Job Finder:** the latest daily run of [job-finder](../job-finder) and the number of open jobs.
- **Decision Inbox:** job-finder matches that have not been answered yet.
- **Roadmap:** the phases of the roadmap note.
- **Ask Jarvis:** questions answered by Claude Code running headless on the desktop, with the owner's Obsidian vault as context. It reads the vault and the code projects and writes only in the vault's `02_Projects/Jarvis/Memory/` folder, whose short memory note goes in front of each new chat. Each chat keeps its own context, and a quick Haiku call picks Haiku, Sonnet or Opus for each request (`/opus`, `/sonnet` or `/haiku` at the start chooses by hand).

## Run

```
uv sync
cp .env.example .env    # then set JARVIS_TOKEN to a long random value
claude setup-token      # and put the token in .env as CLAUDE_CODE_OAUTH_TOKEN
uv run jarvis-brain
```

The server listens on `JARVIS_HOST` only (the desktop's Tailscale address) and every `/api` call needs `Authorization: Bearer <JARVIS_TOKEN>`. The page asks for the token once and keeps it in the browser. A browser can open it as an app (Edge or Chrome: `--app=http://<host>:8700/`, or "Install app").

To start the Brain at logon and restart it if it stops, run `scripts/register_task.ps1` once. It registers `scripts/brain.ps1` as the `Jarvis Brain` scheduled task (log in `data/brain.log`).

## Develop

```
uv run pytest
uv run mypy
```
