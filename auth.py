from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import Message

from playwright.async_api import Page

from browser_utils import (
    capture_debug_screenshot,
    click_first_visible,
    fill_first_visible,
    find_first_visible,
    human_delay,
)
from config import Account, BASE_URL
from logging_utils import safe_exception_message

LOGIN_EMAIL_SELECTORS = (
    "input[type='email']",
    "input[name='email']",
    "input[placeholder*='email' i]",
)

LOGIN_SUBMIT_SELECTORS = (
    "button[type='submit']",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "button:has-text('Continue')",
    "button:has-text('Send')",
)

LOGIN_CONFIRM_SELECTORS = (
    "button:has-text('Confirm')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "button:has-text('Continue')",
    "a:has-text('Confirm')",
    "a:has-text('Sign in')",
)

LOGGED_IN_SELECTORS = (
    "[class*='avatar' i]",
    "[class*='user-menu' i]",
    "button:has-text('Sign out')",
    "a:has-text('Sign out')",
    "button:has-text('Log out')",
    "[data-testid*='user']",
    "[aria-label*='profile' i]",
    "[class*='profile' i]",
)


def fetch_magic_link(
    email_address: str,
    app_password: str,
    logger: logging.Logger,
    account_key: str,
    timeout_sec: int = 120,
) -> str | None:
    logger.info(
        "[%s] Waiting for the magic link email (timeout: %d seconds)",
        account_key,
        timeout_sec,
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_sec)

    while datetime.now(timezone.utc) < deadline:
        mail: imaplib.IMAP4_SSL | None = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(email_address, app_password)
            mail.select("INBOX")

            since = (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).strftime("%d-%b-%Y")

            _, data = mail.search(None, f'(UNSEEN SINCE "{since}" FROM "circle")')
            if not data or not data[0]:
                _, data = mail.search(None, f'(UNSEEN SINCE "{since}")')

            message_ids = data[0].split() if data and data[0] else []
            for message_id in reversed(message_ids):
                _, message_data = mail.fetch(message_id, "(RFC822)")
                raw_message = message_data[0][1]
                message = email_lib.message_from_bytes(raw_message)

                sender = message.get("From", "").lower()
                subject = message.get("Subject", "").lower()
                if not any(
                    keyword in sender or keyword in subject
                    for keyword in ("arc", "circle", "sign in", "login", "magic", "confirm")
                ):
                    continue

                body = _extract_email_body(message)
                urls = re.findall(
                    r'https?://[^\s"\'<>]+(?:magic|token|sign_in|confirm|auth)[^\s"\'<>]*',
                    body,
                )
                if not urls:
                    urls = re.findall(
                        r'https?://(?:[^\s"\'<>]*arc\.network|[^\s"\'<>]*circle)[^\s"\'<>]*',
                        body,
                    )

                if urls:
                    magic_link = urls[0].rstrip(".")
                    mail.store(message_id, "+FLAGS", "\\Seen")
                    logger.info("[%s] Magic link email received", account_key)
                    return magic_link
        except Exception as exc:
            logger.warning("[%s] IMAP check failed: %s", account_key, safe_exception_message(exc))
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass

        remaining = int((deadline - datetime.now(timezone.utc)).total_seconds())
        logger.info(
            "[%s] Magic link email not found yet. Retrying in 8 seconds (%d seconds remaining)",
            account_key,
            max(0, remaining),
        )
        time.sleep(8)

    logger.error("[%s] Magic link not received within timeout", account_key)
    return None


async def is_logged_in(page: Page) -> bool:
    current_url = page.url
    if "sign_in" in current_url or "login" in current_url.lower():
        return False

    selector, _ = await find_first_visible(page, LOGGED_IN_SELECTORS, timeout=2000)
    if selector is not None:
        return True

    return "404" not in current_url and current_url not in {BASE_URL, f"{BASE_URL}/"}


