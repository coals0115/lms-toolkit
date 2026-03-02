import asyncio
from playwright.async_api import async_playwright, Playwright, Page, BrowserContext
from typing import Optional, Tuple

from src.video_pipeline.login import perform_login_if_needed
from src.video_pipeline.video_parser import extract_video_url
from src.video_pipeline.download_video import download_video
from src.user_setting import UserSetting


class VideoPipeline:
    def __init__(self, user_setting: UserSetting):
        self.user_setting = user_setting
        self.user_id = user_setting.user_id
        self.password = user_setting.password
        self.downloads_dir = None  # 다운로드 경로는 나중에 설정됨

    async def _setup_browser(self, playwright: Playwright) -> Tuple[Page, any, BrowserContext]:
        """브라우저 설정 및 페이지 생성"""
        browser = await playwright.chromium.launch(
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--enable-proprietary-codecs",
                "--disable-web-security",
                "--auto-open-devtools-for-tabs",
                "--use-fake-ui-for-media-stream",
            ],
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            permissions=["camera", "microphone", "geolocation"],
        )

        page = await context.new_page()
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            window.chrome = { runtime: {} };
            """
        )

        return page, browser, context

    async def _process_single_url(self, page: Page, url: str) -> Optional[str]:
        """단일 URL에 대한 비디오 처리"""
        print(f"\n[INFO] 처리 중: {url}")
        await page.goto(url, wait_until="networkidle")
        print(f"[DEBUG] 페이지 이동 완료: {page.url}")

        # 혹시 세션이 만료되었을 경우 재로그인
        if await perform_login_if_needed(page, self.user_id, self.password):
            print("[INFO] 로그인 완료 또는 유지됨.")
            await page.wait_for_load_state("networkidle")
            print(f"[DEBUG] 로그인 후 현재 URL: {page.url}")

        video_url, title = await extract_video_url(page)

        if video_url:
            print(f"[SUCCESS] 동영상 링크 추출됨: {video_url}, 제목: {title}")
            filepath = download_video(video_url, filename=title)
            print(f"[SUCCESS] 동영상 다운로드 완료: {filepath}")
            return filepath
        else:
            print("[WARN] 동영상 링크를 찾지 못했습니다.")
            return None

    async def process(self, urls: list[str]) -> list[str]:
        """비디오 다운로드 파이프라인 실행 (순차 처리)"""
        if not urls:
            print("[WARN] 처리할 URL이 없습니다.")
            return []

        print(f"[INFO] {len(urls)}개의 동영상을 순차적으로 다운로드합니다.")

        downloaded_videos_path = []

        async with async_playwright() as p:
            page, browser, context = await self._setup_browser(p)
            try:
                # 첫 번째 URL로 로그인
                print("[INFO] 로그인 세션 초기화 중...")
                await page.goto(urls[0], wait_until="networkidle")
                if await perform_login_if_needed(page, self.user_id, self.password):
                    print("[INFO] 로그인 완료.")
                    await page.wait_for_load_state("networkidle")

                # 모든 URL 순차 처리
                for url in urls:
                    try:
                        filepath = await self._process_single_url(page, url)
                        if filepath:
                            downloaded_videos_path.append(filepath)
                    except Exception as e:
                        print(f"[ERROR] URL 처리 중 오류 발생 ({url}): {e}")

            finally:
                await browser.close()
                print("[DEBUG] 브라우저 종료 완료")

        print(f"[INFO] 총 {len(downloaded_videos_path)}/{len(urls)}개의 동영상 다운로드 완료")
        return downloaded_videos_path

    def process_sync(self, urls: list[str]) -> list[str]:
        """동기 방식으로 파이프라인 실행"""
        return asyncio.run(self.process(urls))
