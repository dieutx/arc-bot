# arc-bot

Playwright automation for Arc Network daily tasks across one or more accounts.

The script logs in with Arc email magic links, reads content, registers events, creates one discussion post, and submits comments. It supports optional per-account proxies, saved browser sessions, and Telegram run summaries.

If your system exposes `python3` instead of `python`, replace the example commands below accordingly.

## Security Notes

- Do not put live secrets in tracked files.
- Keep live credentials in `accounts.local.txt`, `gmail_passes.local.txt`, and `proxies.local.txt`.
- Telegram settings are loaded from environment variables or from `.env` files in the repo root or its parent directory.
- Logs, screenshots, session files, and summaries use hashed account labels such as `acct_529ca001` instead of raw emails.
- See `SECURITY_REVIEW.md` for the hardening summary and reviewed outbound paths.

## Requirements

- Python 3.10+
- A Gmail mailbox for each Arc account
- Gmail IMAP enabled
- A Gmail app password for each mailbox
- Chromium installed through Playwright

## Installation

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Install the Chromium browser used by Playwright:

```bash
python -m playwright install chromium
```

3. On Linux, install Playwright's system dependencies if needed:

```bash
python -m playwright install-deps chromium
```

You can also use the built-in setup helper:

```bash
python arc_daily.py --setup
```

## Configuration Files

Tracked files such as `accounts.txt`, `gmail_passes.txt`, and `proxies.txt` are templates only.

Put live secrets in these ignored local files:

### `accounts.local.txt`

One Arc login email per line:

```text
alice@gmail.com
bob@example.com
```

Important: this file must contain emails only. The old `email----password` format is not supported.

### `gmail_passes.local.txt`

One Gmail app password per line. The order must match `accounts.local.txt`.

```text
abcd efgh ijkl mnop
wxyz abcd efgh ijkl
```

### `proxies.local.txt`

Optional. One proxy per line. The order must match `accounts.local.txt`.

Use `none` or leave the file empty if an account should run without a proxy.

Supported formats:

```text
http://user:pass@host:port
https://user:pass@host:port
socks5://user:pass@host:port
http://host:port
none
```

## Telegram Reporting

The script will send the final run summary to Telegram when both of these values are available in the environment or a `.env` file:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

`.env.example` documents the expected keys. The repository never stores real bot tokens or chat IDs.

## Gmail Notes

- Google 2-Step Verification must be enabled.
- Create a 16-character app password in Google Account -> Security -> App passwords.
- Gmail IMAP must be enabled in Gmail -> Settings -> Forwarding and POP/IMAP.
- The script reads login emails from Gmail over IMAP and looks for Arc/Circle magic links.

## Run Modes

Run everything once and exit:

```bash
python arc_daily.py --run-once
```

Run continuously with a 24 hour interval:

```bash
python arc_daily.py --daemon
```

Run only one configured account:

```bash
python arc_daily.py --run-once --account alice@gmail.com
```

Run Chromium in headful mode for debugging:

```bash
python arc_daily.py --run-once --headful
```

Install the daily cron entry:

```bash
python arc_daily.py --setup-cron
```

By default the cron helper installs `11 7 * * *` with `CRON_TZ=Asia/Ho_Chi_Minh`, which runs every day at 07:11 Hanoi time.

## Runtime Files

- Logs: `logs/`
- Browser sessions: `sessions/`
- State file: `arc_state.json`
- Cron log: `logs/arc_cron.log`

The state file stores per-account registered events, read articles, and the last successful run timestamp by hashed account ID.

## Notes on Sessions and Proxies

- Browser sessions are saved with hashed account IDs, not raw emails.
- If a saved session expires or cannot be reused, the script deletes it and logs in again.
- SOCKS5 proxies with authentication are bridged to a local HTTP proxy because Playwright Chromium does not handle authenticated SOCKS5 directly.
- Slow proxies may still require longer page timeouts on some Arc pages.

## Typical Workflow

1. Fill in `accounts.local.txt` and `gmail_passes.local.txt`.
2. Optionally fill in `proxies.local.txt`.
3. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to your server `.env` file if you want Telegram summaries.
4. Run `python arc_daily.py --run-once` to validate the setup.
5. If the one-shot run works, use `python arc_daily.py --setup-cron` or `python arc_daily.py --daemon`.
