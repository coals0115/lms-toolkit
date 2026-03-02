"""숭실대 LMS 자동 수강 시스템

mypage에서 미수강 동영상 과목을 감지하고,
각 강의를 1x 배속으로 재생하여 수강 인정 처리.

Usage:
    python -m src.auto_watch
"""

import asyncio
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import requests as req_lib
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, Frame, Request

load_dotenv()

USERID = os.getenv("USERID")
PASSWORD = os.getenv("PASSWORD")

BASE_URL = "https://canvas.ssu.ac.kr"
MYPAGE_URL = f"{BASE_URL}/accounts/1/external_tools/67?launch_type=global_navigation"

PROJECT_DIR = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"


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


async def get_unwatched_courses(page: Page) -> List[Dict]:
    """마이페이지에서 미수강 동영상이 있는 과목 목록 반환"""
    print("\n[INFO] 마이페이지에서 미수강 과목 탐색 중...")
    await page.goto(MYPAGE_URL, wait_until="networkidle")
    await login_if_needed(page)

    # 로그인 후 마이페이지로 리디렉션 안 됐으면 재접속
    if "external_tools/67" not in page.url:
        await page.goto(MYPAGE_URL, wait_until="networkidle")

    frame = await get_tool_content_frame(page)
    await frame.wait_for_selector(".xn-student-course-container", timeout=15000)

    courses = await frame.evaluate("""
        () => {
            const containers = document.querySelectorAll('.xn-student-course-container');
            return Array.from(containers).map(container => {
                const linkEl = container.querySelector('a.xnscc-header-redirect-link');
                const href = linkEl?.href || '';
                const courseId = href.match(/courses\\/(\\d+)/)?.[1] || '';

                const todoLeft = container.querySelector('.xnscc-header-todo-info-left');
                const todoText = todoLeft?.innerText || '';
                const videoMatch = todoText.match(/(\\d+)\\s*\\n\\s*동영상/);
                const videoCount = videoMatch ? parseInt(videoMatch[1]) : 0;

                const nameEl = container.querySelector('.xnscc-header-course-info');
                const rawName = nameEl?.textContent?.trim() || '';
                const name = rawName.split('\\n')[0].trim();

                return { name, courseId, videoCount };
            }).filter(c => c.courseId);
        }
    """)

    unwatched = [c for c in courses if c["videoCount"] > 0]

    if unwatched:
        print(f"[INFO] 미수강 동영상이 있는 과목 {len(unwatched)}개:")
        for c in unwatched:
            print(f"  • {c['name']} — 동영상 {c['videoCount']}개")
    else:
        print("[INFO] 미수강 동영상 없음!")

    return unwatched


