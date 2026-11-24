# Jarvis

A personal assistant that runs on a home desktop and is used from any of the owner's devices over Tailscale.

The **Brain** is a small FastAPI server on the desktop. It serves a HUD-style web interface (`web/index.html`) and the data behind its panels:

- **Systems:** Tailscale, the SSH server and Ollama on the desktop.
- **Job Finder:** the latest daily run of [job-finder](../job-finder) and the number of open jobs.
- **Decision Inbox:** job-finder matches that have not been answered yet.
- **Roadmap:** the phases of the roadmap note.
- **Ask Jarvis:** questions answered by Claude Code running headless on the desktop, with the owner's Obsidian vault as context. It reads the vault and the code projects and writes only in the vault's `02_Projects/Jarvis/Memory/` folder, whose short memory note goes in front of each new chat. A chat left idle for 30 minutes is reviewed for anything lasting, which goes into that note, and earlier chats are saved as Markdown files Jarvis can search. Each chat keeps its own context, and a quick Haiku call picks Haiku, Sonnet or Opus for each request (`/opus`, `/sonnet` or `/haiku` at the start chooses by hand). Light requests that need no files (rewording, translating, a short message) can go to a local model served by Ollama on the desktop's GPU (`/local`).
- **Daily brief:** every morning (`BRIEF_TIME`, or the first chance after it) Claude summarizes today's and tomorrow's calendar and the last day's mail, sends it to Telegram and keeps it as a chat to ask about. Mail is read over IMAP with an app password and never marked as read; calendars come from their secret iCal addresses. The BRIEF button writes one now.
- **Backup:** while the desktop is off, the same server runs on the laptop (`JARVIS_ROLE=backup`, on 127.0.0.1) and answers through Claude only, with no job-finder data and memory read-only. The page tries the desktop first and switches by itself; when the desktop is back, the backup's chats move there and are reviewed for memory.

## Run

```
uv sync
cp .env.example .env    # then set JARVIS_TOKEN to a long random value
claude setup-token      # and put the token in .env as CLAUDE_CODE_OAUTH_TOKEN
uv run jarvis-brain
```

The server listens on `JARVIS_HOST` only (the desktop's Tailscale address) and every `/api` call needs `Authorization: Bearer <JARVIS_TOKEN>`. The page asks for the token once and keeps it in the browser. A browser can open it as an app (Edge or Chrome: `--app=http://<host>:8700/`, or "Install app").

To start the Brain at logon and restart it if it stops, run `scripts/register_task.ps1` once. It registers `scripts/brain.ps1` as the `Jarvis Brain` scheduled task (log in `data/brain.log`).

### The laptop backup

Clone `jarvis` and `job-finder` side by side on the laptop, install `uv` and Claude Code, make sure `ssh <desktop>` works, then run from the jarvis checkout:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_backup.ps1 -Desktop <ssh host> -VaultDir <vault folder>
```

It copies the two tokens from the desktop's `.env` over SSH without printing them, writes the backup `.env`, registers the logon task and adds a Jarvis shortcut that opens `http://127.0.0.1:8700/`.

## Develop

```
uv run pytest
uv run mypy
```
