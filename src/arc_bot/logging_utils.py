from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_GMAIL_APP_PASSWORD_RE = re.compile(r"\b(?:[a-z]{4}\s){3}[a-z]{4}\b", re.IGNORECASE)
_PROXY_AUTH_RE = re.compile(r"((?:http|https|socks5)://)([^:@/\s]+):([^@/\s]+)@", re.IGNORECASE)
_SENSITIVE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_PARAM_RE = re.compile(
    r"([?&](?:token|auth|key|code|password|pass|sig|signature)=)[^&\s]+",
    re.IGNORECASE,
)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    text = _SENSITIVE_URL_RE.sub(lambda match: _redact_url(match.group(0)), text)
    text = _PROXY_AUTH_RE.sub(r"\1[redacted]@", text)
    text = _TELEGRAM_TOKEN_RE.sub("[redacted-token]", text)
    text = _GMAIL_APP_PASSWORD_RE.sub("[redacted-app-password]", text)
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _SENSITIVE_PARAM_RE.sub(r"\1[redacted]", text)
    return text


def safe_exception_message(exc: BaseException | object) -> str:
    message = redact_sensitive_text(exc)
    return message or exc.__class__.__name__


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)

        if isinstance(record.args, dict):
            record.args = {
                key: _sanitize_log_value(value)
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(_sanitize_log_value(value) for value in record.args)

        return True


def _sanitize_log_value(value: Any) -> Any:
    if isinstance(value, BaseException):
        return safe_exception_message(value)
    if isinstance(value, (str, Path)):
        return redact_sensitive_text(value)
    return value


def _redact_url(url: str) -> str:
    lowered = url.lower()
    if any(
        keyword in lowered
        for keyword in ("magic", "token", "auth", "confirm", "sign_in", "login", "password", "pass=")
    ):
        return "[redacted-url]"
    return _SENSITIVE_PARAM_RE.sub(r"\1[redacted]", _PROXY_AUTH_RE.sub(r"\1[redacted]@", url))


def configure_logger(log_dir: Path, logger_name: str = "arc") -> tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"arc_daily_{date.today().isoformat()}.log"

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    redacting_filter = RedactingFilter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redacting_filter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redacting_filter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger, log_file
