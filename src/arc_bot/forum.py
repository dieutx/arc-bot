from __future__ import annotations

import logging
import random
from datetime import datetime

from playwright.async_api import Page

from .browser_utils import (
    click_first_visible,
    collect_unique_hrefs,
    fill_first_visible,
    goto_url_with_retries,
    human_delay,
    scroll_slowly,
)
from .config import BASE_URL
from .logging_utils import redact_sensitive_text, safe_exception_message

POST_CREATE_SELECTORS = (
    "button:has-text('Create a post')",
    "button:has-text('New post')",
    "a:has-text('Create a post')",
)

EDITOR_SELECTORS = (
    "div[contenteditable='true']",
    "textarea",
    ".ql-editor",
    "div[role='textbox']",
)

POST_SUBMIT_SELECTORS = (
    "button:has-text('Post')",
    "button:has-text('Publish')",
    "button:has-text('Submit')",
    "button[type='submit']",
)

COMMENT_TRIGGER_SELECTORS = (
    "button:has-text('Add a comment')",
    "button:has-text('Comment')",
    "button:has-text('Reply')",
)

COMMENT_SUBMIT_SELECTORS = (
    "button:has-text('Post')",
    "button:has-text('Submit')",
    "button:has-text('Reply')",
    "button:has-text('Send')",
    "button[type='submit']",
)

POST_LINK_SELECTOR = (
    "a[href*='/home/forum/'], "
    "a[href*='/home/post/'], "
    "a[href*='/home/discussion']"
)

POST_TEMPLATES = [
    "What are the most exciting use cases you have seen being built on Arc recently? I would love to hear what the community is working on.",
    "For builders using Arc App Kits, what has your experience been like so far? Any tips for getting started faster?",
    "I am curious how the community sees stablecoin adoption trends right now. Are we seeing more real-world usage than last year?",
    "Has anyone attended recent Arc Office Hours? What topics were most useful for builders?",
    "What tooling or documentation improvements would help you most as an Arc developer?",
    "How are developers handling cross-chain UX challenges when building on Arc?",
    "Are there any Arc community projects looking for contributors? I would be happy to help with testing or documentation.",
    "What is your current take on the role of stablecoins in DeFi liquidity?",
]

COMMENT_TEMPLATES = [
    "Great perspective. Thanks for sharing this.",
    "Really useful to read. I appreciate the write-up.",
    "This matches my experience too.",
    "Interesting point. Have you explored how this might work at scale?",
    "Thanks for the detailed explanation. I am bookmarking this for reference.",
    "Solid question. I have been wondering about this as well.",
    "Appreciate the insight. This gave me another angle to consider.",
    "Well said. More discussions like this are helpful for the ecosystem.",
]


async def find_forum_url(page: Page, account_key: str, logger: logging.Logger) -> str:
    default_forum_url = f"{BASE_URL}/home/forum"
    disallowed_keywords = ("club", "group", "member")
    try:
        nav_links = await page.locator("nav a, aside a, [class*='sidebar'] a, [class*='nav'] a").all()
        for link in nav_links:
            raw_href = (await link.get_attribute("href") or "").strip()
            if not raw_href:
                continue
            href = raw_href.lower()
            text = (await link.text_content() or "").lower().strip()
            if any(keyword in href for keyword in disallowed_keywords):
                continue
            if any(
                keyword in text or keyword in href
                for keyword in ("forum", "discussion", "discuss", "community", "post")
            ):
                full_url = raw_href if raw_href.startswith("http") else f"{BASE_URL}{raw_href}"
                logger.info(
                    "[%s] Using forum URL discovered from navigation: %s",
                    account_key,
                    redact_sensitive_text(full_url),
                )
                return full_url
    except Exception as exc:
        logger.warning(
            "[%s] Failed to discover a forum URL from navigation: %s",
            account_key,
            safe_exception_message(exc),
        )

    logger.info("[%s] Falling back to default forum URL: %s", account_key, redact_sensitive_text(default_forum_url))
    return default_forum_url


