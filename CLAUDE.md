# jarvis — context for coding agents

## What this is
The Jarvis assistant. Plan and phases: Obsidian vault `02_Projects/Jarvis/Jarvis - Roadmap.md` (Phase N). Topology: the desktop runs everything; the laptop and phone reach it over Tailscale.

## Layout
- `src/jarvis/brain/` is the Brain server: `config.py` (settings from `.env`), `status.py` (system checks, last daily run, roadmap phases), `inbox.py` (unanswered job-finder matches, read-only), `app.py` (FastAPI app and `jarvis-brain` entry point).
- `web/index.html` is the whole interface: one file, no build step. The orb is a Canvas 2D particle sphere; `window.jarvisState("idle" | "thinking" | "speaking")` drives it.
- job-finder is an editable path dependency (`../job-finder`); Jarvis reads its DB, labels and priorities and never duplicates its logic.

## Where it stands (20.11.2026)
- Done: Brain with `/api/status` and `/api/inbox`, interface wired to them, tested from the laptop.
- Next: inbox buttons write answers back to job-finder; then the Ask box runs an agent (N1 auth from the subscription first).

## Hard rules
- Listen on the Tailscale address only; every `/api` route needs the bearer token. Never add an unauthenticated data route.
- Never commit `.env` or personal data. The token is never printed to logs or chat.
- Applications are never submitted without explicit human approval.

## Engineering standards
- Python 3.12 via uv, full type hints, `mypy --strict` clean, pytest in `tests/`, no network or machine access in unit tests (inject runners and fetchers).
- Talk with the user in Hebrew; code, commits and docs in English. Commit only after the user approves; push only when asked.
