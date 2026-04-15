from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from .browser_utils import click_first_visible, goto_url_with_retries, human_delay
from .config import BASE_URL
from .logging_utils import safe_exception_message

REGISTER_CONFIRM_SELECTORS = (
    "button:has-text('Confirm')",
    "button:has-text('Submit')",
    "button:has-text('OK')",
)


async def register_events(page: Page, account_key: str, account_state: dict[str, Any], logger: logging.Logger) -> int:
    logger.info("[%s] Starting event registration task", account_key)
    await goto_url_with_retries(
        f"{BASE_URL}/home/events",
        page=page,
        logger=logger,
        log_context=f"[{account_key}] events page",
    )
    await human_delay(2, 3)

    upcoming_clicked = await click_first_visible(
        page,
        ("button:has-text('Upcoming')",),
        timeout=3000,
        delay_after=(1, 2),
        logger=logger,
        log_context=f"[{account_key}] upcoming events tab",
    )
    if upcoming_clicked is not None:
        logger.info("[%s] Switched to the Upcoming events tab", account_key)

    register_buttons = page.locator("button:has-text('Register')")
    button_count = await register_buttons.count()
    logger.info("[%s] Found %d Register button(s)", account_key, button_count)

    registered_count = 0
    registered_events = account_state.setdefault("registered_events", [])

    for index in range(button_count):
        button = register_buttons.nth(index)
        try:
            card = button.locator(
                "xpath=ancestor::div[contains(@class,'CardContainer') or contains(@class,'card')]"
            ).first
            title_locator = card.locator("h3, h2").first
            title = await _safe_text_content(title_locator, timeout_ms=3000)
            if not title:
                title = f"Event_{index + 1}"

            if title in registered_events:
                logger.info("[%s] Skipped event already recorded in local state: %s", account_key, title)
                continue

            logger.info("[%s] Registering event: %s", account_key, title)
            try:
                await button.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            await human_delay(1, 2)
            await button.click()
            await human_delay(2, 4)

            confirm_selector = await click_first_visible(
                page,
                REGISTER_CONFIRM_SELECTORS,
                timeout=3000,
                use_last=True,
                delay_after=(1, 2),
                logger=logger,
                log_context=f"[{account_key}] event confirmation button",
            )
            if confirm_selector is not None:
                logger.info("[%s] Confirmed event registration using %s", account_key, confirm_selector)

            await click_first_visible(
                page,
                ("button[aria-label='Close']", "[class*='close']"),
                timeout=2000,
                delay_after=(1, 2),
                logger=logger,
                log_context=f"[{account_key}] event dialog close button",
            )

            registered_events.append(title)
            registered_count += 1
            logger.info("[%s] Event registered successfully (+5): %s", account_key, title)

            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await human_delay(2, 3)
        except Exception as exc:
            logger.warning(
                "[%s] Failed to register event #%d: %s",
                account_key,
                index + 1,
                safe_exception_message(exc),
            )

    logger.info(
        "[%s] Event task complete. Registered %d event(s) (+%d).",
        account_key,
        registered_count,
        registered_count * 5,
    )
    return registered_count


async def _safe_text_content(locator: Any, timeout_ms: int) -> str | None:
    try:
        if not await locator.is_visible(timeout=timeout_ms):
            return None
        text = (await locator.text_content(timeout=timeout_ms) or "").strip()
        return text or None
    except Exception:
        return None
