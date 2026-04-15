from __future__ import annotations

import logging
from datetime import datetime

from .models import AccountResult


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
        lines.append(f"  Score before: {format_score(result.score_before)}")
        lines.append(f"  Score after : {format_score(result.score_after)}")
        lines.append(f"  Gained      : {format_gain(gained)}")
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
    lines.append(f"Known gain    : {format_gain(known_total_gain(results))}")
    lines.append(separator)
    return "\n".join(lines)


def format_score(value: int | None) -> str:
    return f"{value:,}" if value is not None else "unavailable"


def format_gain(value: int | None) -> str:
    if value is None:
        return "unavailable"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value}"


def known_total_gain(results: list[AccountResult]) -> int:
    total = 0
    for result in results:
        gained = result.gained()
        if gained is not None:
            total += gained
    return total


def send_summary_notification(summary_text: str, logger: logging.Logger) -> None:
    try:
        from .notifications import send_telegram_message
    except ImportError:
        return

    send_telegram_message(summary_text, logger)
