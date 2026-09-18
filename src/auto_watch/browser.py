"""Playwright 브라우저 설정"""

from playwright.async_api import Browser, BrowserContext, Page

from .config import CHROME_PATH, USER_AGENT


async def setup_browser(playwright, headless: bool = False) -> tuple[Page, Browser, BrowserContext]:
    """Playwright 브라우저 설정 (봇 탐지 우회 포함)"""
    browser = await playwright.chromium.launch(
        headless=headless,
        executable_path=CHROME_PATH,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--enable-proprietary-codecs",
            "--use-fake-ui-for-media-stream",
        ],
    )

    context = await browser.new_context(
        user_agent=USER_AGENT,
        permissions=["camera", "microphone"],
    )

    page = await context.new_page()
    page.set_default_navigation_timeout(90000)
    page.set_default_timeout(90000)
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return page, browser, context
