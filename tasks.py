from __future__ import annotations

import logging
import random
import re
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from browser_utils import (
    capture_debug_screenshot,
    click_first_visible,
    collect_unique_hrefs,
    fill_first_visible,
    goto_with_fallback_paths,
    human_delay,
    scroll_slowly,
)
from config import BASE_URL
from logging_utils import redact_sensitive_text, safe_exception_message

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

CONTENT_LINK_SELECTOR = (
    "a[href*='/home/blogs/'], a[href*='/home/externals/'], a[href*='/home/videos/'], "
    "a[href*='/home/content/'], a[href*='/home/posts/'], a[href*='/home/articles/']"
)
FALLBACK_CONTENT_LINK_SELECTOR = "a[href*='/home/']"
CONTENT_NAV_KEYWORDS = (
    "sign_in",
    "sign_out",
    "profile",
    "settings",
    "events",
    "forum",
    "content",
    "notifications",
    "members",
    "leaderboard",
)

REGISTER_CONFIRM_SELECTORS = (
    "button:has-text('Confirm')",
    "button:has-text('Submit')",
    "button:has-text('OK')",
)

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


async def read_content(page: Page, account_key: str, account_state: dict[str, Any], logger: logging.Logger) -> dict[str, int]:
    logger.info("[%s] Starting content tasks: 5 articles and 1 video", account_key)
    await page.goto(f"{BASE_URL}/home/content", wait_until="domcontentloaded", timeout=90000)
    await human_delay(3, 5)

    target_articles = 5
    target_videos = 1
    articles_read = 0
    videos_watched = 0
    read_history = account_state.setdefault("read_articles", [])

    hrefs = await collect_unique_hrefs(page, CONTENT_LINK_SELECTOR)
    if not hrefs:
        hrefs = await collect_unique_hrefs(
            page,
            FALLBACK_CONTENT_LINK_SELECTOR,
            include=lambda href: not any(keyword in href for keyword in CONTENT_NAV_KEYWORDS),
        )
        logger.info("[%s] Content fallback mode found %d candidate links", account_key, len(hrefs))

    logger.info("[%s] Found %d content links", account_key, len(hrefs))

    new_hrefs = [href for href in hrefs if href not in read_history or "/videos/" in href]
    skipped_count = len(hrefs) - len(new_hrefs)
    if skipped_count > 0:
        logger.info(
            "[%s] Skipped %d previously read article(s). %d unread items remain.",
            account_key,
            skipped_count,
            len(new_hrefs),
        )

    if len(new_hrefs) < target_articles:
        already_read = [href for href in hrefs if href in read_history and "/videos/" not in href]
        needed = target_articles - len(new_hrefs)
        if already_read:
            top_up = already_read[:needed]
            new_hrefs.extend(top_up)
            logger.info(
                "[%s] Not enough unread articles. Reusing %d previously read article(s).",
                account_key,
                len(top_up),
            )

    random.shuffle(new_hrefs)

    for href in new_hrefs:
        if articles_read >= target_articles and videos_watched >= target_videos:
            break

        is_video = "/videos/" in href
        if is_video and videos_watched >= target_videos:
            continue
        if not is_video and articles_read >= target_articles:
            continue

        target_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            await human_delay(1, 2)
            await scroll_slowly(page, steps=random.randint(4, 8))
            dwell_seconds = random.uniform(15, 30)
            logger.info(
                "[%s] Reading content for %.0f seconds: %s",
                account_key,
                dwell_seconds,
                target_url.split("/")[-1][:50],
            )
            await human_delay(dwell_seconds, dwell_seconds)

            if is_video:
                videos_watched += 1
            else:
                articles_read += 1
                if href not in read_history:
                    read_history.append(href)
        except Exception as exc:
            logger.warning(
                "[%s] Skipped content item %s: %s",
                account_key,
                redact_sensitive_text(target_url),
                safe_exception_message(exc),
            )

    logger.info(
        "[%s] Content tasks complete. Articles: %d/%d, videos: %d/%d, total tracked articles: %d",
        account_key,
        articles_read,
        target_articles,
        videos_watched,
        target_videos,
        len(read_history),
    )
    return {"articles": articles_read, "videos": videos_watched}


async def register_events(page: Page, account_key: str, account_state: dict[str, Any], logger: logging.Logger) -> int:
    logger.info("[%s] Starting event registration task", account_key)
    await page.goto(f"{BASE_URL}/home/events", wait_until="domcontentloaded", timeout=90000)
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


async def find_forum_url(page: Page, account_key: str, logger: logging.Logger) -> str:
    default_forum_url = f"{BASE_URL}/home/forum"
    disallowed_keywords = ("club", "group", "member")
    try:
        nav_links = await page.locator("nav a, aside a, [class*='sidebar'] a, [class*='nav'] a").all()
        for link in nav_links:
            href = (await link.get_attribute("href") or "").lower()
            text = (await link.text_content() or "").lower().strip()
            if any(keyword in href for keyword in disallowed_keywords):
                continue
            if any(
                keyword in text or keyword in href
                for keyword in ("forum", "discussion", "discuss", "community", "post")
            ):
                full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
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
    await page.goto(forum_url, wait_until="domcontentloaded", timeout=90000)
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
    await fill_first_visible(
        page,
        ("input[placeholder*='title' i]", "input[name='title']"),
        title,
        timeout=5000,
        logger=logger,
        log_context=f"[{account_key}] post title input",
    )
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
    await page.goto(forum_url, wait_until="domcontentloaded", timeout=90000)
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
            await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
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


def _extract_reasonable_score(text: str) -> int | None:
    numbers = re.findall(r"\d[\d,]*", text.replace(",", ""))
    if not numbers:
        return None
    score = int(numbers[0].replace(",", ""))
    if 0 < score < 1_000_000:
        return score
    return None


async def _safe_text_content(locator: Any, timeout_ms: int) -> str | None:
    try:
        if not await locator.is_visible(timeout=timeout_ms):
            return None
        text = (await locator.text_content(timeout=timeout_ms) or "").strip()
        return text or None
    except Exception:
        return None
