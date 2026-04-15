from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .logging_utils import redact_sensitive_text

BASE_URL = "https://community.arc.network"
PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
SCRIPT_DIR = SRC_DIR.parent
LOG_DIR = SCRIPT_DIR / "logs"
ACCOUNTS_FILE = SCRIPT_DIR / "accounts.txt"
LOCAL_ACCOUNTS_FILE = SCRIPT_DIR / "accounts.local.txt"
GMAIL_PASSES_FILE = SCRIPT_DIR / "gmail_passes.txt"
LOCAL_GMAIL_PASSES_FILE = SCRIPT_DIR / "gmail_passes.local.txt"
PROXIES_FILE = SCRIPT_DIR / "proxies.txt"
LOCAL_PROXIES_FILE = SCRIPT_DIR / "proxies.local.txt"
STATE_FILE = SCRIPT_DIR / "arc_state.json"
SESSIONS_DIR = SCRIPT_DIR / "sessions"
ENV_FILE = SCRIPT_DIR / ".env"
PARENT_ENV_FILE = SCRIPT_DIR.parent / ".env"

DEFAULT_DAEMON_INTERVAL_HOURS = 24.0
DEFAULT_CRON_SCHEDULE = "11 7 * * *"


ACCOUNT_TEMPLATE = """# Arc account emails, one per line
# Lines are matched by position with gmail_passes.txt and proxies.txt.
# Example:
# alice@gmail.com
# bob@example.com
"""


GMAIL_PASSWORD_TEMPLATE = """# Gmail app passwords, one per line
# Lines are matched by position with accounts.txt.
# Generate these from Google Account -> Security -> App passwords.
# Example:
# abcd efgh ijkl mnop
# wxyz abcd efgh ijkl
"""


PROXY_TEMPLATE = """# Optional proxies, one per line
# Lines are matched by position with accounts.txt.
# Use "none" or leave a line blank to run an account without a proxy.
# Supported formats:
# http://user:pass@host:port
# https://user:pass@host:port
# socks5://user:pass@host:port
# http://host:port
"""


@dataclass(slots=True)
class Account:
    email: str
    app_pass: str
    proxy: str | None = None


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


def ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_config_templates() -> None:
    _write_template_if_missing(ACCOUNTS_FILE, ACCOUNT_TEMPLATE)
    _write_template_if_missing(GMAIL_PASSES_FILE, GMAIL_PASSWORD_TEMPLATE)
    _write_template_if_missing(PROXIES_FILE, PROXY_TEMPLATE)


def describe_proxy(proxy_url: str) -> str:
    return proxy_url.split("@")[-1]


def account_id(email: str) -> str:
    normalized = email.strip().lower()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"acct_{digest}"


def session_path(email: str) -> Path:
    ensure_runtime_dirs()
    return SESSIONS_DIR / f"{account_id(email)}.json"


def log_artifact_path(prefix: str, account_key: str, suffix: str = ".png") -> Path:
    ensure_runtime_dirs()
    return LOG_DIR / f"{prefix}_{account_key}{suffix}"


def mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return account_id(email)
    if len(local) <= 2:
        masked_local = local[:1] + "*"
    else:
        masked_local = local[:1] + "*" * (len(local) - 2) + local[-1:]
    return f"{masked_local}@{domain}"


def load_runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    for env_file in (ENV_FILE, PARENT_ENV_FILE):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in env:
                env[key] = value
    return env


