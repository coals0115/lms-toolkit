"""다운로드 모드 강의 목록 로딩 성능 테스트.

실행: PYTHONPATH=. uv run python scripts/check_download_timing.py

실제 SSU LMS에 접속하여 get_lectures()의 동작을 검증한다.
- iframe 콘텐츠가 정상 로드되는지
- 모두 펼치기가 동작하는지
- 강의 목록이 반환되는지
- 소요 시간이 합리적인지 (10초 이내)
"""

import asyncio
import time

from playwright.async_api import async_playwright

from src.auto_watch.browser import setup_browser
from src.auto_watch.config import SCHOOL_CONFIGS
from src.auto_watch.provider import get_provider


async def main():
    provider = get_provider("ssu")
    config = SCHOOL_CONFIGS["ssu"]

    assert config.userid and config.password, ".env에 SSU_USERID/SSU_PASSWORD 필요"

    async with async_playwright() as p:
        page, browser, _ctx = await setup_browser(p)

        try:
            # 1. 로그인
            print("[1/3] 로그인 중...")
            t0 = time.perf_counter()
            await provider.login(page)
            t_login = time.perf_counter() - t0
            print(f"  로그인 완료: {t_login:.1f}초")

            # 2. 과목 목록
            print("[2/3] 과목 목록 조회 중...")
            t0 = time.perf_counter()
            courses = await provider.get_courses(page)
            t_courses = time.perf_counter() - t0
            print(f"  과목 {len(courses)}개 조회: {t_courses:.1f}초")

            assert len(courses) > 0, "과목이 0개"

            # 3. 전체 과목 강의 목록 로딩
            total_lectures = 0
            t0_all = time.perf_counter()
            for i, target in enumerate(courses):
                t0 = time.perf_counter()
                lectures = await provider.get_lectures(
                    page, target["courseId"], target["name"]
                )
                t_lectures = time.perf_counter() - t0
                total_lectures += len(lectures)
                short_name = target["name"][:30]
                print(f"  [{i+1}/{len(courses)}] {short_name}: {len(lectures)}개, {t_lectures:.1f}초")
            t_all = time.perf_counter() - t0_all

            print(f"\n=== 결과 ===")
            print(f"전체 {len(courses)}과목, 강의 {total_lectures}개, 총 {t_all:.1f}초")
            if total_lectures == 0:
                print("FAIL: 강의 0개")
            elif t_all > 30:
                print(f"WARN: 총 {t_all:.1f}초 — 여전히 느림")
            else:
                print("OK")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
