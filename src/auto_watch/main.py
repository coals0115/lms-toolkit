"""메인 오케스트레이터"""

import asyncio
import sys
from datetime import datetime

from playwright.async_api import async_playwright

from .config import USERID, PASSWORD
from .browser import setup_browser
from .courses import get_unwatched_courses, get_unwatched_lectures
from .player import watch_lecture
from .cli import select_lectures


async def main():
    if not USERID or not PASSWORD:
        print("[ERROR] .env에 USERID와 PASSWORD를 설정하세요")
        sys.exit(1)

    print("=" * 60)
    print("  숭실대 LMS 자동 수강 시스템")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  모드: 1x 배속 (출결 인정)")
    print("=" * 60)

    async with async_playwright() as p:
        page, browser, context = await setup_browser(p)

        try:
            courses = await get_unwatched_courses(page)
            if not courses:
                print("\n[INFO] 모든 강의 수강 완료! 끝.")
                return

            # 모든 과목의 미수강 강의를 수집
            all_lectures = []
            for course in courses:
                lectures = await get_unwatched_lectures(page, course["courseId"], course["name"])
                all_lectures.extend(lectures)

            if not all_lectures:
                print("\n[INFO] 미수강 강의 없음!")
                return

            # CLI 선택 UI
            selected = select_lectures(all_lectures)
            if not selected:
                print("\n[INFO] 선택 없음. 종료.")
                return

            sel_total = sum(l["durationSec"] for l in selected)
            sel_m, sel_s = divmod(sel_total, 60)
            print(f"\n[INFO] {len(selected)}개 선택, 총 {sel_m}:{sel_s:02d}")

            completed = 0
            failed = 0
            transcribed = 0

            for i, lecture in enumerate(selected, 1):
                print(f"\n[{i}/{len(selected)}]", end=" ")
                result = await watch_lecture(page, lecture)
                if result["attended"]:
                    completed += 1
                else:
                    failed += 1
                if result.get("txt"):
                    transcribed += 1
                await asyncio.sleep(3)

            # 결과 요약
            print(f"\n{'=' * 60}")
            print(f"  완료! {completed}개 수강, 총 {sel_m}:{sel_s:02d}")
            if transcribed:
                print(f"  스크립트: {transcribed}개 추출 → output/")
            if failed:
                print(f"  실패: {failed}개")
            print(f"  종료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'=' * 60}")

        finally:
            try:
                input("\n엔터를 누르면 브라우저를 닫습니다...")
            except EOFError:
                pass
            await browser.close()