async def get_unwatched_lectures(page: Page, course_id: str, course_name: str = "") -> List[Dict]:
    """과목의 주차학습 페이지에서 미수강 강의 목록 반환"""
    url = f"{BASE_URL}/courses/{course_id}/external_tools/71"
    print(f"\n[INFO] 주차학습 페이지 로드 중... (course {course_id})")
    await page.goto(url, wait_until="networkidle")
    await login_if_needed(page)
    if "external_tools/71" not in page.url:
        await page.goto(url, wait_until="networkidle")

    frame = await get_tool_content_frame(page)

    # "모두 펼치기" 클릭하여 전체 주차 확장
    try:
        expand_btn = await frame.query_selector('text="모두 펼치기"')
        if expand_btn:
            await expand_btn.click()
            await asyncio.sleep(2)
            print("[INFO] 전체 주차 펼침")
    except Exception:
        pass

    lectures = await frame.evaluate("""
        () => {
            const items = document.querySelectorAll('.xnmb-module_item-outer-wrapper');
            return Array.from(items).map(item => {
                const link = item.querySelector('a.xnmb-module_item-left-title');
                if (!link) return null;

                const href = link.href || '';
                const title = link.textContent?.trim() || '';

                // 텍스트 노드를 개별 추출 후 공백으로 연결
                // (innerText는 인접 인라인 요소를 구분자 없이 합쳐서
                //  "11:59"+"24:22" → "11:5924:22"가 되는 버그 발생)
                const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
                const textParts = [];
                while (walker.nextNode()) {
                    const t = walker.currentNode.textContent.trim();
                    if (t) textParts.push(t);
                }
                const text = textParts.join(' ');

                // 완료 여부
                const isCompleted = text.includes('완료') && !text.includes('미완료');

                // 시작일 추출: "시작 M월 D일" 패턴
                const startMatch = text.match(/시작\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                let startDate = null;
                if (startMatch) {
                    const now = new Date();
                    startDate = new Date(now.getFullYear(), parseInt(startMatch[1]) - 1, parseInt(startMatch[2]));
                }

                // 마감일 추출: "마감 M월 D일" 또는 "종료 M월 D일" 패턴
                const endMatch = text.match(/(?:마감|종료)\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                let deadline = null;
                if (endMatch) {
                    const now = new Date();
                    deadline = new Date(now.getFullYear(), parseInt(endMatch[1]) - 1, parseInt(endMatch[2]));
                }

                // 재생 시간 추출: MM:SS 패턴 중 영상 길이인 것을 찾음
                const allTimes = [...text.matchAll(/(\\d+):(\\d{2})(?!\\d)/g)];
                let durationSec = 0;
                for (const m of allTimes) {
                    const mins = parseInt(m[1]);
                    const secs = parseInt(m[2]);
                    if (secs < 60 && mins >= 1 && mins <= 180) {
                        durationSec = mins * 60 + secs;
                    }
                }

                return { title, href, isCompleted, durationSec, startDate: startDate?.toISOString() || null, deadline: deadline?.toISOString() || null };
            }).filter(item => {
                if (!item || !item.href || item.durationSec <= 0 || item.isCompleted) return false;
                // 시작일이 있으면 오늘 이후인 것은 제외
                if (item.startDate) {
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);
                    if (new Date(item.startDate) > today) return false;
                }
                return true;
            });
        }
    """)

    # 각 강의에 과목명 추가
    for lec in lectures:
        lec["courseName"] = course_name

    print(f"[INFO] 미수강 강의 {len(lectures)}개:")
    for lec in lectures:
        m, s = divmod(lec["durationSec"], 60)
        print(f"  • {lec['title']} ({m}:{s:02d})")

    return lectures


def select_lectures(all_lectures: List[Dict]) -> List[Dict]:
    """미수강 강의 목록 표시 + 사용자 선택 → 선택된 강의 리스트 반환"""
    if not all_lectures:
        return []

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  미수강 강의 {len(all_lectures)}개:")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    total_sec = 0
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for i, lec in enumerate(all_lectures, 1):
        m, s = divmod(lec["durationSec"], 60)
        total_sec += lec["durationSec"]

        # D-day 계산
        d_day = ""
        if lec.get("deadline"):
            deadline_dt = datetime.fromisoformat(lec["deadline"].replace("Z", "+00:00")).replace(tzinfo=None)
            days_left = (deadline_dt - today).days
            d_day = f" D-{days_left}" if days_left >= 0 else f" D+{abs(days_left)}"

        print(f"  [{i}] {lec['courseName']} — {lec['title']} ({m}:{s:02d}){d_day}")

    total_m, total_s = divmod(total_sec, 60)
    total_h, total_m = divmod(total_m, 60)
    time_str = f"{total_h}:{total_m:02d}:{total_s:02d}" if total_h else f"{total_m}:{total_s:02d}"
    print(f"\n  총 재생시간: {time_str}")
    print()

    while True:
        try:
            choice = input("재생할 번호 (예: 1,2 / all / q): ").strip().lower()
        except EOFError:
            return []

        if choice == "q":
            return []
        if choice == "all":
            return all_lectures

        # 번호 파싱
        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(all_lectures):
                    selected.append(all_lectures[idx - 1])
                else:
                    print(f"  [WARN] {idx}번은 범위 밖 (1~{len(all_lectures)})")
            if selected:
                return selected
            print("  유효한 번호를 입력하세요.")
        except ValueError:
            print("  숫자, all, q 중 하나를 입력하세요.")


