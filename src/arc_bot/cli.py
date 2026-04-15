"""
Arc Network daily automation.

Configuration files in the project root:
- accounts.local.txt: one Arc login email per line
- gmail_passes.local.txt: one Gmail app password per line, matched by line number
- proxies.local.txt: optional proxy per line, matched by line number

Run modes:
- arc-bot --run-once
- python -m arc_bot --daemon
- python arc_daily.py --setup
- python arc_daily.py --setup-cron
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .config import (
    BASE_URL,
    DEFAULT_CRON_SCHEDULE,
    DEFAULT_DAEMON_INTERVAL_HOURS,
    ACCOUNTS_FILE,
    LOCAL_ACCOUNTS_FILE,
    GMAIL_PASSES_FILE,
    LOCAL_GMAIL_PASSES_FILE,
    LOG_DIR,
    PROXIES_FILE,
    LOCAL_PROXIES_FILE,
    SCRIPT_DIR,
    STATE_FILE,
    Account,
    ConfigError,
    account_id,
    describe_proxy,
    ensure_config_templates,
    ensure_runtime_dirs,
    load_runtime_accounts,
    read_non_comment_lines,
    session_path,
)
from .logging_utils import configure_logger, safe_exception_message
from .state import clone_account_state, commit_account_state, load_state, save_state

ensure_runtime_dirs()
log, log_file = configure_logger(LOG_DIR)

RUNTIME_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
]

T = TypeVar("T")


@dataclass(slots=True)
class AccountResult:
    account_key: str
    score_before: int | None = None
    score_after: int | None = None
    tasks_done: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def gained(self) -> int | None:
        if self.score_before is None or self.score_after is None:
            return None
        return self.score_after - self.score_before


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Arc Network daily automation tasks.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--run-once",
        action="store_true",
        help="Run all configured accounts once and exit (default).",
    )
    mode_group.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously with a 24 hour interval between runs.",
    )
    mode_group.add_argument(
        "--setup",
        action="store_true",
        help="Install Python dependencies, install Chromium, and review local config files.",
    )
    mode_group.add_argument(
        "--setup-cron",
        action="store_true",
        help="Install a cron entry that runs this script with --run-once.",
    )
    parser.add_argument(
        "--account",
        help="Run only the exact email address listed in the local account configuration.",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Launch Chromium in headful mode for debugging.",
    )
    parser.add_argument(
        "--cron-schedule",
        default=DEFAULT_CRON_SCHEDULE,
        help=(
            "Cron schedule used by --setup-cron. Default: "
            f"{DEFAULT_CRON_SCHEDULE!r} with CRON_TZ=Asia/Ho_Chi_Minh."
        ),
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=DEFAULT_DAEMON_INTERVAL_HOURS,
        help="Loop interval used by --daemon. Default: 24 hours.",
    )
    return parser


async def run_once(args: argparse.Namespace) -> int:
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
    log.info("=" * 68)

    accounts = load_runtime_accounts(log, selected_email=args.account)
    state = load_state(STATE_FILE, log)
    results: list[AccountResult] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not args.headful,
            args=RUNTIME_BROWSER_ARGS,
        )
        try:
            for index, account in enumerate(accounts, start=1):
                account_key = account_id(account.email)
                log.info("%s", "-" * 68)
                log.info("Account %d/%d: %s", index, len(accounts), account_key)
                log.info("%s", "-" * 68)

                result = await run_account(account, browser, state)
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
        _format_gain(_known_total_gain(results)),
    )
    _send_summary_notification(summary_text)
    return 1 if any(result.error for result in results) else 0


async def run_daemon(args: argparse.Namespace) -> int:
    log.info("=" * 68)
    log.info("Arc daemon started at %s", datetime.now().isoformat(sep=" ", timespec="seconds"))
    log.info("Daemon interval: %.2f hours", args.interval_hours)
    log.info("=" * 68)

    run_count = 0
    interval_seconds = max(1, int(args.interval_hours * 3600))

    while True:
        run_count += 1
        started_at = datetime.now()
        log.info("%s", "=" * 68)
        log.info("Daemon cycle %d started at %s", run_count, started_at.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("%s", "=" * 68)

        try:
            await run_once(args)
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


async def run_account(account: Account, browser: Any, state: dict[str, Any]) -> AccountResult:
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
        context, page, using_saved_session = await _open_account_context(
            browser,
            account,
            context_options,
            storage_file,
            is_logged_in,
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
            await _save_browser_session(context, account_key, storage_file, "Saved browser session")

        result.score_before = await _run_step(
            account_key,
            "score check before tasks",
            lambda: get_score(page, account_key, log),
            None,
        )

        content_result = await _run_step(
            account_key,
            "content tasks",
            lambda: read_content(page, account_key, staged_state, log),
            {"articles": 0, "videos": 0},
        )
        await human_delay(3, 6)

        event_count = await _run_step(
            account_key,
            "event registration task",
            lambda: register_events(page, account_key, staged_state, log),
            0,
        )
        await human_delay(3, 6)

        post_created = await _run_step(
            account_key,
            "post creation task",
            lambda: create_post(page, account_key, log),
            False,
        )
        await human_delay(3, 6)

        comment_count = await _run_step(
            account_key,
            "comment task",
            lambda: comment_on_posts(page, account_key, log),
            0,
        )

        result.tasks_done = {
            "articles": content_result.get("articles", 0),
            "videos": content_result.get("videos", 0),
            "events": event_count,
            "post": post_created,
            "comments": comment_count,
        }

        await human_delay(3, 6)
        result.score_after = await _run_step(
            account_key,
            "score check after tasks",
            lambda: get_score(page, account_key, log),
            None,
        )

        staged_state["last_run"] = datetime.now().isoformat()
        commit_account_state(state, account_key, staged_state, legacy_keys=[account.email])
        await _save_browser_session(context, account_key, storage_file, "Updated browser session")
    except Exception as exc:
        result.error = safe_exception_message(exc)
        log.error("[%s] Account run failed: %s", account_key, result.error)
        if page is not None:
            await capture_debug_screenshot(page, "error", account_key, log)
    finally:
        if context is not None:
            await context.close()

    return result


def build_summary_text(results: list[AccountResult]) -> str:
    separator = "=" * 76
    lines = ["", separator, f"Arc Daily Summary | {datetime.now().strftime('%Y-%m-%d %H:%M')}", separator]

    for result in results:
        gained = result.gained()

        status = "OK" if not result.error else "FAILED"
        lines.append("")
        lines.append(f"[{status}] {result.account_key}")
        if result.error:
            lines.append(f"  Error       : {result.error}")
            continue

        tasks = result.tasks_done
        lines.append(f"  Score before: {_format_score(result.score_before)}")
        lines.append(f"  Score after : {_format_score(result.score_after)}")
        lines.append(f"  Gained      : {_format_gain(gained)}")
        lines.append(
            "  Tasks       : "
            f"Articles {tasks.get('articles', 0)}/5 | "
            f"Videos {tasks.get('videos', 0)}/1 | "
            f"Events {tasks.get('events', 0)} | "
            f"Post {'yes' if tasks.get('post') else 'no'} | "
            f"Comments {tasks.get('comments', 0)}/2"
        )

    lines.append("")
    lines.append(separator)
    lines.append(f"Accounts      : {len(results)}")
    lines.append(f"Known gain    : {_format_gain(_known_total_gain(results))}")
    lines.append(separator)
    return "\n".join(lines)


def setup_environment() -> None:
    import platform

    ensure_config_templates()

    steps: list[tuple[str, list[str]]] = [
        (
            "Install the project in editable mode",
            [sys.executable, "-m", "pip", "install", "-e", str(SCRIPT_DIR)],
        ),
        (
            "Install Chromium for Playwright",
            [sys.executable, "-m", "playwright", "install", "chromium"],
        ),
    ]
    if platform.system() == "Linux":
        steps.append(
            (
                "Install Linux browser dependencies for Chromium",
                [sys.executable, "-m", "playwright", "install-deps", "chromium"],
            )
        )

    print("=" * 72)
    print("Arc Bot setup")
    print("=" * 72)

    total_steps = len(steps) + 1
    for index, (description, command) in enumerate(steps, start=1):
        print(f"\n[{index}/{total_steps}] {description}")
        subprocess.run(command, check=True)

    print(f"\n[{total_steps}/{total_steps}] Review local configuration files")
    _print_config_status()

    module_command = f"{shlex.quote(sys.executable)} -m arc_bot"
    legacy_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_DIR / 'arc_daily.py'))}"
    print("\nNext steps:")
    print(f"  {module_command} --run-once")
    print(f"  {module_command} --daemon")
    print(f"  {legacy_command} --setup-cron")


def setup_cron(schedule: str) -> None:
    import platform

    script_path = SCRIPT_DIR / "arc_daily.py"
    python_bin = shlex.quote(sys.executable)
    quoted_script = shlex.quote(str(script_path))
    quoted_log = shlex.quote(str(LOG_DIR / "arc_cron.log"))
    cron_command = f"cd {shlex.quote(str(SCRIPT_DIR))} && {python_bin} {quoted_script} --run-once >> {quoted_log} 2>&1"
    cron_entry = f"{schedule} {cron_command}"
    cron_timezone = "CRON_TZ=Asia/Ho_Chi_Minh"

    print("=" * 72)
    print("Arc Bot cron setup")
    print("=" * 72)

    if platform.system() == "Windows":
        print("Windows does not use cron. Create a Task Scheduler job with this command:")
        print(f"  {sys.executable} {script_path} --run-once")
        return

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing_crontab = result.stdout if result.returncode == 0 else ""
        filtered_lines = [
            line
            for line in existing_crontab.splitlines()
            if str(script_path) not in line
        ]
        filtered_lines = [line for line in filtered_lines if line.strip() != cron_timezone]
        filtered_lines.append(cron_timezone)
        filtered_lines.append(cron_entry)

        new_crontab = "\n".join(filtered_lines).rstrip("\n") + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)

        print("Cron entry installed successfully.")
        print("Timezone : Asia/Ho_Chi_Minh")
        print(f"Schedule : {schedule}")
        print(f"Command  : {cron_command}")
    except FileNotFoundError:
        print("crontab was not found on this system. Add the following command to your scheduler manually:")
        print(f"  {cron_timezone}")
        print(f"  {cron_entry}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.setup:
            setup_environment()
            return 0

        if args.setup_cron:
            setup_cron(args.cron_schedule)
            return 0

        if args.daemon:
            return asyncio.run(run_daemon(args))
        return asyncio.run(run_once(args))
    except ConfigError as exc:
        log.error("%s", safe_exception_message(exc))
        return 1
    except subprocess.CalledProcessError as exc:
        command = exc.cmd if isinstance(exc.cmd, str) else " ".join(exc.cmd)
        log.error("Command failed with exit code %s: %s", exc.returncode, command)
        return 1
    finally:
        _stop_proxy_tunnels_safely()


async def _run_step(
    account_key: str,
    step_name: str,
    action: Callable[[], Awaitable[T]],
    fallback: T,
) -> T:
    try:
        return await action()
    except Exception as exc:
        log.error("[%s] %s failed: %s", account_key, step_name, safe_exception_message(exc))
        return fallback


async def _open_account_context(
    browser: Any,
    account: Account,
    context_options: dict[str, Any],
    storage_file: Path,
    is_logged_in: Callable[[Any], Awaitable[bool]],
) -> tuple[Any, Any, bool]:
    from .browser_utils import human_delay

    if storage_file.exists():
        account_key = account_id(account.email)
        log.info("[%s] Found saved browser session: %s", account_key, storage_file.name)
        context = None
        keep_context_open = False
        try:
            context, page = await _new_context(
                browser,
                {**context_options, "storage_state": str(storage_file)},
            )
            await page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=60000)
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

    context, page = await _new_context(browser, context_options)
    return context, page, False


async def _new_context(browser: Any, context_options: dict[str, Any]) -> tuple[Any, Any]:
    context = await browser.new_context(**context_options)
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()
    return context, page


async def _save_browser_session(
    context: Any,
    account_key: str,
    storage_file: Path,
    success_message: str,
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


def _stop_proxy_tunnels_safely() -> None:
    try:
        from .browser_utils import stop_all_tunnels
    except ImportError:
        return

    stop_all_tunnels()


def _print_config_status() -> None:
    files = [
        (
            LOCAL_ACCOUNTS_FILE,
            True,
            "Add one Arc login email per line in accounts.local.txt.",
        ),
        (
            LOCAL_GMAIL_PASSES_FILE,
            True,
            "Add one Gmail app password per line in gmail_passes.local.txt. The order must match accounts.local.txt.",
        ),
        (
            LOCAL_PROXIES_FILE,
            False,
            "Optional. Add one proxy per line in proxies.local.txt, or leave the file blank to run direct connections.",
        ),
    ]

    for path, required, hint in files:
        lines = read_non_comment_lines(path)
        if path == LOCAL_ACCOUNTS_FILE and any("----" in line for line in lines):
            print(f"  {path.name}: invalid legacy format detected. {hint}")
            continue

        if lines:
            print(f"  {path.name}: {len(lines)} configured entr{'y' if len(lines) == 1 else 'ies'}")
            continue

        if required:
            print(f"  {path.name}: missing required content. {hint}")
        else:
            print(f"  {path.name}: empty. {hint}")


def _format_score(value: int | None) -> str:
    return f"{value:,}" if value is not None else "unavailable"


def _format_gain(value: int | None) -> str:
    if value is None:
        return "unavailable"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value}"


def _known_total_gain(results: list[AccountResult]) -> int:
    total = 0
    for result in results:
        gained = result.gained()
        if gained is not None:
            total += gained
    return total


def _send_summary_notification(summary_text: str) -> None:
    try:
        from .notifications import send_telegram_message
    except ImportError:
        return

    send_telegram_message(summary_text, log)


def main_cli() -> int:
    return main()
