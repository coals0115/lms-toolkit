"""메인 오케스트레이터"""

import argparse
import asyncio
import contextlib
import getpass
import logging
import sys
from datetime import datetime

from playwright.async_api import Page, async_playwright

from .browser import setup_browser
from .cli import select_courses, select_lectures, select_mode
from .config import PASSWORD, USERID, update_credentials
from .courses import get_courses, get_items_by_week, get_lectures
from .exceptions import LMSError, LoginError
from .log import setup_logging
from .player import process_lecture
from .types import Course

logger = logging.getLogger(__name__)


async def _run_watch_mode(page: Page, courses: list[Course]) -> str | None:
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


async def _run_download_mode(page: Page, courses: list[Course]) -> str | None:
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


async def _run_sync_mode(page: Page, courses: list[Course]) -> None:
    """동기화 모드: LMS 현황을 수집하여 출력 (Phase 1 — 데이터 수집만)"""
    logger.info("[sync] %d개 과목 현황 수집 시작", len(courses))

    for course in courses:
        status = await get_items_by_week(page, course["courseId"], course["name"])
        weeks = status["weeks"]

        print(f"\n{'─' * 50}")
        print(f"  {status['courseName']} (course {status['courseId']})")
        print(f"{'─' * 50}")

        for wn in sorted(weeks.keys()):
            items = weeks[wn]
            week_label = f"{wn}주차" if wn > 0 else "기타"
            completed = sum(1 for it in items if it["isCompleted"])
            print(f"\n  {week_label} ({completed}/{len(items)} 완료)")

            for item in items:
                m, s = divmod(item["durationSec"], 60)
                check = "✅" if item["isCompleted"] else "  "
                duration = f" ({m}:{s:02d})" if item["durationSec"] > 0 else ""
                deadline_str = ""
                if item["deadline"]:
                    dl = datetime.fromisoformat(
                        item["deadline"].replace("Z", "+00:00")
                    ).strftime("%m/%d")
                    deadline_str = f" 마감 {dl}"
                print(
                    f"    {check} [{item['itemType']:10s}] "
                    f"{item['title']}{duration}{deadline_str}"
                )

    print(f"\n{'═' * 50}")
    print("  [sync] 데이터 수집 완료 (vault 동기화는 Phase 2에서 구현)")
    print(f"{'═' * 50}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="숭실대 LMS 자동 수강 시스템")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="LMS 현황 → Obsidian vault 동기화 (비대화형, headless)",
    )
    parser.add_argument(
        "--init-mapping",
        action="store_true",
        help="과목 매핑 초기화 (LMS ↔ vault 폴더)",
    )
    return parser.parse_args()


def cli_entry() -> None:
    """project.scripts 엔트리포인트"""
    setup_logging()
    asyncio.run(main())


async def main() -> None:
    setup_logging()
    args = _parse_args()

    if not USERID or not PASSWORD:
        logger.error(".env에 USERID와 PASSWORD를 설정하세요")
        sys.exit(1)

    # --sync: 비대화형 headless 동기화
    if args.sync:
        print("=" * 60)
        print("  숭실대 LMS → Obsidian 동기화")
        print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        async with async_playwright() as p:
            # headless 시도, 실패 시 headed fallback
            try:
                page, browser, _context = await setup_browser(p, headless=True)
            except Exception:
                logger.warning("[sync] headless 실패, headed로 재시도")
                page, browser, _context = await setup_browser(p, headless=False)

            try:
                courses = await get_courses(page)
                if not courses:
                    return
                await _run_sync_mode(page, courses)
            except LMSError as e:
                logger.error("%s", e)
                sys.exit(1)
            finally:
                await browser.close()
        return

    # --init-mapping: 과목 매핑 초기화 (Phase 2에서 구현)
    if args.init_mapping:
        print("[TODO] 과목 매핑 초기화는 Phase 2에서 구현")
        return

    # 대화형 모드
    print("=" * 60)
    print("  숭실대 LMS 자동 수강 시스템")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  종료: q 또는 Ctrl+C")
    print("=" * 60)

    async with async_playwright() as p:
        page, browser, _context = await setup_browser(p)

        try:
            # 로그인 재시도 루프 (최대 3회)
            courses = None
            for attempt in range(3):
                try:
                    courses = await get_courses(page)
                    break
                except LoginError:
                    if attempt == 2:
                        logger.error("로그인 3회 실패. 종료합니다.")
                        sys.exit(1)
                    retry = input("\n학번/비밀번호를 다시 입력하시겠습니까? (Y/n): ").strip()
                    if retry.lower() == "n":
                        sys.exit(1)
                    userid = input("학번 (q=종료): ").strip()
                    if not userid or userid.lower() == "q":
                        sys.exit(0)
                    pwd = getpass.getpass("비밀번호 (빈 입력=종료): ")
                    if not pwd:
                        sys.exit(0)
                    update_credentials(userid, pwd)
                    await page.goto("about:blank")
                    logger.info("다시 로그인 시도 중...")

            if not courses:
                return

            while True:
                mode = select_mode()

                if mode == "quit":
                    break
                elif mode == "watch":
                    result = await _run_watch_mode(page, courses)
                elif mode == "download":
                    result = await _run_download_mode(page, courses)
                elif mode == "sync":
                    await _run_sync_mode(page, courses)
                    result = None
                else:
                    result = None

                if result != "back":
                    break

        except LMSError as e:
            logger.error("%s", e)
            sys.exit(1)
        finally:
            with contextlib.suppress(EOFError):
                input("\n 엔터를 누르면 브라우저를 닫습니다...")
            await browser.close()
