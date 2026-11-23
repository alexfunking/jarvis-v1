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
- `claude.exe` is called directly, not npm's `claude.cmd` shim, so cmd.exe never parses the arguments.
- `scripts/brain.ps1` keeps the Brain running (restarts every 30 s, log in `data/brain.log`); `scripts/register_task.ps1` registers it as the `Jarvis Brain` logon task.
- Each conversation is one Claude Code session: `--session-id` on the first turn, `--resume` after. A Haiku call picks haiku / sonnet / opus per request; `/haiku`, `/sonnet`, `/opus` at the start choose by hand. Any other leading `/` is refused, so requests cannot run Claude Code commands.
- The request goes in on stdin. The fixed arguments (the system prompt too) still hold none of `" & | < > ^ % !`, for the case where only the `.cmd` shim is found; a test checks it.

## Where it stands (23.11.2026)
- Done: Brain with status, inbox (answers write back to job-finder) and Ask Jarvis with chats, vault context, model routing and vault memory; auto-start at logon; all tested live.
- Next: queue requests while the desktop is off (N6).

## Hard rules
- Listen on the Tailscale address only; every `/api` route needs the bearer token. Never add an unauthenticated data route.
- Never commit `.env` or personal data. The token is never printed to logs or chat.
- Applications are never submitted without explicit human approval.

## Engineering standards
- Python 3.12 via uv, full type hints, `mypy --strict` clean, pytest in `tests/`, no network or machine access in unit tests (inject runners and fetchers).
- Talk with the user in Hebrew; code, commits and docs in English. Commit only after the user approves; push only when asked.
