"""강의 재생(미수강) / 다운로드(수강완료) 처리"""

from __future__ import annotations

import asyncio
from datetime import datetime

from playwright.async_api import Page, Frame, Request

from .browser import get_tool_content_frame
from .cli import _is_target_video_url
from .transcription import download_and_transcribe


def find_commons_frame(page: Page) -> Frame | None:
    """page.frames에서 commons.ssu.ac.kr 프레임 찾기"""
    for f in page.frames:
        if "commons.ssu.ac.kr" in f.url:
            return f
    return None


async def _enter_lecture_page(page: Page, lecture: dict) -> Frame | None:
    """강의 페이지 진입 → iframe 대기 → 이어보기 처리 → commons frame 반환.
    실패 시 None 반환."""
    await page.goto(lecture["href"], wait_until="networkidle")

    tool_frame = await get_tool_content_frame(page)
    await tool_frame.wait_for_selector(".xnlailvc-commons-frame", timeout=20000)
    await asyncio.sleep(3)

    commons = find_commons_frame(page)
    if not commons:
        print("[ERROR] commons.ssu.ac.kr iframe을 찾을 수 없음")
        return None

    # "이전에 시청했던 XX:XX부터 이어서 보시겠습니까?" 다이얼로그 처리
    try:
        ok_btn = await commons.wait_for_selector(
            ".confirm-ok-btn", timeout=5000, state="visible",
        )
        if ok_btn:
            await ok_btn.click()
            print("[INFO] 이어보기 다이얼로그 → '예' (이어서 재생)")
            await asyncio.sleep(1)
    except Exception:
        pass  # 다이얼로그가 안 뜨면 정상 — 처음 보는 강의

    return commons


async def _click_play_and_capture_url(page: Page, commons: Frame) -> str | None:
    """재생 버튼 클릭 + 비디오 URL 캡처. 실패 시 None."""
    captured_video_url = {"url": None}

    def on_request(request: Request):
        if captured_video_url["url"] is None and _is_target_video_url(request.url):
            captured_video_url["url"] = request.url

    page.on("request", on_request)

    try:
        await commons.wait_for_selector(".vc-front-screen-play-btn", timeout=15000)
        await asyncio.sleep(1)
        await commons.click(".vc-front-screen-play-btn")
        print("[INFO] 재생 시작")
    except Exception as e:
        print(f"[ERROR] 재생 버튼 클릭 실패: {e}")
        page.remove_listener("request", on_request)
        return None

    # 재생 버튼 클릭 후 인트로 재생 → 이어보기 다이얼로그가 뜰 수 있음 (~5초 소요)
    try:
        ok_btn = await commons.wait_for_selector(
            ".confirm-ok-btn", timeout=10000, state="visible",
        )
        if ok_btn:
            await ok_btn.click()
            print("[INFO] 이어보기 다이얼로그 → '예' (이어서 재생)")
    except Exception:
        pass

    await asyncio.sleep(3)

    # 비디오 URL 캡처 대기 (최대 5초)
    for _ in range(50):
        if captured_video_url["url"]:
            break
        await asyncio.sleep(0.1)

    page.remove_listener("request", on_request)
    return captured_video_url["url"]


async def _monitor_playback(commons: Frame, title: str, duration_sec: int) -> bool:
    """재생 진행 모니터링. 수강 완료 시 True 반환."""
    start_time = datetime.now()
    timeout_sec = duration_sec + 60  # 1분 여유
    last_log_time = 0

    while True:
        elapsed = (datetime.now() - start_time).total_seconds()

        try:
            progress = await commons.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    for (const v of videos) {
                        if (v.duration > 10) {
                            return {
                                currentTime: v.currentTime,
                                duration: v.duration,
                                paused: v.paused,
                                ended: v.ended,
                                rate: v.playbackRate
                            };
                        }
                    }
                    return null;
                }
            """)
        except Exception:
            progress = None

        if progress:
            pct = (progress["currentTime"] / progress["duration"] * 100) if progress["duration"] else 0
            cur_m, cur_s = divmod(int(progress["currentTime"]), 60)
            dur_m, dur_s = divmod(int(progress["duration"]), 60)

            # 30초마다 로그 출력
            if progress["currentTime"] - last_log_time >= 30 or pct >= 99:
                print(
                    f"  [{cur_m}:{cur_s:02d} / {dur_m}:{dur_s:02d}] "
                    f"{pct:.1f}% | {progress['rate']}x"
                )
                last_log_time = progress["currentTime"]

            # 완료 체크
            if progress["ended"] or pct >= 99.5:
                print(f"[DONE] {title} 수강 완료!")
                await asyncio.sleep(3)  # 완료 이벤트가 서버에 전송될 시간
                return True

            # 일시정지 감지 → 자동 재개
            if progress["paused"] and progress["currentTime"] > 1:
                print(f"  [WARN] 일시정지 감지 ({pct:.1f}%), 재개 시도...")
                try:
                    await commons.evaluate("""
                        () => {
                            const videos = document.querySelectorAll('video');
                            for (const v of videos) {
                                if (v.duration > 10 && v.paused) v.play();
                            }
                        }
                    """)
                except Exception:
                    pass

        # 타임아웃
        if elapsed > timeout_sec:
            print(f"[WARN] 타임아웃 ({elapsed:.0f}s). 다음 강의로 이동.")
            return False

        await asyncio.sleep(5)


async def process_lecture(page: Page, lecture: dict) -> dict:
    """강의 처리: 미수강이면 재생+출석, 수강완료면 다운로드만.

    Returns:
        {"attended": bool, "download_only": bool, "mp4": str|None, "txt": str|None}
    """
    title = lecture["title"]
    duration_sec = lecture["durationSec"]
    course_name = lecture.get("courseName", "unknown")
    is_completed = lecture.get("isCompleted", False)

    m, s = divmod(duration_sec, 60)
    print(f"\n{'─' * 50}")
    if is_completed:
        print(f"[DL] {title} (수강완료 — 다운로드만)")
    else:
        print(f"[PLAY] {title} ({m}:{s:02d})")
    print(f"{'─' * 50}")

    # 공통: 페이지 진입 + iframe 준비
    commons = await _enter_lecture_page(page, lecture)
    if not commons:
        return {"attended": False, "download_only": False, "mp4": None, "txt": None}

    # 공통: 재생 버튼 클릭 + URL 캡처
    video_url = await _click_play_and_capture_url(page, commons)

    # 수강완료 강의: 즉시 비디오 정지
    if is_completed and video_url:
        try:
            await commons.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    for (const v of videos) {
                        if (v.duration > 10) v.pause();
                    }
                }
            """)
            print("[INFO] 비디오 정지 (수강완료 — URL 캡처 완료)")
        except Exception:
            pass

    # 공통: 다운로드+전사 시작
    transcript_task = None
    if video_url:
        print(f"  ├ 영상 URL 캡처 완료")
        transcript_task = asyncio.create_task(
            download_and_transcribe(video_url, course_name, title)
        )
    else:
        print(f"  ├ 영상 URL 미감지 — 스크립트 추출 건너뜀")

    # 미수강: 재생 진행 모니터링
    attended = False
    if not is_completed:
        attended = await _monitor_playback(commons, title, duration_sec)

    # 공통: 다운로드/전사 완료 대기
    transcript_result = {"mp4": None, "txt": None}
    if transcript_task:
        if not transcript_task.done():
            print("  [INFO] 스크립트 추출 완료 대기 중...")
        transcript_result = await transcript_task

    return {"attended": attended, "download_only": is_completed, **transcript_result}
