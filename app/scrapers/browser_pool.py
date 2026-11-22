from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional


class BrowserPool:
    """Administrador perezoso de Chromium (Playwright) para tiendas que requieren renderizado JS."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._semaphore = asyncio.Semaphore(3)
        self._available: Optional[bool] = None

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                self._available = False
                return None

            try:
                self._playwright = await async_playwright().start()
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
                last_err = None
                for channel in (None, "chrome", "msedge"):
                    try:
                        kwargs = {"headless": True, "args": launch_args}
                        if channel:
                            kwargs["channel"] = channel
                        self._browser = await self._playwright.chromium.launch(**kwargs)
                        self._available = True
                        return self._browser
                    except Exception as err:
                        last_err = err
                raise last_err or RuntimeError("No se pudo iniciar Chromium/Chrome/Edge.")
            except Exception:
                self._available = False
                return None

    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[object]:
        browser = await self._ensure_browser()
        if browser is None:
            yield None
            return

        async with self._semaphore:
            context = await browser.new_context(
                locale="es-CO",
                timezone_id="America/Bogota",
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
                },
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = await context.new_page()
            # Block heavy media/fonts to speed up local scraping significantly
            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("media", "font")
                else route.continue_(),
            )
            try:
                yield page
            finally:
                await context.close()

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._browser:
                    await self._browser.close()
                if self._playwright:
                    await self._playwright.stop()
            except Exception:
                pass
            self._browser = None
            self._playwright = None


browser_pool = BrowserPool()
