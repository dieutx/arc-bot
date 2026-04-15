from __future__ import annotations

import logging
import random
from typing import Any

from playwright.async_api import Page

from .browser_utils import collect_unique_hrefs, goto_url_with_retries, human_delay, scroll_slowly
from .config import BASE_URL
from .logging_utils import redact_sensitive_text, safe_exception_message

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


async def read_content(page: Page, account_key: str, account_state: dict[str, Any], logger: logging.Logger) -> dict[str, int]:
    logger.info("[%s] Starting content tasks: 5 articles and 1 video", account_key)
    await goto_url_with_retries(
        f"{BASE_URL}/home/content",
        page=page,
        logger=logger,
        log_context=f"[{account_key}] content page",
    )
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
            await goto_url_with_retries(
                target_url,
                page=page,
                attempts=(("domcontentloaded", 60000), ("commit", 45000)),
                logger=logger,
                log_context=f"[{account_key}] content item",
            )
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