def read_non_comment_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def load_runtime_accounts(
    logger: logging.Logger,
    selected_email: str | None = None,
) -> list[Account]:
    ensure_runtime_dirs()
    ensure_config_templates()

    emails = _load_required_lines(
        _resolve_config_file(LOCAL_ACCOUNTS_FILE, ACCOUNTS_FILE),
        ACCOUNT_TEMPLATE,
        "No valid account emails were found in accounts.local.txt or accounts.txt.",
        "No valid account emails were found in accounts.local.txt or accounts.txt.",
    )
    _reject_legacy_account_format(emails)

    gmail_passwords = _load_required_lines(
        _resolve_config_file(LOCAL_GMAIL_PASSES_FILE, GMAIL_PASSES_FILE),
        GMAIL_PASSWORD_TEMPLATE,
        "No valid Gmail app passwords were found in gmail_passes.local.txt or gmail_passes.txt.",
        "No valid Gmail app passwords were found in gmail_passes.local.txt or gmail_passes.txt.",
    )

    if len(gmail_passwords) < len(emails):
        raise ConfigError(
            "The Gmail app password file has fewer entries than the account file. "
            "Each account email must have a matching Gmail app password."
        )
    if len(gmail_passwords) > len(emails):
        logger.warning(
            "%s has %d entries for %d accounts. Extra lines will be ignored.",
            _resolve_config_file(LOCAL_GMAIL_PASSES_FILE, GMAIL_PASSES_FILE).name,
            len(gmail_passwords),
            len(emails),
        )

    proxies = _load_proxies(len(emails), logger)
    accounts = [
        Account(email=email, app_pass=app_pass, proxy=proxy)
        for email, app_pass, proxy in zip(emails, gmail_passwords, proxies)
    ]

    if selected_email:
        accounts = [
            account
            for account in accounts
            if account.email.lower() == selected_email.strip().lower()
        ]
        if not accounts:
            raise ConfigError("Selected account was not found in the account configuration.")
        logger.info("Running only the selected account: %s", account_id(accounts[0].email))

    logger.info(
        "Loaded %d accounts from %s",
        len(accounts),
        _resolve_config_file(LOCAL_ACCOUNTS_FILE, ACCOUNTS_FILE).name,
    )
    logger.info(
        "Loaded %d Gmail app passwords from %s",
        len(accounts),
        _resolve_config_file(LOCAL_GMAIL_PASSES_FILE, GMAIL_PASSES_FILE).name,
    )
    return accounts


def _load_proxies(count: int, logger: logging.Logger) -> list[str | None]:
    proxy_file = _resolve_config_file(LOCAL_PROXIES_FILE, PROXIES_FILE)
    if not proxy_file.exists():
        _write_template_if_missing(PROXIES_FILE, PROXY_TEMPLATE)
        logger.warning(
            "No proxy file was found. All accounts will run without a proxy."
        )
        return [None] * count

    raw_lines = read_non_comment_lines(proxy_file)
    proxies = [None if line.lower() == "none" else line for line in raw_lines]

    if len(proxies) < count:
        missing = count - len(proxies)
        logger.warning(
            "%s has %d entries for %d accounts. %d account(s) will run without a proxy.",
            proxy_file.name,
            len(proxies),
            count,
            missing,
        )
        proxies.extend([None] * missing)
    elif len(proxies) > count:
        logger.warning(
            "%s has %d entries for %d accounts. Extra lines will be ignored.",
            proxy_file.name,
            len(proxies),
            count,
        )

    for index, proxy in enumerate(proxies[:count], start=1):
        if proxy is None:
            logger.warning(
                "Account %d has no proxy configured. The account will use a direct connection.",
                index,
            )
            continue
        if not re.match(r"^(http|https|socks5)://", proxy):
            raise ConfigError(
                f"Invalid proxy format on line {index} of {proxy_file.name}: "
                f"{redact_sensitive_text(proxy)!r}. "
                "Use http://, https://, or socks5://."
            )

    logger.info("Loaded proxy settings for %d accounts from %s", count, proxy_file.name)
    return proxies[:count]


def _load_required_lines(
    path: Path,
    template: str,
    missing_message: str,
    empty_message: str,
) -> list[str]:
    if not path.exists():
        raise ConfigError(missing_message)

    lines = read_non_comment_lines(path)
    if not lines:
        raise ConfigError(empty_message)
    return lines


def _reject_legacy_account_format(emails: list[str]) -> None:
    if any("----" in email for email in emails):
        raise ConfigError(
            "Legacy combined email/password entries were detected in accounts.txt. "
            "Put only email addresses in accounts.txt and move Gmail app passwords "
            "to gmail_passes.txt."
        )


def _write_template_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _resolve_config_file(local_path: Path, default_path: Path) -> Path:
    if local_path.exists():
        return local_path
    return default_path
