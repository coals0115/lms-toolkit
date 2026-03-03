"""강의 재생 및 수강 처리"""

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


async def watch_lecture(page: Page, lecture: dict) -> dict:
    """강의 페이지 이동 → 재생 → 완료 대기 + 병렬 다운로드/전사

    Returns:
        {"attended": bool, "mp4": str|None, "txt": str|None}
    """
    title = lecture["title"]
    url = lecture["href"]
    duration_sec = lecture["durationSec"]
    course_name = lecture.get("courseName", "unknown")

    m, s = divmod(duration_sec, 60)
    print(f"\n{'─' * 50}")
    print(f"[PLAY] {title} ({m}:{s:02d})")
    print(f"{'─' * 50}")

    # 비디오 URL 캡처 준비
    captured_video_url = {"url": None}

    def on_request(request: Request):
        if captured_video_url["url"] is None and _is_target_video_url(request.url):
            captured_video_url["url"] = request.url

    page.on("request", on_request)

    await page.goto(url, wait_until="networkidle")

    # tool_content iframe 대기
    tool_frame = await get_tool_content_frame(page)

    # commons iframe 대기
    await tool_frame.wait_for_selector(".xnlailvc-commons-frame", timeout=20000)
    await asyncio.sleep(3)

    commons = find_commons_frame(page)
    if not commons:
        print("[ERROR] commons.ssu.ac.kr iframe을 찾을 수 없음")
        page.remove_listener("request", on_request)
        return {"attended": False, "mp4": None, "txt": None}

    # "이전에 시청했던 XX:XX부터 이어서 보시겠습니까?" 다이얼로그 처리
    try:
        confirm_dialog = await commons.wait_for_selector("#confirm-dialog", timeout=3000)
        if confirm_dialog:
            cancel_btn = await confirm_dialog.query_selector(".confirm-cancel-btn")
            if cancel_btn:
                await cancel_btn.click()
                print("[INFO] 이어보기 다이얼로그 → '아니오' (처음부터 재생)")
                await asyncio.sleep(1)
    except Exception:
        pass  # 다이얼로그가 안 뜨면 정상 — 처음 보는 강의

    # 재생 버튼 대기 및 클릭
    try:
        await commons.wait_for_selector(".vc-front-screen-play-btn", timeout=15000)
        await asyncio.sleep(1)
        await commons.click(".vc-front-screen-play-btn")
        print("[INFO] 재생 시작")
    except Exception as e:
        print(f"[ERROR] 재생 버튼 클릭 실패: {e}")
        page.remove_listener("request", on_request)
        return {"attended": False, "mp4": None, "txt": None}

    await asyncio.sleep(3)

    # 비디오 URL 캡처 대기 (최대 5초)
    for _ in range(50):
        if captured_video_url["url"]:
            break
        await asyncio.sleep(0.1)

    page.remove_listener("request", on_request)

    # 비디오 URL이 잡혔으면 병렬 다운로드+전사 시작
    transcript_task = None
    if captured_video_url["url"]:
        print(f"  ├ 영상 URL 캡처 완료")
        transcript_task = asyncio.create_task(
            download_and_transcribe(captured_video_url["url"], course_name, title)
        )
    else:
        print(f"  ├ 영상 URL 미감지 — 스크립트 추출 건너뜀")

    # 재생 진행 모니터링
    start_time = datetime.now()
    timeout_sec = duration_sec + 60  # 1분 여유
    last_log_time = 0
    attended = False

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
                attended = True
                break

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
            break

        await asyncio.sleep(5)

    # 다운로드/전사 완료 대기
    transcript_result = {"mp4": None, "txt": None}
    if transcript_task:
        if not transcript_task.done():
            print("  [INFO] 스크립트 추출 완료 대기 중...")
        transcript_result = await transcript_task

    return {"attended": attended, **transcript_result}
