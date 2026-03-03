"""Playwright 브라우저 설정 및 LMS 인증"""

import asyncio
import sys

from playwright.async_api import Page, Frame

from src.auto_watch.config import USERID, PASSWORD


async def setup_browser(playwright):
    """Playwright headed 브라우저 설정 (봇 탐지 우회 포함)"""
    browser = await playwright.chromium.launch(
        headless=False,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--enable-proprietary-codecs",
            "--use-fake-ui-for-media-stream",
        ],
    )

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        permissions=["camera", "microphone"],
    )

    page = await context.new_page()
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return page, browser, context


async def login_if_needed(page: Page):
    """현재 페이지가 로그인 페이지면 SSO 로그인 수행"""
    if "login" not in page.url and "smartid" not in page.url:
        return

    print(f"[INFO] 로그인 필요 — {page.url}")

    # Canvas 로그인 페이지 → SSO 버튼 클릭
    login_btn = await page.query_selector(".login_btn a")
    if login_btn:
        await login_btn.click()
        await page.wait_for_load_state("networkidle")

    # SSO 로그인 폼 입력 (type으로 키보드 시뮬레이션)
    await page.wait_for_selector("input#userid", timeout=10000)

    # 기존 값 클리어 후 타이핑
    await page.click("input#userid")
    await page.fill("input#userid", "")
    await page.type("input#userid", USERID, delay=50)

    await page.click("input#pwd")
    await page.fill("input#pwd", "")
    await page.type("input#pwd", PASSWORD, delay=50)

    await asyncio.sleep(1)

    # JS로 직접 폼 제출
    await page.evaluate("""
        () => {
            // 로그인 버튼 찾기
            const btn = document.querySelector('a.btn_login');
            if (btn) { btn.click(); return 'btn_click'; }
            // 폼 직접 submit
            const form = document.querySelector('form');
            if (form) { form.submit(); return 'form_submit'; }
            return 'nothing_found';
        }
    """)
    # Canvas 리디렉션 대기
    for i in range(20):
        await asyncio.sleep(2)
        current_url = page.url
        if i % 5 == 0:
            print(f"[INFO] 리디렉션 대기... ({current_url[:60]})")

        if "canvas.ssu.ac.kr" in current_url and "login" not in current_url:
            break
        if "lms.ssu.ac.kr" in current_url and "login" not in current_url:
            break

        # SSO 페이지에 에러 메시지 있는지 확인
        if "smartid" in current_url:
            error_msg = await page.evaluate("""
                () => {
                    const alerts = document.querySelectorAll('.alert, .error, .err_msg, [class*="error"]');
                    return Array.from(alerts).map(el => el.innerText?.trim()).filter(t => t).join(' | ') || null;
                }
            """)
            if error_msg:
                print(f"[ERROR] SSO 에러: {error_msg}")
                break

    if "login" in page.url or "smartid" in page.url:
        print("[ERROR] 로그인 실패 — .env의 USERID/PASSWORD 확인 필요")
        sys.exit(1)

    print(f"[INFO] 로그인 성공 — {page.url}")


async def get_tool_content_frame(page: Page, timeout: int = 15000) -> Frame:
    """tool_content iframe의 Frame 객체를 반환"""
    await page.wait_for_selector("#tool_content", timeout=timeout)
    frame = page.frame("tool_content")
    if not frame:
        raise Exception("tool_content frame not found")
    await frame.wait_for_load_state("domcontentloaded")
    # iframe 내부 콘텐츠 로드 대기
    await asyncio.sleep(2)
    return frame
