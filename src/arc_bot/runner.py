from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .config import (
    BASE_URL,
    DEFAULT_DAEMON_INTERVAL_HOURS,
    STATE_FILE,
    Account,
    ConfigError,
    account_id,
    describe_proxy,
    load_runtime_accounts,
    session_path,
)
from .logging_utils import safe_exception_message
from .models import AccountResult
from .reporting import build_summary_text, format_gain, known_total_gain, send_summary_notification
from .state import clone_account_state, commit_account_state, load_state, save_state

RUNTIME_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
]

T = TypeVar("T")


async def run_once(args: Any, log: Any, log_file: Path) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ConfigError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `python -m playwright install chromium` before starting a run."
        ) from exc

    log.info("=" * 68)
    log.info("Arc daily run started at %s", datetime.now().isoformat(sep=" ", timespec="seconds"))
    log.info("Logging to %s", log_file.name)
    log.info("Browser mode: %s", "headful" if args.headful else "headless")
    if needs_no_sandbox():
        log.warning("Chromium sandbox is disabled because the current process is running as root.")
    log.info("=" * 68)

    accounts = load_runtime_accounts(log, selected_email=args.account)
    state = load_state(STATE_FILE, log)
    results: list[AccountResult] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not args.headful,
            args=browser_launch_args(),
        )
        try:
            for index, account in enumerate(accounts, start=1):
                account_key = account_id(account.email)
                log.info("%s", "-" * 68)
                log.info("Account %d/%d: %s", index, len(accounts), account_key)
                log.info("%s", "-" * 68)

                result = await run_account(account, browser, state, log)
                results.append(result)
                save_state(state, STATE_FILE, log)

                if index < len(accounts):
                    wait_seconds = random.randint(30, 90)
                    log.info("Waiting %d seconds before the next account", wait_seconds)
                    await asyncio.sleep(wait_seconds)
        finally:
            await browser.close()

    summary_text = build_summary_text(results)
    print(summary_text)
    log.info(
        "Summary complete for %d account(s). Known total gain: %s",
        len(results),
        format_gain(known_total_gain(results)),
    )
    send_summary_notification(summary_text, log)
    return 1 if any(result.error for result in results) else 0


