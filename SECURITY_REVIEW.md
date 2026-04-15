# Security Review

Date: 2026-04-15

## Scope

Reviewed the repository for hidden outbound behavior, accidental secret exposure, and operator-facing leaks in logs, state, screenshots, sessions, and notifications.
The current codebase is packaged under `src/arc_bot` with a root compatibility wrapper.

## Findings

1. No hidden exfiltration logic was found.
The code only contains expected outbound paths for the automation workflow:
- Arc Network via Playwright browser navigation
- Gmail IMAP for magic-link retrieval
- Optional proxy tunneling for browser traffic
- Telegram `sendMessage` for run summaries

2. Secret storage in tracked files was a real risk and has been fixed.
- Live credentials were moved out of tracked config files and into ignored local files under `data/accounts/`
- `.env`, `data/accounts/*.local.txt`, and legacy root `*.local.txt` are ignored by Git
- Tracked files now contain templates only

3. Raw account identifiers were leaking into runtime artifacts and have been fixed.
- Session files, screenshots, state keys, logs, and summaries now use stable hashed labels such as `acct_529ca001`
- Existing state remains readable through legacy-key migration support

4. Runtime exceptions could leak secrets through logs or Telegram summaries and have been reduced.
- Added centralized redaction for emails, bot tokens, Gmail app passwords, proxy credentials, and sensitive URLs
- Account-level errors and task failures now store and report sanitized messages
- Telegram notifications send sanitized summary text only

## Residual Risks

- Browser automation still depends on the current Arc UI and external services controlled by Arc, Gmail, and the configured proxies.
- A future code change could introduce a new outbound path, so any new networking dependency should be reviewed before deployment.
- Telegram summaries intentionally send task status and account hashes off-host; disable Telegram configuration if that is not acceptable for a given environment.

## Hardening Notes

- Main application package lives in `src/arc_bot`
- `arc_daily.py` is retained as a compatibility entrypoint for existing cron jobs
- Cron uses `CRON_TZ=Asia/Ho_Chi_Minh` and runs at `11 7 * * *`
- Preferred runtime logs go to `data/logs/`
- Preferred browser sessions go to `data/sessions/`
- State is saved atomically to `data/arc_state.json`
