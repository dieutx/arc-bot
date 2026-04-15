# Security Best Practices Report

Date: 2026-04-15

## Executive Summary

The repository does not contain hidden exfiltration logic. The main issues found in this pass were operational and defensive-code gaps: cron schedule input was not validated before being written into the user's crontab, navigation retries were not centralized for the slow proxy-heavy runtime path, and one navigation helper lowercased discovered URLs before reuse. These issues were fixed in this pass.

## High

### 1. Cron schedule input allowed newline-style crontab injection

Impact: a malicious or malformed `--cron-schedule` value could add unintended crontab content instead of a single schedule.

- Affected area before fix: `setup_cron(schedule)` wrote the provided string directly into the crontab entry.
- Fixed in `src/arc_bot/setup_ops.py` via `validate_cron_schedule(...)`, which normalizes and validates a strict five-field cron expression before writing anything to crontab.

## Medium

### 2. Proxy-driven page loads were using inconsistent retry behavior

Impact: task pages were much more likely to fail than the login page on slow proxies, causing partial runs and unreliable automation.

- Before this pass, several task pages used one-shot `page.goto(...)` calls without the retry behavior already added for the sign-in page.
- Fixed in:
  - `src/arc_bot/browser_utils.py` with `goto_url_with_retries(...)`
  - `src/arc_bot/content.py` for content navigation
  - `src/arc_bot/events.py` for event navigation
  - `src/arc_bot/forum.py` for forum and comment navigation
  - `src/arc_bot/auth.py` for magic-link and home fallback navigation
  - `src/arc_bot/runner.py` for saved-session validation

### 3. Discovered forum URLs were lowercased before reuse

Impact: case-sensitive paths or query fragments could be corrupted, producing wrong navigation targets.

- Fixed in `src/arc_bot/forum.py` by preserving the raw href for navigation and using lowercase only for matching.

## Low

### 4. Browser sandbox was disabled for all runs, even when not needed

Impact: unnecessary reduction of Chromium isolation when the process is not running as root.

- Fixed in `src/arc_bot/runner.py` so `--no-sandbox` is only added when the current process is running as root.

### 5. State-save failures logged full tracebacks instead of a sanitized error

Impact: low direct risk, but unnecessary traceback logging increases noise and can leak more context than needed.

- Fixed in `src/arc_bot/state.py` by logging a sanitized failure message without traceback spam.

## Residual Risks

1. The project still depends on external Arc page structure and Gmail delivery timing.
2. Live browser automation against third-party pages is inherently brittle under proxy latency.
3. The bot still posts and comments with static template pools, so anti-spam or UI changes may affect runtime success.
