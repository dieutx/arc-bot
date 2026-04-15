from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any


def load_state(state_file: Path, logger: logging.Logger) -> dict[str, dict[str, Any]]:
    if not state_file.exists():
        return {}

    try:
        raw_state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "Failed to parse %s. Starting with an empty state file: %s",
            state_file.name,
            exc,
        )
        return {}

    if not isinstance(raw_state, dict):
        logger.warning(
            "State file %s is not a JSON object. Starting with an empty state.",
            state_file.name,
        )
        return {}

    normalized_state: dict[str, dict[str, Any]] = {}
    for email, account_state in raw_state.items():
        if not isinstance(email, str) or not email:
            logger.warning("Skipped an invalid state entry with a non-string account key.")
            continue
        normalized_state[email] = normalize_account_state(account_state)

    return normalized_state


def save_state(
    state: dict[str, dict[str, Any]],
    state_file: Path,
    logger: logging.Logger,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    normalized_state = {
        email: normalize_account_state(account_state)
        for email, account_state in state.items()
        if isinstance(email, str) and email
    }

    fd, temp_path = tempfile.mkstemp(
        prefix=f"{state_file.stem}_",
        suffix=".tmp",
        dir=str(state_file.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(normalized_state, temp_file, indent=2, ensure_ascii=False)
            temp_file.write("\n")
        os.replace(temp_path, state_file)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        logger.error("Failed to save %s", state_file.name, exc_info=True)
        raise


def clone_account_state(
    state: dict[str, dict[str, Any]],
    account_key: str,
    legacy_keys: list[str] | None = None,
) -> dict[str, Any]:
    keys = [account_key]
    if legacy_keys:
        keys.extend(legacy_keys)
    for key in keys:
        if key in state:
            return copy.deepcopy(normalize_account_state(state.get(key)))
    return copy.deepcopy(normalize_account_state(None))


def commit_account_state(
    state: dict[str, dict[str, Any]],
    account_key: str,
    account_state: dict[str, Any],
    legacy_keys: list[str] | None = None,
) -> None:
    if legacy_keys:
        for key in legacy_keys:
            if key != account_key:
                state.pop(key, None)
    state[account_key] = normalize_account_state(account_state)


def normalize_account_state(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}

    registered_events = data.get("registered_events", [])
    if not isinstance(registered_events, list):
        registered_events = []
    registered_events = [str(item) for item in registered_events if item]

    read_articles = data.get("read_articles", [])
    if not isinstance(read_articles, list):
        read_articles = []
    read_articles = [str(item) for item in read_articles if item]

    last_run = data.get("last_run")
    if last_run is not None and not isinstance(last_run, str):
        last_run = str(last_run)

    return {
        "registered_events": registered_events,
        "read_articles": read_articles,
        "last_run": last_run,
    }
