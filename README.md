# arc-bot

Playwright automation for Arc Network daily tasks across one or more accounts.

The project logs in through Arc email magic links, runs the daily task flow, keeps browser sessions and local state, supports one proxy per account, and can send the final run summary to Telegram.

## Purpose

The repository is organized as a Python package so it is easier to install, inspect, and operate:

- `src/arc_bot/`: application package
- `arc_daily.py`: compatibility wrapper for existing cron jobs and direct script usage
- `pyproject.toml`: package metadata and console script definition
- `ARCHITECTURE.md`: module map and runtime flow
- `data/accounts/`: tracked templates plus ignored local account files
- `data/`: runtime logs, sessions, and state

## Runtime Workflow

For each configured account, the bot:

1. Opens Arc through the configured proxy, if any.
2. Reuses a saved session when possible.
3. Falls back to Gmail IMAP magic-link login when the session is missing or expired.
4. Checks the current score.
5. Runs the daily tasks:
   read content, register events, create one discussion post, submit comments.
6. Saves the updated session and state.
7. Prints and optionally sends a Telegram summary.

The run is serial by design. Accounts are not processed in parallel.

## Project Layout

```text
arc-bot/
  arc_daily.py
  pyproject.toml
  README.md
  SECURITY_REVIEW.md
  data/
    accounts/
      accounts.txt
      gmail_passes.txt
      proxies.txt
    logs/
    sessions/
    arc_state.json
  src/
    arc_bot/
      __init__.py
      __main__.py
      cli.py
      runner.py
      setup_ops.py
      reporting.py
      models.py
      config.py
      logging_utils.py
      state.py
      browser_utils.py
      auth.py
      profile.py
      content.py
      events.py
      forum.py
      tasks.py
      notifications.py
```

Module responsibilities:

- `cli.py`: thin CLI entrypoint that parses flags and dispatches work
- `runner.py`: main runtime orchestration, browser launch, per-account execution, daemon loop
- `setup_ops.py`: setup flow, config status reporting, cron installation and validation
- `reporting.py`: summary formatting and notification dispatch boundary
- `models.py`: small shared dataclasses used across the runtime flow
- `config.py`: filesystem paths, env loading, account/proxy loading, config validation
- `logging_utils.py`: logger setup and secret redaction
- `state.py`: state normalization and atomic save
- `browser_utils.py`: selector helpers, navigation helpers, proxy parsing, SOCKS5 bridge
- `auth.py`: Arc login flow and Gmail IMAP magic-link retrieval
- `profile.py`: score lookup and profile navigation
- `content.py`: article/video task flow
- `events.py`: event registration task flow
- `forum.py`: forum URL detection, post creation, and commenting
- `tasks.py`: compatibility re-export layer for the task functions above
- `notifications.py`: Telegram summary delivery

The compatibility layers are intentional:

- `arc_daily.py` keeps existing cron commands stable
- `tasks.py` keeps older internal imports stable

New feature work should go into the focused modules, not back into the compatibility facades.

## Requirements

- Python 3.10 or newer
- Gmail mailbox for each Arc account
- Gmail IMAP enabled
- Gmail app password for each mailbox
- Chromium installed through Playwright

## Installation

1. Install the package in editable mode:

```bash
python3 -m pip install -e .
```

If your server already has the dependencies and editable install fails because of build isolation, use:

```bash
python3 -m pip install --no-build-isolation -e .
```

2. Install the Chromium browser used by Playwright:

```bash
python3 -m playwright install chromium
```

3. On Linux, install Playwright system dependencies if needed:

```bash
python3 -m playwright install-deps chromium
```

Alternative:

```bash
python3 arc_daily.py --setup
```

`requirements.txt` is kept for convenience and simply installs the local package:

```bash
python3 -m pip install -r requirements.txt
```

## Commands

Preferred package entrypoints:

```bash
arc-bot --run-once
python3 -m arc_bot --run-once
```

Compatibility entrypoint:

```bash
python3 arc_daily.py --run-once
```

Supported modes:

