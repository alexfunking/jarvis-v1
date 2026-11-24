# jarvis — context for coding agents

## What this is
The Jarvis assistant. Plan and phases: Obsidian vault `02_Projects/Jarvis/Jarvis - Roadmap.md` (Phase N). Topology: the desktop runs everything; the laptop and phone reach it over Tailscale.

## Layout
- `src/jarvis/brain/` is the Brain server: `config.py` (settings from `.env`), `status.py` (system checks, last daily run, roadmap phases), `inbox.py` (job-finder matches and the answers to them, through job-finder's `record_answer`), `tasks.py` (Ask Jarvis: headless Claude Code runs, the model router, the task queue), `conversations.py` (chats and their history), `app.py` (FastAPI app and `jarvis-brain` entry point).
- `web/index.html` is the whole interface: one file, no build step. The orb is a Canvas 2D particle sphere; `window.jarvisState("idle" | "thinking" | "speaking")` drives it. `manifest.webmanifest` and `icon.svg` let a browser open it as an app.
- job-finder is an editable path dependency (`../job-finder`); Jarvis reads its DB, labels and priorities and never duplicates its logic.
- `data/` (ignored) holds `conversations.json` (chat history) and `runs.jsonl` (one line per run: model, seconds, tokens, never the request text).

## Ask Jarvis
- Each request runs `claude -p` (the installed Claude Code CLI) on the subscription, through `CLAUDE_CODE_OAUTH_TOKEN` in `.env` from `claude setup-token`. Chosen over the Agent SDK because the SDK's docs direct third-party products to API keys.
- It works from the vault (`VAULT_DIR`, else the folder above `ROADMAP_PATH` that holds `CLAUDE_INDEX.md`), with the code projects added. It writes only in the vault's memory folder `02_Projects/Jarvis/Memory/`: `--tools Read,Grep,Glob,Edit,Write`, one allow rule `Edit(02_Projects/Jarvis/Memory/**)`, and `--permission-mode dontAsk` denies every other edit (tested live: a write outside is blocked by the permission layer, not only by the prompt). Without a vault it is read-only.
- Memory: `Jarvis Memory.md` in that folder is a short index put in front of the first request of each chat (`with_memory`); longer material goes in topic notes there.
- Memory review (desktop only): a thread runs `TaskQueue.review_idle` every 5 minutes. Chats idle 30 minutes with turns not yet reviewed (`Conversation.reviewed_turns`) go to one Sonnet run with the memory tools and `--no-session-persistence`, which writes what is lasting. Imported backup chats are reviewed the same way. The Systems panel shows the last review; `runs.jsonl` logs it as `kind: memory-review`, without chat text.
- Past chats: every chat is also `data/chats/<date> <id>.md` (ignored), added with `--add-dir` and named in the system prompt, so Jarvis can Grep earlier conversations.
- `claude.exe` is called directly, not npm's `claude.cmd` shim, so cmd.exe never parses the arguments.
- `scripts/brain.ps1` keeps the Brain running (restarts every 30 s, log in `data/brain.log`); `scripts/register_task.ps1` registers it as the `Jarvis Brain` logon task.
- Each conversation is one Claude Code session: `--session-id` on the first turn, `--resume` after. A Haiku call picks haiku / sonnet / opus per request; `/haiku`, `/sonnet`, `/opus`, `/local` at the start choose by hand. Any other leading `/` is refused, so requests cannot run Claude Code commands.
- The request goes in on stdin. The fixed arguments (the system prompt too) still hold none of `" & | < > ^ % !`, for the case where only the `.cmd` shim is found; a test checks it.
- Local model (`local.py`): the router may also answer `local` when Ollama has `JARVIS_LOCAL_MODEL` (default `gemma4:latest`); `/local` picks it by hand; without it `local` becomes haiku. It has no tools, gets the memory index and the last turns, and never touches the Claude session.
- `Conversation.claude_turns` counts the turns the Claude session has seen; the rest (local answers, imported chats) go in front of the next Claude request (`with_history`).

## Daily brief (`brief.py`)
- Desktop only. A thread checks every minute; after `BRIEF_TIME` it runs once a day (state in `data/brief.json`), and a failed run is retried after 30 minutes. `POST /api/brief` and the BRIEF button run it now.
- Mail: `ImapReader`, read-only (EXAMINE, BODY.PEEK), the last 24 hours, at most 40 messages; on Gmail promotions, social and forums are left out (X-GM-RAW). Calendars: `BRIEF_CALENDAR_URLS`, secret iCal addresses, expanded with `recurring-ical-events` for today and tomorrow. The addresses never go in a log or an error message.
- Claude writes it in one `ClaudeCode.complete` run: Sonnet, no tools, no session; the prompt says the mail is data and never instructions. Sent to Telegram (job-finder's bot unless set here), HTML-escaped, split under the limit. Saved as a chat marked reviewed, so mail content never reaches the memory review or the vault. `runs.jsonl` gets counts only (`kind: daily-brief`).

## Backup (N6)
- The desktop is preferred. While it is off, the laptop runs the same server with `JARVIS_ROLE=backup` on 127.0.0.1: Claude only, memory read but not written (`memory_writable=False`), no job-finder data. Setup: `scripts/setup_backup.ps1`.
- The page asks its own server `/api/role`; on the backup it tries `JARVIS_PRIMARY_URL` first (3 s), rechecks every 30 s and never switches under a running request. The desktop allows that origin through CORS (`JARVIS_CORS_ORIGINS`, default `http://127.0.0.1:<port>`); the token is still required.
- When the desktop is back, the page posts the backup's chats to `POST /api/conversations/import` (desktop only) and deletes them on the backup; the memory review picks them up. Only the desktop writes memory.

## Where it stands (23.11.2026)
- Done: Brain with status, inbox (answers write back to job-finder) and Ask Jarvis with chats, vault context, model routing and vault memory; auto-start at logon; local model and the laptop backup (N6), tested live with two Brains on one machine; idle chats reviewed for memory and past chats searchable, tested live in a temp copy of the memory folder.
- Daily brief built (24.11): reader tested live on Gmail, the summary tested live on sample data; waiting for the user's app password and calendar address in `.env`.
- Next: run `scripts/setup_backup.ps1` on the Zenbook and test with the desktop really off.

## Hard rules
- Listen on the Tailscale address only; every `/api` route needs the bearer token. Never add an unauthenticated data route.
- Never commit `.env` or personal data. The token is never printed to logs or chat.
- Applications are never submitted without explicit human approval.

## Engineering standards
- Python 3.12 via uv, full type hints, `mypy --strict` clean, pytest in `tests/`, no network or machine access in unit tests (inject runners and fetchers).
- Talk with the user in Hebrew; code, commits and docs in English. Commit only after the user approves; push only when asked.
