# Architecture

This document is a quick map for humans and AI agents working in this repository.

## Runtime Flow

Normal execution path:

1. `arc_daily.py` or `python -m arc_bot`
2. `src/arc_bot/cli.py`
3. `src/arc_bot/runner.py`
4. `src/arc_bot/auth.py` for login/session recovery
5. focused task modules:
   - `profile.py`
   - `content.py`
   - `events.py`
   - `forum.py`
6. `state.py` persists local state
7. `reporting.py` builds the summary
8. `notifications.py` sends the Telegram message when configured

## Module Ownership

- `cli.py`
  Owns argument parsing and top-level process exit behavior only.
- `runner.py`
  Owns the browser lifecycle, per-account sequencing, saved-session reuse, and daemon scheduling.
- `setup_ops.py`
  Owns setup commands, config status output, and cron installation.
- `auth.py`
  Owns Arc sign-in and Gmail IMAP magic-link retrieval.
- `browser_utils.py`
  Owns reusable Playwright selector helpers, navigation retries, screenshots, and proxy helpers.
- `profile.py`
  Owns score extraction and profile page fallback lookup.
- `content.py`
  Owns article/video reading logic and related history tracking.
- `events.py`
  Owns event registration logic.
- `forum.py`
  Owns forum discovery, post creation, and commenting.
- `state.py`
  Owns defensive state loading and atomic writes.
- `reporting.py`
  Owns summary formatting and the notification handoff.
- `tasks.py`
  Compatibility facade only. Do not grow new task logic here.

## Editing Rules

- Preferred operator input lives in `data/accounts/`.
- Preferred runtime artifacts live in `data/logs/`, `data/sessions/`, and `data/arc_state.json`.
- Root-level config/state/session files are legacy compatibility paths. Do not add new features around them.
- Add task behavior in the focused task modules, not in `tasks.py`.
- Add orchestration behavior in `runner.py`, not in `cli.py`.
- Add setup or cron behavior in `setup_ops.py`.
- Reuse helpers from `browser_utils.py` before introducing new selector or navigation patterns.
- Keep all operator-facing messages in plain English.
- Never print or commit live secrets from `.env`, `*.local.txt`, session files, or logs.

## Verification

Minimum checks after structural changes:

```bash
python3 -m py_compile arc_daily.py setup.py src/arc_bot/*.py
python3 arc_daily.py --help
PYTHONPATH=src python3 -m arc_bot --help
```
