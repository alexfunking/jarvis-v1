# Jarvis

A personal assistant that runs on a home desktop and is used from any of the owner's devices over Tailscale.

The **Brain** is a small FastAPI server on the desktop. It serves a HUD-style web interface (`web/index.html`) and the data behind its panels:

- **Systems:** Tailscale, the SSH server and Ollama on the desktop.
- **Job Finder:** the latest daily run of [job-finder](../job-finder) and the number of open jobs.
- **Decision Inbox:** job-finder matches that have not been answered yet.
- **Roadmap:** the phases of the roadmap note.

## Run

```
uv sync
cp .env.example .env    # then set JARVIS_TOKEN to a long random value
uv run jarvis-brain
```

The server listens on `JARVIS_HOST` only (the desktop's Tailscale address) and every `/api` call needs `Authorization: Bearer <JARVIS_TOKEN>`. The page asks for the token once and keeps it in the browser.

## Develop

```
uv run pytest
uv run mypy
```
