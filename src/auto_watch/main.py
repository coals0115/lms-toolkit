"""메인 오케스트레이터"""

import asyncio
import contextlib
import logging
import sys
from datetime import datetime

from playwright.async_api import async_playwright

from .browser import setup_browser
from .cli import select_courses, select_lectures, select_mode
from .config import PASSWORD, USERID
from .courses import get_courses, get_lectures
from .exceptions import LMSError
from .log import setup_logging
from .player import process_lecture

logger = logging.getLogger(__name__)


async def _run_watch_mode(page, courses):
    """자동 수강 모드: 미수강 동영상만 재생 + 다운로드/전사"""
    target_courses = [c for c in courses if c["videoCount"] > 0]

    if not target_courses:
        logger.info("미수강 동영상이 없습니다!")
        return

    all_lectures = []
    for course in target_courses:
        lectures = await get_lectures(page, course["courseId"], course["name"])
        all_lectures.extend(lectures)

    if not all_lectures:
        logger.info("강의 없음!")
        return

    selected = select_lectures(all_lectures)
    if selected == "back":
        return "back"
    if not selected:
        logger.info("선택 없음. 종료.")
        return

    sel_total = sum(lec["durationSec"] for lec in selected)
    sel_m, sel_s = divmod(sel_total, 60)
    logger.info("%d개 선택, 총 %d:%02d", len(selected), sel_m, sel_s)

    watch_completed = 0
    download_only = 0
    transcribed = 0
    watch_failed = 0

    for i, lecture in enumerate(selected, 1):
        print(f"\n[{i}/{len(selected)}]", end=" ")
        result = await process_lecture(page, lecture)
        if result.get("download_only"):
            download_only += 1
        elif result["attended"]:
            watch_completed += 1
        else:
            watch_failed += 1
        if result.get("txt"):
            transcribed += 1
        await asyncio.sleep(3)

    print(f"\n{'═' * 40}")
    print("  완료!")
    if watch_completed:
        print(f"  수강 처리: {watch_completed}개")
    if download_only:
        print(f"  다운로드: {download_only}개 (수강완료 강의)")
    if transcribed:
        print(f"  스크립트: {transcribed}개 추출 → output/")
    if watch_failed:
        print(f"  수강 실패: {watch_failed}개")
    print(f"{'═' * 40}")


async def _run_download_mode(page, courses):
    """다운로드 모드: 과목 선택 → 강의 선택 → 다운로드/전사만"""
    while True:
        selected_courses = select_courses(courses)
        if selected_courses == "back":
            return "back"
        if not selected_courses:
            logger.info("선택 없음. 종료.")
            return

        all_lectures = []
        for course in selected_courses:
            lectures = await get_lectures(page, course["courseId"], course["name"])
            all_lectures.extend(lectures)

        if not all_lectures:
            logger.info("강의 없음!")
            continue

        selected = select_lectures(all_lectures, download_mode=True)
        if selected == "back":
            continue
        if not selected:
            logger.info("선택 없음. 종료.")
            return

        sel_total = sum(lec["durationSec"] for lec in selected)
        sel_m, sel_s = divmod(sel_total, 60)
        logger.info("%d개 선택, 총 %d:%02d", len(selected), sel_m, sel_s)

        transcribed = 0

        for i, lecture in enumerate(selected, 1):
            print(f"\n[{i}/{len(selected)}]", end=" ")
            lecture["isCompleted"] = True
            result = await process_lecture(page, lecture)
            if result.get("txt"):
                transcribed += 1
            await asyncio.sleep(3)

        print(f"\n{'═' * 40}")
        print("  완료!")
        print(f"  다운로드: {len(selected)}개")
        if transcribed:
            print(f"  스크립트: {transcribed}개 추출 → output/")
        print(f"{'═' * 40}")
        break


def cli_entry():
    """project.scripts 엔트리포인트"""
    setup_logging()
    asyncio.run(main())


async def main():
    setup_logging()

    if not USERID or not PASSWORD:
        logger.error(".env에 USERID와 PASSWORD를 설정하세요")
        sys.exit(1)

    print("=" * 60)
    print("  숭실대 LMS 자동 수강 시스템")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    async with async_playwright() as p:
        page, browser, _context = await setup_browser(p)

        try:
            courses = await get_courses(page)
            if not courses:
                return

            while True:
                mode = select_mode()

                if mode == "watch":
                    result = await _run_watch_mode(page, courses)
                else:
                    result = await _run_download_mode(page, courses)

                if result != "back":
                    break

        except LMSError as e:
            logger.error("%s", e)
            sys.exit(1)
        finally:
            with contextlib.suppress(EOFError):
                input("\n 엔터를 누르면 브라우저를 닫습니다...")
            await browser.close()