async def create_post(page: Page, account_key: str, logger: logging.Logger) -> bool:
    logger.info("[%s] Starting discussion post task", account_key)
    forum_url = await find_forum_url(page, account_key, logger)
    await goto_url_with_retries(
        forum_url,
        page=page,
        logger=logger,
        log_context=f"[{account_key}] forum page for post creation",
    )
    await human_delay(3, 5)

    create_selector = await click_first_visible(
        page,
        POST_CREATE_SELECTORS,
        timeout=10000,
        delay_after=(2, 3),
        logger=logger,
        log_context=f"[{account_key}] post creation button",
    )
    if create_selector is None:
        logger.warning("[%s] Post creation button was not found", account_key)
        return False

    title = f"Daily Discussion - {datetime.now().strftime('%B %d')}"
    title_selector = await fill_first_visible(
        page,
        ("input[placeholder*='title' i]", "input[name='title']"),
        title,
        timeout=5000,
        logger=logger,
        log_context=f"[{account_key}] post title input",
    )
    if title_selector is None:
        logger.warning("[%s] Post title input was not found", account_key)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False
    await human_delay(0.5, 1.5)

    post_text = random.choice(POST_TEMPLATES)
    body_selector = await fill_first_visible(
        page,
        EDITOR_SELECTORS,
        post_text,
        timeout=4000,
        logger=logger,
        log_context=f"[{account_key}] post editor",
    )
    if body_selector is None:
        logger.warning("[%s] Post editor was not found. Skipping the post task.", account_key)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    await human_delay(1, 2)

    submit_selector = await click_first_visible(
        page,
        POST_SUBMIT_SELECTORS,
        timeout=3000,
        use_last=True,
        logger=logger,
        log_context=f"[{account_key}] post submit button",
    )
    if submit_selector is None:
        logger.warning("[%s] Post submit button was not found", account_key)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    await human_delay(3, 5)
    logger.info("[%s] Post submitted successfully (+10)", account_key)
    return True


async def comment_on_posts(page: Page, account_key: str, logger: logging.Logger) -> int:
    logger.info("[%s] Starting comment task (target: 2 comments)", account_key)
    forum_url = await find_forum_url(page, account_key, logger)
    await goto_url_with_retries(
        forum_url,
        page=page,
        logger=logger,
        log_context=f"[{account_key}] forum page for comments",
    )
    await human_delay(3, 5)

    hrefs = await collect_unique_hrefs(page, POST_LINK_SELECTOR)
    logger.info("[%s] Found %d discussion post link(s)", account_key, len(hrefs))

    target = 2
    commented = 0
    target_posts = hrefs[1 : target + 4] if len(hrefs) > 1 else hrefs

    for href in target_posts:
        if commented >= target:
            break

        target_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        try:
            await goto_url_with_retries(
                target_url,
                page=page,
                attempts=(("domcontentloaded", 60000), ("commit", 45000)),
                logger=logger,
                log_context=f"[{account_key}] discussion post page",
            )
            await human_delay(2, 4)
            await scroll_slowly(page, steps=3)

            comment_text = random.choice(COMMENT_TEMPLATES)
            editor_selector = await fill_first_visible(
                page,
                EDITOR_SELECTORS,
                comment_text,
                timeout=4000,
                logger=logger,
                log_context=f"[{account_key}] comment editor",
            )

            if editor_selector is None:
                trigger_selector = await click_first_visible(
                    page,
                    COMMENT_TRIGGER_SELECTORS,
                    timeout=4000,
                    delay_after=(1, 2),
                    logger=logger,
                    log_context=f"[{account_key}] comment trigger button",
                )
                if trigger_selector is not None:
                    editor_selector = await fill_first_visible(
                        page,
                        EDITOR_SELECTORS,
                        comment_text,
                        timeout=3000,
                        logger=logger,
                        log_context=f"[{account_key}] comment editor after trigger",
                    )

            if editor_selector is None:
                logger.warning(
                    "[%s] Comment editor was not found on %s",
                    account_key,
                    redact_sensitive_text(target_url),
                )
                continue

            await human_delay(1, 2)

            submit_selector = await click_first_visible(
                page,
                COMMENT_SUBMIT_SELECTORS,
                timeout=3000,
                use_last=True,
                logger=logger,
                log_context=f"[{account_key}] comment submit button",
            )
            if submit_selector is None:
                logger.warning(
                    "[%s] Comment submit button was not found on %s",
                    account_key,
                    redact_sensitive_text(target_url),
                )
                continue

            await human_delay(3, 5)
            commented += 1
            logger.info("[%s] Comment submitted successfully (%d/%d)", account_key, commented, target)
        except Exception as exc:
            logger.error(
                "[%s] Failed to submit comment on %s: %s",
                account_key,
                redact_sensitive_text(target_url),
                safe_exception_message(exc),
            )

    logger.info("[%s] Comment task complete. Submitted %d/%d comment(s) (+%d).", account_key, commented, target, commented * 5)
    return commented
