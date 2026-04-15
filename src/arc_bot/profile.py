from __future__ import annotations

import logging
import re

from playwright.async_api import Page

from .browser_utils import capture_debug_screenshot, goto_with_fallback_paths, human_delay
from .config import BASE_URL
from .logging_utils import safe_exception_message

PROFILE_PATHS = (
    "/home/profile",
    "/home/member/profile",
    "/profile",
    "/home/account",
)

SCORE_SELECTORS = (
    "[class*='point' i]",
    "[class*='score' i]",
    "[class*='credit' i]",
    "[class*='reward' i]",
    "span:has-text('points')",
    "span:has-text('Points')",
    "div:has-text('points')",
    "[class*='stat'] [class*='number']",
    "[class*='badge'] [class*='count']",
)


async def get_score(page: Page, account_key: str, logger: logging.Logger) -> int | None:
    try:
        path, _ = await goto_with_fallback_paths(
            page,
            BASE_URL,
            PROFILE_PATHS,
            timeout=60000,
            logger=logger,
            log_context=f"[{account_key}] profile page lookup",
        )
        if path is None:
            logger.warning("[%s] Failed to find a working profile page. Score check skipped.", account_key)
            return None

        await human_delay(2, 4)

        for selector in SCORE_SELECTORS:
            try:
                elements = page.locator(selector)
                count = await elements.count()
                for index in range(min(count, 5)):
                    text = (await elements.nth(index).text_content() or "").strip()
                    score = _extract_reasonable_score(text)
                    if score is not None:
                        logger.info("[%s] Current score: %d", account_key, score)
                        return score
            except Exception as exc:
                logger.debug("[%s] Score selector %r failed: %s", account_key, selector, exc)

        screenshot_path = await capture_debug_screenshot(page, "profile", account_key, logger)
        logger.warning(
            "[%s] Failed to read the score from the profile page. Screenshot saved to %s.",
            account_key,
            screenshot_path,
        )
        return None
    except Exception as exc:
        logger.warning(
            "[%s] Failed to read score from the profile page: %s",
            account_key,
            safe_exception_message(exc),
        )
        return None


def _extract_reasonable_score(text: str) -> int | None:
    numbers = re.findall(r"\d[\d,]*", text.replace(",", ""))
    if not numbers:
        return None
    score = int(numbers[0].replace(",", ""))
    if 0 < score < 1_000_000:
        return score
    return None
