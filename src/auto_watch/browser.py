"""Playwright 브라우저 설정"""

import logging

from playwright.async_api import Browser, BrowserContext, Page

from .config import CHROME_PATH, USER_AGENT

logger = logging.getLogger(__name__)


async def setup_browser(playwright, headless: bool = False) -> tuple[Page, Browser, BrowserContext]:
    """Playwright 브라우저 설정 (봇 탐지 우회 포함)"""
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--enable-proprietary-codecs",
        "--use-fake-ui-for-media-stream",
    ]

    if CHROME_PATH:
        logger.debug("CHROME_PATH 사용: %s", CHROME_PATH)
        browser = await playwright.chromium.launch(
            headless=headless,
            executable_path=CHROME_PATH,
            args=launch_args,
        )
    else:
        logger.info("CHROME_PATH 미설정/미탐지: Playwright 기본 Chromium 사용")
        browser = await playwright.chromium.launch(
            headless=headless,
            args=launch_args,
        )

    context = await browser.new_context(
        user_agent=USER_AGENT,
        permissions=["camera", "microphone"],
    )

    page = await context.new_page()
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return page, browser, context