async def run_daemon(args: Any, log: Any, log_file: Path) -> int:
    log.info("=" * 68)
    log.info("Arc daemon started at %s", datetime.now().isoformat(sep=" ", timespec="seconds"))
    log.info("Daemon interval: %.2f hours", args.interval_hours)
    log.info("=" * 68)

    run_count = 0
    interval_seconds = max(1, int(args.interval_hours * 3600 if args.interval_hours else DEFAULT_DAEMON_INTERVAL_HOURS * 3600))

    while True:
        run_count += 1
        started_at = datetime.now()
        log.info("%s", "=" * 68)
        log.info("Daemon cycle %d started at %s", run_count, started_at.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("%s", "=" * 68)

        try:
            await run_once(args, log, log_file)
        except Exception as exc:
            log.error("Daemon cycle %d failed: %s", run_count, safe_exception_message(exc))

        finished_at = datetime.now()
        elapsed_seconds = int((finished_at - started_at).total_seconds())
        wait_seconds = max(0, interval_seconds - elapsed_seconds)
        next_run = finished_at + timedelta(seconds=wait_seconds)

        log.info("Daemon cycle %d finished in %.1f minutes", run_count, elapsed_seconds / 60)
        log.info("Next run scheduled for %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("Sleeping for %.2f hours", wait_seconds / 3600)

        await asyncio.sleep(wait_seconds)


async def run_account(account: Account, browser: Any, state: dict[str, Any], log: Any) -> AccountResult:
    from .auth import is_logged_in, login
    from .browser_utils import capture_debug_screenshot, human_delay, parse_proxy
    from .tasks import comment_on_posts, create_post, get_score, read_content, register_events

    account_key = account_id(account.email)
    result = AccountResult(account_key=account_key)
    staged_state = clone_account_state(state, account_key, legacy_keys=[account.email])
    storage_file = session_path(account.email)

    context = None
    page = None

    context_options: dict[str, Any] = {
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "locale": "en-US",
    }

    if account.proxy:
        context_options["proxy"] = parse_proxy(account.proxy, log)
        log.info("[%s] Using proxy: %s", account_key, describe_proxy(account.proxy))

    try:
        context, page, using_saved_session = await open_account_context(
            browser,
            account,
            context_options,
            storage_file,
            is_logged_in,
            log,
        )

        if using_saved_session:
            log.info("[%s] Using saved browser session", account_key)
        else:
            try:
                await login(page, account, log, account_key)
            except Exception as exc:
                result.error = safe_exception_message(exc)
                log.error("[%s] Login failed: %s", account_key, result.error)
                return result
            await save_browser_session(context, account_key, storage_file, "Saved browser session", log)

        result.score_before = await run_step(
            account_key,
            "score check before tasks",
            lambda: get_score(page, account_key, log),
            None,
            log,
        )

        content_result = await run_step(
            account_key,
            "content tasks",
            lambda: read_content(page, account_key, staged_state, log),
            {"articles": 0, "videos": 0},
            log,
        )
        await human_delay(3, 6)

        event_count = await run_step(
            account_key,
            "event registration task",
            lambda: register_events(page, account_key, staged_state, log),
            0,
            log,
        )
        await human_delay(3, 6)

        post_created = await run_step(
            account_key,
            "post creation task",
            lambda: create_post(page, account_key, log),
            False,
            log,
        )
        await human_delay(3, 6)

        comment_count = await run_step(
            account_key,
            "comment task",
            lambda: comment_on_posts(page, account_key, log),
            0,
            log,
        )

        result.tasks_done = {
            "articles": content_result.get("articles", 0),
            "videos": content_result.get("videos", 0),
            "events": event_count,
            "post": post_created,
            "comments": comment_count,
        }

        await human_delay(3, 6)
        result.score_after = await run_step(
            account_key,
            "score check after tasks",
            lambda: get_score(page, account_key, log),
            None,
            log,
        )

        staged_state["last_run"] = datetime.now().isoformat()
        commit_account_state(state, account_key, staged_state, legacy_keys=[account.email])
        await save_browser_session(context, account_key, storage_file, "Updated browser session", log)
    except Exception as exc:
        result.error = safe_exception_message(exc)
        log.error("[%s] Account run failed: %s", account_key, result.error)
        if page is not None:
            await capture_debug_screenshot(page, "error", account_key, log)
    finally:
        if context is not None:
            await context.close()

    return result


async def run_step(
    account_key: str,
    step_name: str,
    action: Callable[[], Awaitable[T]],
    fallback: T,
    log: Any,
) -> T:
    try:
        return await action()
    except Exception as exc:
        log.error("[%s] %s failed: %s", account_key, step_name, safe_exception_message(exc))
        return fallback


async def open_account_context(
    browser: Any,
    account: Account,
    context_options: dict[str, Any],
    storage_file: Path,
    is_logged_in: Callable[[Any], Awaitable[bool]],
    log: Any,
) -> tuple[Any, Any, bool]:
    from .browser_utils import goto_url_with_retries, human_delay

    if storage_file.exists():
        account_key = account_id(account.email)
        log.info("[%s] Found saved browser session: %s", account_key, storage_file.name)
        context = None
        keep_context_open = False
        try:
            context, page = await new_context(
                browser,
                {**context_options, "storage_state": str(storage_file)},
            )
            await goto_url_with_retries(
                f"{BASE_URL}/home",
                page=page,
                attempts=(("domcontentloaded", 60000), ("commit", 45000)),
                logger=log,
                log_context=f"[{account_key}] saved-session home page",
            )
            await human_delay(2, 3)
            if await is_logged_in(page):
                keep_context_open = True
                return context, page, True

            log.warning("[%s] Saved browser session expired. Re-authentication required.", account_key)
        except Exception as exc:
            log.warning("[%s] Failed to load saved browser session: %s", account_key, safe_exception_message(exc))
        finally:
            if context is not None and not keep_context_open:
                await context.close()

        try:
            storage_file.unlink(missing_ok=True)
        except OSError as exc:
            log.warning(
                "[%s] Failed to remove invalid session file %s: %s",
                account_key,
                storage_file.name,
                safe_exception_message(exc),
            )

    context, page = await new_context(browser, context_options)
    return context, page, False


async def new_context(browser: Any, context_options: dict[str, Any]) -> tuple[Any, Any]:
    context = await browser.new_context(**context_options)
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()
    return context, page


async def save_browser_session(
    context: Any,
    account_key: str,
    storage_file: Path,
    success_message: str,
    log: Any,
) -> None:
    try:
        await context.storage_state(path=str(storage_file))
        log.info("[%s] %s to %s", account_key, success_message, storage_file.name)
    except Exception as exc:
        log.warning(
            "[%s] Failed to write browser session file %s: %s",
            account_key,
            storage_file.name,
            safe_exception_message(exc),
        )


def stop_proxy_tunnels_safely() -> None:
    try:
        from .browser_utils import stop_all_tunnels
    except ImportError:
        return

    stop_all_tunnels()


def browser_launch_args() -> list[str]:
    args = list(RUNTIME_BROWSER_ARGS)
    if needs_no_sandbox():
        args.insert(0, "--no-sandbox")
    return args


def needs_no_sandbox() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    try:
        return geteuid() == 0
    except OSError:
        return False