def _is_target_video_url(url: str) -> bool:
    """재생 중인 강의 영상 URL인지 판별"""
    if not url.endswith(".mp4"):
        return False
    if "commons.ssu.ac.kr" not in url and "commonscdn.com" not in url:
        return False
    if "intro.mp4" in url or "media_files" not in url:
        return False
    return True


def _safe_filename(name: str) -> str:
    """파일시스템에 안전한 파일명으로 변환"""
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip()


async def download_and_transcribe(video_url: str, course_name: str, title: str) -> Dict:
    """영상 다운로드 + 음성→텍스트 전사 (재생과 병렬 실행)"""
    loop = asyncio.get_event_loop()
    result = {"mp4": None, "txt": None}

    course_dir = OUTPUT_DIR / _safe_filename(course_name)
    course_dir.mkdir(parents=True, exist_ok=True)

    safe_title = _safe_filename(title)
    mp4_path = course_dir / f"{safe_title}.mp4"
    txt_path = course_dir / f"{safe_title}.txt"

    # 1. 다운로드
    try:
        def _download():
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://commons.ssu.ac.kr/",
            }
            resp = req_lib.get(video_url, stream=True, headers=headers)
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_report = 0
            with open(mp4_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        # 50MB마다 중간 보고
                        if total and downloaded - last_report >= 50 * 1024 * 1024:
                            pct = downloaded / total * 100
                            print(f"  ├ 다운로드: {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct:.0f}%)")
                            last_report = downloaded
            return str(mp4_path)

        print(f"  ├ 다운로드: 시작...")
        result["mp4"] = await loop.run_in_executor(None, _download)
        size_mb = mp4_path.stat().st_size / (1024 * 1024)
        print(f"  ├ 다운로드: 완료 ({size_mb:.1f}MB)")
    except Exception as e:
        print(f"  ├ 다운로드: 실패 — {e}")
        traceback.print_exc()
        return result

    # 2. mp4 → wav → txt
    try:
        def _transcribe():
            import time
            from src.audio_pipeline.converter import convert_mp4_to_wav
            from src.audio_pipeline.transcriber import WhisperTranscriber

            wav_path = course_dir / f"{safe_title}.wav"

            print(f"  ├ 스크립트: [1/3] mp4 → wav 변환 중...")
            convert_mp4_to_wav(str(mp4_path), str(wav_path))

            print(f"  ├ 스크립트: [2/3] Whisper 모델 로딩...")
            transcriber = WhisperTranscriber()

            print(f"  ├ 스크립트: [3/3] 음성 → 텍스트 전사 중...")
            t_start = time.time()
            transcriber.transcribe(str(wav_path), str(txt_path))
            elapsed = time.time() - t_start
            em, es = divmod(int(elapsed), 60)
            print(f"  ├ 스크립트: 전사 완료 ({em}분 {es}초)")

            wav_path.unlink(missing_ok=True)
            return str(txt_path)

        result["txt"] = await loop.run_in_executor(None, _transcribe)
        print(f"  └ 스크립트: 저장 완료 → {txt_path.relative_to(PROJECT_DIR)}")
    except Exception as e:
        print(f"  └ 스크립트: 전사 실패 — {e}")
        traceback.print_exc()

    return result


def find_commons_frame(page: Page) -> Optional[Frame]:
    """page.frames에서 commons.ssu.ac.kr 프레임 찾기"""
    for f in page.frames:
        if "commons.ssu.ac.kr" in f.url:
            return f
    return None


async def watch_lecture(page: Page, lecture: dict) -> Dict:
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
    # #confirm-dialog > .confirm-cancel-btn (기존 video_parser.py에서 확인된 selector)
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


if __name__ == "__main__":
    asyncio.run(main())
