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
from .cli import select_courses, select_lectures, select_mode, select_school
from .config import HEADLESS, SCHOOL_CONFIGS, update_credentials
from .exceptions import LMSError, LoginError
from .log import setup_logging
from .plugin import discover_plugins
from .provider import LMSProvider, get_provider
from .types import Course

logger = logging.getLogger(__name__)


async def _run_watch_mode(page: Page, courses: list[Course], provider: LMSProvider) -> str | None:
    """자동 수강 모드: 미수강 동영상만 재생 + 다운로드/전사"""
    target_courses = [c for c in courses if c["videoCount"] > 0]

    if not target_courses:
        logger.info("미수강 동영상이 없습니다!")
        return

    all_lectures = []
    for course in target_courses:
        lectures = await provider.get_lectures(page, course["courseId"], course["name"])
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
        result = await provider.process_lecture(page, lecture, defer_transcript=True)
        if result.get("download_only"):
            download_only += 1
        elif result["attended"]:
            watch_completed += 1
        else:
            watch_failed += 1
        await asyncio.sleep(3)

    # 백그라운드 다운로드/전사 완료 대기
    transcript_results = await provider.drain_tasks()
    transcribed = sum(1 for r in transcript_results if r.get("txt"))

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


async def _run_download_mode(
    page: Page, courses: list[Course], provider: LMSProvider
) -> str | None:
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
            lectures = await provider.get_lectures(page, course["courseId"], course["name"])
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

        for i, lecture in enumerate(selected, 1):
            print(f"\n[{i}/{len(selected)}]", end=" ")
            lecture["isCompleted"] = True
            await provider.process_lecture(page, lecture, defer_transcript=True)
            await asyncio.sleep(3)

        # 백그라운드 다운로드/전사 완료 대기
        print()
        logger.info("다운로드/전사 완료 대기 중...")
        transcript_results = await provider.drain_tasks()
        transcribed = sum(1 for r in transcript_results if r.get("txt"))

        print(f"\n{'═' * 40}")
        print("  완료!")
        print(f"  다운로드: {len(selected)}개")
        if transcribed:
            print(f"  스크립트: {transcribed}개 추출 → output/")
        print(f"{'═' * 40}")
        break


def _parse_args(plugins=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LMS 자동 수강 시스템")
    parser.add_argument(
        "--headless",
        action="store_true",
        default=HEADLESS,
        help="브라우저를 headless 모드로 실행합니다. (env: LMS_HEADLESS=1)",
    )
    if plugins:
        for plugin in plugins:
            plugin.add_arguments(parser)
    return parser.parse_args()


def cli_entry() -> None:
    """project.scripts 엔트리포인트"""
    setup_logging()
    asyncio.run(main())


async def main() -> None:
    setup_logging()
    plugins = discover_plugins()
    args = _parse_args(plugins)

    # 학교 선택 + Provider 생성
    school = select_school()
    provider = get_provider(school)
    school_config = SCHOOL_CONFIGS[school]

    if not school_config.userid or not school_config.password:
        env_prefix = school.upper()
        logger.error(".env에 %s_USERID와 %s_PASSWORD를 설정하세요", env_prefix, env_prefix)
        sys.exit(1)

    # 플러그인 CLI 플래그 처리 (예: --sync)
    active_plugin = next((p for p in plugins if p.should_handle(args)), None)
    if active_plugin:
        async with async_playwright() as p:
            try:
                page, browser, _context = await setup_browser(p, headless=args.headless)
            except Exception:
                logger.warning("브라우저 실행 실패(headless=%s), headed로 재시도", args.headless)
                page, browser, _context = await setup_browser(p, headless=False)

            try:
                await provider.login(page)
                courses = await provider.get_courses(page)
                if not courses:
                    return
                await active_plugin.run(page, courses)
            except LMSError as e:
                logger.error("%s", e)
                sys.exit(1)
            finally:
                await browser.close()
        return

    # 대화형 모드
    print("=" * 60)
    print(f"  {provider.display_name} LMS 자동 수강 시스템")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  종료: q 또는 Ctrl+C")
    print("=" * 60)

    async with async_playwright() as p:
        page, browser, _context = await setup_browser(p, headless=args.headless)

        try:
            # 로그인 재시도 루프 (최대 3회)
            courses = None
            for attempt in range(3):
                try:
                    await provider.login(page)
                    courses = await provider.get_courses(page)
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
                    update_credentials(school, userid, pwd)
                    await page.goto("about:blank")
                    logger.info("다시 로그인 시도 중...")

            if not courses:
                return

            plugin_map = {pl.name: pl for pl in plugins}

            while True:
                mode = select_mode(plugins)

                if mode == "quit":
                    break
                elif mode == "watch":
                    result = await _run_watch_mode(page, courses, provider)
                elif mode == "download":
                    result = await _run_download_mode(page, courses, provider)
                elif mode in plugin_map:
                    result = await plugin_map[mode].run(page, courses)
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
