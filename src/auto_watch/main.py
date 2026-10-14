"""메인 오케스트레이터"""

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import truststore
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import setup_browser
from .cli import select_courses, select_lectures, select_mode, select_school
from .config import SCHOOL_CONFIGS, update_credentials
from .exceptions import LMSError, LoginError
from .log import setup_logging
from .plugin import discover_plugins
from .provider import LMSProvider, get_provider
from .transcription import transcript_exists
from .types import Course, Lecture, ProcessResult, TranscriptResult
from .util import format_duration

logger = logging.getLogger(__name__)


async def _process_safely(
    provider: LMSProvider, page: Page, lecture: Lecture
) -> ProcessResult | None:
    try:
        return await provider.process_lecture(page, lecture, defer_transcript=True)
    except Exception:
        logger.exception("강의 처리 실패 — 다음 강의로 넘어감: %s", lecture.get("title"))
        return None


def _key(lecture: Mapping[str, Any]) -> tuple[str, str]:
    return (lecture.get("courseName", "unknown"), lecture["title"])


def _collect_failures(
    lectures: Sequence[Mapping[str, Any]],
    results: list[TranscriptResult],
    crashed: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    by_key = {(r["course"], r["title"]): r for r in results}
    failures = []
    for lec in lectures:
        key = _key(lec)
        r = by_key.get(key)
        if key in crashed:
            failures.append((lec["title"], "강의 처리 중 오류"))
        elif r is None:
            failures.append((lec["title"], "영상 URL 못 찾음"))
        elif not r["mp4"] and not r["txt"]:
            failures.append((lec["title"], "다운로드 실패"))
        elif not r["txt"]:
            failures.append((lec["title"], "전사 실패"))
    return failures


def _print_failures(label: str, failures: list[tuple[str, str]]) -> None:
    if not failures:
        return
    print(f"  {label}: {len(failures)}개 (로그 확인)")
    for title, reason in failures:
        print(f"    - {title}: {reason}")


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

    await _watch_selected(page, provider, selected)


async def _watch_selected(
    page: Page, provider: LMSProvider, selected: list[Lecture]
) -> dict[str, Any]:
    sel_total = sum(lec["durationSec"] for lec in selected)
    logger.info("%d개 선택, 총 %s", len(selected), format_duration(sel_total))

    started = time.monotonic()
    watched: list[Lecture] = []
    download_only = 0
    watch_failed: list[str] = []
    crashed: set[tuple[str, str]] = set()

    for i, lecture in enumerate(selected, 1):
        print(f"\n[{i}/{len(selected)}]", end=" ")
        result = await _process_safely(provider, page, lecture)
        if result is None:
            watch_failed.append(lecture["title"])
            crashed.add(_key(lecture))
        elif result.get("download_only"):
            download_only += 1
        elif result["attended"]:
            watched.append(lecture)
        else:
            watch_failed.append(lecture["title"])
        await asyncio.sleep(3)

    # 백그라운드 다운로드/전사 완료 대기
    print()
    logger.info("다운로드/전사 완료 대기 중...")
    transcript_results = await provider.drain_tasks()
    transcribed = sum(1 for r in transcript_results if r["txt"] and not r["skipped"])
    # 수강 단계에서 이미 실패로 센 강의를 다운로드 실패로 한 번 더 세지 않는다
    remaining = [lec for lec in selected if _key(lec) not in crashed]
    failures = _collect_failures(remaining, transcript_results, set())

    print(f"\n{'═' * 40}")
    print(f"  완료! (총 {format_duration(time.monotonic() - started)})")
    if watched:
        print(f"  수강 처리: {len(watched)}개")
    if download_only:
        print(f"  다운로드: {download_only}개 (수강완료 강의)")
    if transcribed:
        print(f"  스크립트: {transcribed}개 추출 → output/")
    if watch_failed:
        print(f"  수강 실패: {len(watch_failed)}개")
        for title in watch_failed:
            print(f"    - {title}")
    _print_failures("다운로드/전사 실패", failures)
    print(f"{'═' * 40}")
    return {
        "watched": [f"{lec['courseName']} — {lec['title']}" for lec in watched],
        "failed": watch_failed,
        "transcript_failed": [f"{t}: {r}" for t, r in failures],
    }


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
        logger.info("%d개 선택, 총 %s", len(selected), format_duration(sel_total))

        started = time.monotonic()
        pending = [lec for lec in selected if not transcript_exists(*_key(lec))]
        skipped = len(selected) - len(pending)
        if skipped:
            logger.info("스크립트가 이미 있는 %d개는 건너뜀", skipped)

        crashed: set[tuple[str, str]] = set()
        for i, lecture in enumerate(pending, 1):
            print(f"\n[{i}/{len(pending)}]", end=" ")
            lecture["isCompleted"] = True
            if await _process_safely(provider, page, lecture) is None:
                crashed.add(_key(lecture))
            await asyncio.sleep(3)

        # 백그라운드 다운로드/전사 완료 대기
        print()
        logger.info("다운로드/전사 완료 대기 중...")
        transcript_results = await provider.drain_tasks()
        fresh = [r for r in transcript_results if not r["skipped"]]
        skipped += len(transcript_results) - len(fresh)
        transcribed = sum(1 for r in fresh if r["txt"])
        downloaded = sum(1 for r in fresh if r["mp4"])

        print(f"\n{'═' * 40}")
        print(f"  완료! (총 {format_duration(time.monotonic() - started)})")
        print(f"  다운로드: {downloaded}개")
        if transcribed:
            print(f"  스크립트: {transcribed}개 추출 → output/")
        if skipped:
            print(f"  건너뜀: {skipped}개 (스크립트 이미 있음)")
        _print_failures("실패", _collect_failures(pending, transcript_results, crashed))
        print(f"{'═' * 40}")
        break


def _parse_args(plugins=None, argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LMS 자동 수강 시스템")
    parser.add_argument("--auto", action="store_true", help="묻지 않고 미수강 강의를 전부 수강")
    parser.add_argument("--check", action="store_true", help="과제·퀴즈·시험·공지·출석을 JSON 으로")
    parser.add_argument(
        "--school",
        action="append",
        choices=sorted(SCHOOL_CONFIGS),
        help="학교 (여러 번 가능, 없으면 계정이 있는 학교 전부)",
    )
    parser.add_argument("--report", help="결과 JSON 을 저장할 경로 (없으면 표준출력)")
    if plugins:
        for plugin in plugins:
            plugin.add_arguments(parser)
    return parser.parse_args(argv)


async def _login(provider: LMSProvider, page: Page) -> list[Course]:
    """배치용 로그인 — 비밀번호를 다시 묻지 않고, 응답 지연만 3번까지 다시 시도한다"""
    for attempt in range(3):
        try:
            await provider.login(page)
            return await provider.get_courses(page)
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
            logger.warning("LMS 응답 지연 — 다시 시도합니다 (%d/3)", attempt + 2)
            await page.goto("about:blank")
    return []


async def _run_school(page: Page, provider: LMSProvider, args: argparse.Namespace) -> dict:
    courses = await _login(provider, page)
    entry: dict[str, Any] = {"courses": [c["name"] for c in courses]}
    if args.auto:
        lectures: list[Lecture] = []
        for course in courses:
            if course["videoCount"] > 0:
                lectures += await provider.get_lectures(page, course["courseId"], course["name"])
        pending = [lec for lec in lectures if not lec["isCompleted"]]
        entry["watch"] = (
            await _watch_selected(page, provider, pending)
            if pending
            else {"watched": [], "failed": [], "transcript_failed": []}
        )
    if args.check:
        for key, method in (
            ("assignments", "get_assignments"),
            ("notices", "get_notices"),
            ("attendance", "get_attendance"),
        ):
            fn = getattr(provider, method, None)
            if fn is None:
                continue
            try:
                entry[key] = await fn(page, courses)
            except Exception as e:
                logger.exception("%s 수집 실패", key)
                entry.setdefault("errors", []).append(f"{key}: {type(e).__name__}: {e}")
    return entry


async def run_batch(args: argparse.Namespace) -> int:
    """--auto / --check: 묻는 것 없이 돌고 결과를 JSON 으로 남긴다 (launchd 용)"""
    schools = args.school or [s for s, c in SCHOOL_CONFIGS.items() if c.userid and c.password]
    report: dict[str, Any] = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "schools": {},
    }
    async with async_playwright() as p:
        for school in schools:
            provider = get_provider(school)
            # 수강은 headless 면 플레이어 완료 이벤트가 안 나서 창을 띄운다
            page, browser, _context = await setup_browser(p, headless=not args.auto)
            try:
                report["schools"][school] = await _run_school(page, provider, args)
            except Exception as e:
                logger.exception("%s 실패", school)
                report["schools"][school] = {"error": f"{type(e).__name__}: {e}"}
            finally:
                await _close_browser(browser)
    report["finished"] = datetime.now().isoformat(timespec="seconds")

    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    else:
        print(text)
    failed = any("error" in e for e in report["schools"].values())
    return 1 if failed else 0


# 정상 닫기는 0.1초. Ctrl+C 땐 Playwright 드라이버(handle_sigint)가 브라우저를 먼저 닫아
# close() 가 응답을 못 받고 이 시간만큼 기다린다
BROWSER_CLOSE_TIMEOUT_SEC = 3


async def _close_browser(browser: Any) -> None:
    # Chrome 이 멈추면 close() 가 끝나지 않아 프로세스가 터미널에 계속 남는다
    try:
        await asyncio.wait_for(browser.close(), BROWSER_CLOSE_TIMEOUT_SEC)
    except Exception as e:
        logger.warning("브라우저 닫기 실패 — 그대로 종료: %s", e)


def run() -> None:
    """Ctrl+C 한 번이면 브라우저를 닫고 끝낸다.

    asyncio.run() 을 쓰지 않는다 — Ctrl+C 를 작업 취소로 바꾸는데, 번호 입력(input) 중엔
    루프가 막혀 취소가 전달되지 않아 첫 Ctrl+C 가 무시되고 프로세스·Chrome 이 남았다.
    """
    loop = asyncio.new_event_loop()
    task = loop.create_task(main())
    try:
        loop.run_until_complete(task)
    except KeyboardInterrupt:
        # 재생 대기(sleep) 중이면 main 의 finally 가 아직 안 돌았다 — 취소해서 브라우저를 닫게 한다
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                loop.run_until_complete(task)
        # 끝난 작업의 KeyboardInterrupt 를 꺼내 두지 않으면 asyncio 가 traceback 을 찍는다
        with contextlib.suppress(BaseException):
            task.result()
        print("\n중단했습니다.")
        sys.exit(130)
    finally:
        loop.close()


def cli_entry() -> None:
    """project.scripts 엔트리포인트"""
    setup_logging()
    run()


async def main() -> None:
    # requests는 certifi 번들만 신뢰해 사내망 TLS 검사 프록시 인증서를 거부한다.
    # OS 신뢰 저장소를 쓰게 해야 영상 CDN 다운로드가 된다.
    truststore.inject_into_ssl()

    setup_logging()
    plugins = discover_plugins()
    args = _parse_args(plugins)

    if args.auto or args.check:
        sys.exit(await run_batch(args))

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
                page, browser, _context = await setup_browser(p, headless=True)
            except Exception:
                logger.warning("headless 실패, headed로 재시도")
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
                await _close_browser(browser)
        return

    # 대화형 모드
    print("=" * 60)
    print(f"  {provider.display_name} LMS 자동 수강 시스템")
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
                    await provider.login(page)
                    courses = await provider.get_courses(page)
                    break
                except PlaywrightTimeoutError as e:
                    if attempt == 2:
                        logger.error("LMS 응답 없음 3회. 종료합니다: %s", e)
                        sys.exit(1)
                    logger.warning("LMS 응답 지연 — 다시 시도합니다 (%d/3)", attempt + 2)
                    await page.goto("about:blank")
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
            await _close_browser(browser)
