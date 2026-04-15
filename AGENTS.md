# AGENTS.md

This file is for human and AI agents working in this repository.

## Project Goal

`arc-bot` automates Arc Network daily tasks with Playwright, Gmail IMAP magic-link login, optional per-account proxies, saved browser sessions, and Telegram summaries.

## Code Layout

- `src/arc_bot/cli.py`: thin CLI entrypoint only
- `src/arc_bot/runner.py`: browser lifecycle, per-account execution, daemon loop
- `src/arc_bot/setup_ops.py`: setup flow, config checks, cron installation
- `src/arc_bot/reporting.py`: summary formatting and notification handoff
- `src/arc_bot/models.py`: shared dataclasses
- `src/arc_bot/config.py`: runtime paths, config loading, env loading, validation
- `src/arc_bot/logging_utils.py`: logger setup and secret redaction
- `src/arc_bot/state.py`: local state normalization and atomic persistence
- `src/arc_bot/browser_utils.py`: selector helpers, navigation retries, SOCKS5 bridge
- `src/arc_bot/auth.py`: login flow and Gmail IMAP magic-link retrieval
- `src/arc_bot/profile.py`: score lookup
- `src/arc_bot/content.py`: content task execution
- `src/arc_bot/events.py`: event registration task execution
- `src/arc_bot/forum.py`: forum discovery, post creation, and commenting
- `src/arc_bot/tasks.py`: compatibility re-export layer
- `src/arc_bot/notifications.py`: Telegram summary delivery
- `arc_daily.py`: compatibility wrapper for existing cron jobs
- `ARCHITECTURE.md`: quick module map and editing guidance

## Entry Points

Preferred:

```bash
python3 -m arc_bot --run-once
```

Compatibility:

```bash
python3 arc_daily.py --run-once
```

## Local Verification

Minimal checks after changes:

```bash
python3 -m py_compile arc_daily.py setup.py src/arc_bot/*.py
python3 arc_daily.py --help
PYTHONPATH=src python3 -m arc_bot --help
```

## Secrets and Safety

- Never print or commit live values from `accounts.local.txt`, `gmail_passes.local.txt`, `proxies.local.txt`, or `.env`.
- Never expose bot tokens, chat IDs, email addresses, app passwords, proxy credentials, session files, or magic links in comments, commit messages, logs shown to the user, or docs.
- Before any push, confirm local secret files are still ignored and not staged.
- The codebase intentionally uses hashed account IDs like `acct_529ca001` in logs and artifacts.

## Runtime Files

- `logs/`: runtime logs and screenshots
- `sessions/`: browser storage state
- `arc_state.json`: local task state

These are runtime artifacts, not source files.

## Operational Constraints

- The project is designed for serial account processing, not parallel browser execution.
- Proxies are first-class runtime inputs. Do not silently switch to direct connections for live checks unless the user explicitly asks for that.
- Cron is installed with `CRON_TZ=Asia/Ho_Chi_Minh` and currently runs the root wrapper `arc_daily.py`.

## Common Failure Modes

- Slow proxies can cause `Page.goto` timeouts on content, events, and forum pages.
- Gmail IMAP can fail if app passwords are wrong or magic-link emails do not arrive.
- Arc UI selectors can drift and break post/comment/event flows.

## Editing Guidance

- Prefer changing code in `src/arc_bot/`, not in generated artifacts.
- Prefer the focused modules over the compatibility facades:
  - runtime flow: `runner.py`
  - setup/cron: `setup_ops.py`
  - tasks: `profile.py`, `content.py`, `events.py`, `forum.py`
- Keep log messages operational and concise.
- Reuse helper functions in `browser_utils.py` instead of copying navigation or selector fallback code.
- Preserve compatibility for the root wrapper and existing cron usage unless the user explicitly asks to break that interface.
