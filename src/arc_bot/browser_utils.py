from __future__ import annotations

import asyncio
import logging
import random
import re
import select
import socket
import threading
import time
from typing import Callable, Sequence

from playwright.async_api import Locator, Page

from .config import log_artifact_path
from .logging_utils import safe_exception_message

_tunnel_servers: dict[str, tuple[int, threading.Event, threading.Thread]] = {}


async def human_delay(min_s: float = 1.5, max_s: float = 4.0) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def scroll_slowly(page: Page, steps: int = 5) -> None:
    for _ in range(steps):
        await page.mouse.wheel(0, random.randint(200, 500))
        await asyncio.sleep(random.uniform(0.4, 0.9))


async def click_first_visible(
    page: Page,
    selectors: Sequence[str],
    *,
    timeout: int = 3000,
    use_last: bool = False,
    scroll: bool = True,
    delay_after: tuple[float, float] | None = None,
    logger: logging.Logger | None = None,
    log_context: str | None = None,
) -> str | None:
    for selector in selectors:
        locator = _pick_locator(page.locator(selector), use_last=use_last)
        try:
            if not await locator.is_visible(timeout=timeout):
                continue
            if scroll:
                try:
                    await locator.scroll_into_view_if_needed(timeout=5000)
                except Exception:
                    pass
            await locator.click(timeout=timeout)
            if delay_after is not None:
                await human_delay(*delay_after)
            return selector
        except Exception as exc:
            _log_selector_debug(logger, log_context, selector, exc)
    return None


async def fill_first_visible(
    page: Page,
    selectors: Sequence[str],
    value: str,
    *,
    timeout: int = 3000,
    use_last: bool = False,
    click_first: bool = True,
    logger: logging.Logger | None = None,
    log_context: str | None = None,
) -> str | None:
    for selector in selectors:
        locator = _pick_locator(page.locator(selector), use_last=use_last)
        try:
            if not await locator.is_visible(timeout=timeout):
                continue
            if click_first:
                try:
                    await locator.click(timeout=timeout)
                except Exception:
                    pass
            await locator.fill(value, timeout=timeout)
            return selector
        except Exception as exc:
            _log_selector_debug(logger, log_context, selector, exc)
    return None


async def text_from_first_visible(
    page: Page,
    selectors: Sequence[str],
    *,
    timeout: int = 3000,
    use_last: bool = False,
    logger: logging.Logger | None = None,
    log_context: str | None = None,
) -> str | None:
    for selector in selectors:
        locator = _pick_locator(page.locator(selector), use_last=use_last)
        try:
            if not await locator.is_visible(timeout=timeout):
                continue
            text = (await locator.text_content()) or ""
            text = text.strip()
            if text:
                return text
        except Exception as exc:
            _log_selector_debug(logger, log_context, selector, exc)
    return None


async def find_first_visible(
    page: Page,
    selectors: Sequence[str],
    *,
    timeout: int = 3000,
    use_last: bool = False,
    logger: logging.Logger | None = None,
    log_context: str | None = None,
) -> tuple[str, Locator] | tuple[None, None]:
    for selector in selectors:
        locator = _pick_locator(page.locator(selector), use_last=use_last)
        try:
            if await locator.is_visible(timeout=timeout):
                return selector, locator
        except Exception as exc:
            _log_selector_debug(logger, log_context, selector, exc)
    return None, None


async def goto_with_fallback_paths(
    page: Page,
    base_url: str,
    paths: Sequence[str],
    *,
    timeout: int = 30000,
    logger: logging.Logger | None = None,
    log_context: str | None = None,
) -> tuple[str | None, object | None]:
    for path in paths:
        target_url = path if path.startswith("http") else f"{base_url}{path}"
        try:
            response = await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=timeout,
            )
            if response and response.status == 404:
                continue
            return path, response
        except Exception as exc:
            _log_selector_debug(logger, log_context, path, exc)
    return None, None


async def collect_unique_hrefs(
    page: Page,
    selector: str,
    *,
    include: Callable[[str], bool] | None = None,
) -> list[str]:
    links = await page.locator(selector).all()
    hrefs: list[str] = []
    seen: set[str] = set()
    for link in links:
        href = await link.get_attribute("href")
        if not href or href in seen:
            continue
        if include is not None and not include(href):
            continue
        seen.add(href)
        hrefs.append(href)
    return hrefs


async def capture_debug_screenshot(
    page: Page,
    prefix: str,
    account_key: str,
    logger: logging.Logger | None = None,
) -> str:
    path = log_artifact_path(prefix, account_key)
    try:
        await page.screenshot(path=str(path))
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "[%s] Failed to save screenshot %s: %s",
                account_key,
                path.name,
                safe_exception_message(exc),
            )
    return str(path)


