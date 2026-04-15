"""
Arc Network daily automation.

Preferred configuration files:
- data/accounts/accounts.local.txt: one Arc login email per line
- data/accounts/gmail_passes.local.txt: one Gmail app password per line, matched by line number
- data/accounts/proxies.local.txt: optional proxy per line, matched by line number

Legacy root config files are still supported for backward compatibility.

Run modes:
- arc-bot --run-once
- python -m arc_bot --daemon
- python arc_daily.py --setup
- python arc_daily.py --setup-cron
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

from .config import DEFAULT_CRON_SCHEDULE, DEFAULT_DAEMON_INTERVAL_HOURS, LOG_DIR, ConfigError, ensure_runtime_dirs
from .logging_utils import configure_logger, safe_exception_message
from .runner import run_daemon, run_once, stop_proxy_tunnels_safely
from .setup_ops import setup_cron, setup_environment

ensure_runtime_dirs()
log, log_file = configure_logger(LOG_DIR)


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
            return asyncio.run(run_daemon(args, log, log_file))
        return asyncio.run(run_once(args, log, log_file))
    except ConfigError as exc:
        log.error("%s", safe_exception_message(exc))
        return 1
    except subprocess.CalledProcessError as exc:
        command = exc.cmd if isinstance(exc.cmd, str) else " ".join(exc.cmd)
        log.error("Command failed with exit code %s: %s", exc.returncode, command)
        return 1
    finally:
        stop_proxy_tunnels_safely()


def main_cli() -> int:
    return main()