async def login(page: Page, account: Account, logger: logging.Logger, account_key: str) -> None:
    logger.info("[%s] Starting login with the email magic link flow", account_key)
    await _open_sign_in_page(page, account_key, logger)
    await human_delay(3, 5)

    email_selector = await fill_first_visible(
        page,
        LOGIN_EMAIL_SELECTORS,
        account.email,
        timeout=60000,
        logger=logger,
        log_context=f"[{account_key}] sign-in email field",
    )
    if email_selector is None:
        raise RuntimeError("Email input was not found on the sign-in page.")

    await human_delay(0.8, 1.5)

    submit_selector = await click_first_visible(
        page,
        LOGIN_SUBMIT_SELECTORS,
        timeout=8000,
        delay_after=(3, 5),
        logger=logger,
        log_context=f"[{account_key}] sign-in submit button",
    )
    if submit_selector is None:
        raise RuntimeError("Sign-in submit button was not found.")

    logger.info("[%s] Sign-in email submitted. Waiting for the login email.", account_key)
    magic_link = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: fetch_magic_link(account.email, account.app_pass, logger, account_key, timeout_sec=120),
    )
    if not magic_link:
        screenshot_path = await capture_debug_screenshot(page, "login_failed", account_key, logger)
        raise RuntimeError(
            f"Magic link not received within timeout. Screenshot saved to {screenshot_path}."
        )

    logger.info("[%s] Opening magic link", account_key)
    response = await page.goto(magic_link, wait_until="domcontentloaded", timeout=60000)
    await human_delay(3, 5)

    if response and response.status == 404:
        logger.warning(
            "[%s] Magic link landing page returned 404. This can be normal after token redemption. "
            "Retrying on /home after the session settles.",
            account_key,
        )
        await asyncio.sleep(3)
        await page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=60000)
        await human_delay(3, 5)
    else:
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await human_delay(2, 3)

    confirm_selector = await click_first_visible(
        page,
        LOGIN_CONFIRM_SELECTORS,
        timeout=3000,
        delay_after=(3, 5),
        logger=logger,
        log_context=f"[{account_key}] login confirmation button",
    )
    if confirm_selector is not None:
        logger.info("[%s] Clicked login confirmation button: %s", account_key, confirm_selector)

    current_url = page.url
    if "sign_in" in current_url or "magic" in current_url.lower():
        logger.warning(
            "[%s] Browser is still on a login-related page. Navigating to /home manually.",
            account_key,
        )
        await page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=60000)
        await human_delay(3, 5)

    if await is_logged_in(page):
        logger.info("[%s] Login completed successfully", account_key)
        return

    screenshot_path = await capture_debug_screenshot(page, "login_result", account_key, logger)
    raise RuntimeError(
        f"Login did not reach an authenticated page. Screenshot saved to {screenshot_path}."
    )


def _extract_email_body(message: Message) -> str:
    body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            charset = part.get_content_charset() or "utf-8"
            body += part.get_payload(decode=True).decode(charset, errors="replace")
        return body

    charset = message.get_content_charset() or "utf-8"
    payload = message.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(charset, errors="replace")


async def _open_sign_in_page(
    page: Page,
    account_key: str,
    logger: logging.Logger,
) -> None:
    target_url = f"{BASE_URL}/home/sign_in"
    attempts = (
        ("domcontentloaded", 120000),
        ("commit", 90000),
    )
    last_error: Exception | None = None

    for attempt_number, (wait_until, timeout_ms) in enumerate(attempts, start=1):
        try:
            logger.info(
                "[%s] Loading sign-in page (attempt %d, wait_until=%s, timeout=%ds)",
                account_key,
                attempt_number,
                wait_until,
                timeout_ms // 1000,
            )
            await page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)
            if wait_until == "commit":
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception:
                    logger.warning(
                        "[%s] Sign-in page committed but did not reach DOMContentLoaded within 30 seconds. "
                        "Continuing with selector-based login checks.",
                        account_key,
                    )
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[%s] Sign-in page load attempt %d failed: %s",
                account_key,
                attempt_number,
                exc,
            )
            await human_delay(2, 4)

    screenshot_path = await capture_debug_screenshot(page, "sign_in_timeout", account_key, logger)
    raise RuntimeError(
        "Failed to open the Arc sign-in page after multiple attempts. "
        f"Last error: {last_error}. Screenshot saved to {screenshot_path}."
    )