def parse_proxy(
    proxy_url: str,
    logger: logging.Logger | None = None,
) -> dict[str, str]:
    match = re.match(r"^((?:http|https|socks5)://)(?:([^:@]+):([^@]+)@)?(.+)$", proxy_url)
    if not match:
        return {"server": proxy_url}

    scheme, username, password, hostport = match.groups()
    if scheme == "socks5://" and username:
        local_http_proxy = start_socks5_tunnel(proxy_url, logger)
        return {"server": local_http_proxy}

    proxy_config: dict[str, str] = {"server": f"{scheme}{hostport}"}
    if username:
        proxy_config["username"] = username
    if password:
        proxy_config["password"] = password
    return proxy_config


def start_socks5_tunnel(
    proxy_url: str,
    logger: logging.Logger | None = None,
) -> str:
    if proxy_url in _tunnel_servers:
        port, stop_event, thread = _tunnel_servers[proxy_url]
        if thread.is_alive():
            return f"http://127.0.0.1:{port}"
        del _tunnel_servers[proxy_url]

    port = _free_port()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_http_proxy,
        args=(port, proxy_url, stop_event, logger),
        daemon=True,
    )
    thread.start()
    time.sleep(0.3)

    if logger is not None:
        logger.info(
            "Started local HTTP bridge for SOCKS5 proxy on http://127.0.0.1:%d",
            port,
        )

    _tunnel_servers[proxy_url] = (port, stop_event, thread)
    return f"http://127.0.0.1:{port}"


def stop_all_tunnels() -> None:
    for _, (_, stop_event, _) in list(_tunnel_servers.items()):
        stop_event.set()
    _tunnel_servers.clear()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _parse_socks5_url(proxy_url: str) -> tuple[str | None, str | None, str, int]:
    match = re.match(r"socks5://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)", proxy_url)
    if not match:
        raise ValueError("Could not parse the SOCKS5 proxy URL.")
    username, password, host, port = match.groups()
    return username, password, host, int(port)


def _run_http_proxy(
    local_port: int,
    socks5_url: str,
    stop_event: threading.Event,
    logger: logging.Logger | None = None,
) -> None:
    try:
        from python_socks.sync import Socks5Proxy
    except ImportError:
        from python_socks import Socks5Proxy

    username, password, socks_host, socks_port = _parse_socks5_url(socks5_url)

    def handle_client(conn: socket.socket) -> None:
        try:
            request = conn.recv(4096)
            if not request:
                return

            first_line = request.split(b"\r\n")[0].decode()
            parts = first_line.split()
            if len(parts) < 2:
                return

            method, target = parts[0], parts[1]
            if method == "CONNECT":
                host, port_text = target.rsplit(":", 1)
                port = int(port_text)
            else:
                from urllib.parse import urlparse

                parsed = urlparse(target)
                host = parsed.hostname or ""
                port = parsed.port or 80

            proxy = Socks5Proxy(
                proxy_host=socks_host,
                proxy_port=socks_port,
                username=username,
                password=password,
                rdns=True,
            )
            remote = proxy.connect(dest_host=host, dest_port=port)

            if method == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                remote.sendall(request)

            def forward(source: socket.socket, destination: socket.socket) -> None:
                try:
                    while True:
                        readable, _, _ = select.select([source], [], [], 5)
                        if not readable:
                            break
                        chunk = source.recv(8192)
                        if not chunk:
                            break
                        destination.sendall(chunk)
                except Exception:
                    # Connection shutdowns are expected during browser navigation.
                    pass
                finally:
                    try:
                        source.close()
                    except Exception:
                        pass
                    try:
                        destination.close()
                    except Exception:
                        pass

            threading.Thread(target=forward, args=(remote, conn), daemon=True).start()
            forward(conn, remote)
        except Exception as exc:
            if logger is not None:
                logger.debug("SOCKS5 bridge client connection failed: %s", safe_exception_message(exc))
            try:
                conn.close()
            except Exception:
                pass

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", local_port))
    server.listen(32)
    server.settimeout(1)

    try:
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
                threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
    finally:
        server.close()


def _pick_locator(locator: Locator, *, use_last: bool) -> Locator:
    return locator.last if use_last else locator.first


def _log_selector_debug(
    logger: logging.Logger | None,
    log_context: str | None,
    selector: str,
    exc: Exception,
) -> None:
    if logger is None or log_context is None:
        return
    logger.debug("%s: selector %r failed: %s", log_context, selector, exc)
