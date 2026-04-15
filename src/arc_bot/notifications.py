from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .config import load_runtime_env
from .logging_utils import redact_sensitive_text, safe_exception_message


def send_telegram_message(message: str, logger: logging.Logger) -> bool:
    env = load_runtime_env()
    bot_token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.info("Telegram reporting is not configured. Skipping notification.")
        return False

    try:
        for chunk in _chunk_message(redact_sensitive_text(message)):
            request_body = urllib.parse.urlencode(
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=request_body,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                logger.warning("Telegram notification was rejected by the API.")
                return False
        logger.info("Telegram summary sent successfully.")
        return True
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to send Telegram notification: %s", safe_exception_message(exc))
        return False


def _chunk_message(message: str, max_length: int = 3500) -> list[str]:
    if len(message) <= max_length:
        return [message]

    chunks: list[str] = []
    remaining = message
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = max_length
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if len(chunks) == 1:
        return chunks

    return [f"[Part {index}/{len(chunks)}]\n{chunk}" for index, chunk in enumerate(chunks, start=1)]