- `--run-once`: execute all configured accounts once
- `--daemon`: run in an internal loop with a 24-hour interval
- `--setup`: install dependencies and review local config status
- `--setup-cron`: install the daily cron entry
- `--account EMAIL`: run one configured account only
- `--headful`: open Chromium in headed mode for debugging
- `--cron-schedule`: override the cron expression used by `--setup-cron`
- `--interval-hours`: override the daemon loop interval

Examples:

```bash
arc-bot --run-once
arc-bot --run-once --account alice@gmail.com
arc-bot --daemon
python3 arc_daily.py --setup-cron
```

## Configuration

Tracked files are templates only. Never store live secrets in tracked files.

Preferred operator files live under `data/accounts/`. Legacy root files are still accepted for backward compatibility, but new setups should use the `data/accounts/` layout.

### `data/accounts/accounts.local.txt`

One Arc login email per line:

```text
alice@gmail.com
bob@example.com
```

Only email addresses are valid. The old `email----password` format is not supported.

### `data/accounts/gmail_passes.local.txt`

One Gmail app password per line. Line order must match `data/accounts/accounts.local.txt`.

```text
abcd efgh ijkl mnop
wxyz abcd efgh ijkl
```

### `data/accounts/proxies.local.txt`

Optional. One proxy per line. Line order must match `data/accounts/accounts.local.txt`.

Supported formats:

```text
http://user:pass@host:port
https://user:pass@host:port
socks5://user:pass@host:port
http://host:port
none
```

Use `none` to run a specific account without a proxy.

### Environment Variables

Telegram summary delivery is enabled only when both values are present in the environment or a `.env` file:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

The loader checks:

- `.env` in the repository root
- `.env` in the parent directory
- existing process environment variables

`.env.example` documents the expected keys. Do not commit live bot tokens or chat IDs.

## Gmail Setup Notes

- Enable Google 2-Step Verification.
- Generate a 16-character app password in Google Account -> Security -> App passwords.
- Enable IMAP in Gmail -> Settings -> Forwarding and POP/IMAP.
- The bot polls Gmail over IMAP and looks for recent Arc or Circle login emails.

## Cron Behavior

`python3 arc_daily.py --setup-cron` installs:

- `CRON_TZ=Asia/Ho_Chi_Minh`
- schedule `11 7 * * *`
- command `python3 arc_daily.py --run-once`

That means the job runs every day at 07:11 Hanoi time.

The cron helper intentionally uses the root wrapper `arc_daily.py` so existing server setups remain stable even after the internal package refactor.

## Runtime Files

- `data/logs/`: runtime logs and screenshots
- `data/sessions/`: browser storage state files
- `data/arc_state.json`: per-account local state
- `data/logs/arc_cron.log`: cron output

Runtime artifacts use hashed account labels such as `acct_529ca001` instead of raw email addresses.

When an older root-level `arc_state.json` or hashed session file is present, the bot migrates it into `data/` automatically on use.

## Security Model

The project was reviewed specifically for accidental secret exposure, outbound behavior, cron injection, and logging leaks.

- `SECURITY_REVIEW.md`: high-level security review and outbound-path summary
- `security_best_practices_report.md`: concrete hardening notes and review findings

Current outbound paths are expected and limited to:

- Arc Network via Playwright
- Gmail IMAP for magic-link retrieval
- configured proxies
- Telegram Bot API for summary delivery

Hardening already in place:

- local secret files are ignored by Git
- tracked config files are templates only
- logs and summaries redact emails, proxy credentials, bot tokens, app passwords, and sensitive URLs
- state writes are atomic
- runtime summaries use hashed account labels

## Troubleshooting

Slow proxy or page timeouts:
- Arc pages behind Cloudflare can load slowly through some proxies.
- Login, content, event, and forum pages may need longer waits on poor proxies.

Magic link not received:
- check Gmail IMAP
- check app password validity
- check whether Arc actually sent the login email
- check whether the mailbox is receiving and not filtering the email

Headful mode on servers:
- `--headful` requires a display server
- on most headless Linux servers, use `--run-once` without `--headful`

Saved session problems:
- invalid or expired sessions are deleted automatically
- the bot then falls back to the login flow
