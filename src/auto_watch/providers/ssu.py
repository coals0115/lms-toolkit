"""숭실대 Canvas LMS Provider"""

import asyncio
import contextlib
import logging
from datetime import datetime

from playwright.async_api import Frame, Page, Request

from ..config import (
    LOGIN_TIMEOUT_MS,
    PLAYBACK_COMPLETION_THRESHOLD,
    PLAYBACK_LOG_INTERVAL_SEC,
    PLAYBACK_TIMEOUT_BUFFER_SEC,
    RESUME_DIALOG_POST_PLAY_TIMEOUT_MS,
    SELECTOR_TIMEOUT_MS,
    SchoolConfig,
)
from ..exceptions import BrowserError, LoginError
from ..transcription import download_and_transcribe
from ..types import Course, Lecture, ProcessResult, TranscriptResult

logger = logging.getLogger(__name__)


class SSUProvider:
    def __init__(self, config: SchoolConfig):
        self._config = config
        self._base_url = config.base_url
        self._pending_tasks: list[asyncio.Task] = []
        self._mypage_url = (
            f"{self._base_url}/accounts/1/external_tools/67?launch_type=global_navigation"
        )

    @property
    def name(self) -> str:
        return "ssu"

    @property
    def display_name(self) -> str:
        return "숭실대"

    def get_credentials(self) -> tuple[str, str]:
        return (self._config.userid or "", self._config.password or "")

    # ── 로그인 ──────────────────────────────────────────────

    async def login(self, page: Page) -> None:
        """Canvas SSO 로그인 수행"""
        logger.info("마이페이지에서 과목 탐색 중...")
        await page.goto(self._mypage_url, wait_until="networkidle")
        await self._sso_login_if_needed(page)

        # 로그인 후 마이페이지로 리디렉션 안 됐으면 재접속
        if "external_tools/67" not in page.url:
            await page.goto(self._mypage_url, wait_until="networkidle")

    async def _sso_login_if_needed(self, page: Page) -> None:
        """현재 페이지가 로그인 페이지면 SSO 로그인 수행"""
        if "login" not in page.url and "smartid" not in page.url:
            return

        logger.info("로그인 필요 — %s", page.url)

        # Canvas 로그인 페이지 → SSO 버튼 클릭
        login_btn = await page.query_selector(".login_btn a")
        if login_btn:
            await login_btn.click()
            await page.wait_for_load_state("networkidle")

        # SSO 로그인 폼 입력
        await page.wait_for_selector("input#userid", timeout=LOGIN_TIMEOUT_MS)

        userid, password = self.get_credentials()

        await page.click("input#userid")
        await page.fill("input#userid", "")
        await page.type("input#userid", userid, delay=50)

        await page.click("input#pwd")
        await page.fill("input#pwd", "")
        await page.type("input#pwd", password, delay=50)

        await asyncio.sleep(1)

        # JS로 직접 폼 제출
        await page.evaluate("""
            () => {
                const btn = document.querySelector('a.btn_login');
                if (btn) { btn.click(); return 'btn_click'; }
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
                logger.info("리디렉션 대기... (%s)", current_url[:60])

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
                    logger.error("SSO 에러: %s", error_msg)
                    break

        if "login" in page.url or "smartid" in page.url:
            raise LoginError("로그인 실패 — .env의 SSU_USERID/SSU_PASSWORD 확인 필요")

        logger.info("로그인 성공 — %s", page.url)

    # ── iframe 헬퍼 ─────────────────────────────────────────

    async def _get_tool_content_frame(
        self, page: Page, timeout: int = 30000, *, lecture_page: bool = False
    ) -> Frame:
        """tool_content iframe의 Frame 객체를 반환"""
        await page.wait_for_selector("#tool_content", timeout=timeout)
        frame = page.frame("tool_content")
        if not frame:
            raise BrowserError("tool_content frame not found")

        if lecture_page:
            # 강의 개별 페이지: commons 플레이어 iframe 대기
            await frame.wait_for_selector(
                ".xnlailvc-commons-frame", timeout=timeout
            )
        else:
            # 주차학습/마이페이지: AJAX 콘텐츠 로드 대기
            # 주의: "모두 접기"는 빈 상태에서도 보이므로 조건에서 제외
            await frame.wait_for_function(
                """() => {
                    if (document.querySelector('.xnmb-module_item-outer-wrapper')) return true;
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if (b.textContent.includes('모두 펼치기')) return true;
                    }
                    if (document.querySelector('.xn-student-course-container')) return true;
                    return false;
                }""",
                timeout=timeout,
            )
        return frame

    def _find_commons_frame(self, page: Page) -> Frame | None:
        """page.frames에서 commons.ssu.ac.kr 프레임 찾기"""
        for f in page.frames:
            if "commons.ssu.ac.kr" in f.url:
                return f
        return None

    # ── 과목/강의 탐색 ─────────────────────────────────────

    async def get_courses(self, page: Page) -> list[Course]:
        """마이페이지에서 모든 과목 목록 반환"""
        # login()에서 이미 마이페이지에 있을 수 있지만, 안전하게 확인
        if "external_tools/67" not in page.url:
            await page.goto(self._mypage_url, wait_until="networkidle")
            await self._sso_login_if_needed(page)
            if "external_tools/67" not in page.url:
                await page.goto(self._mypage_url, wait_until="networkidle")

        frame = await self._get_tool_content_frame(page)
        await frame.wait_for_selector(".xn-student-course-container", timeout=15000)

        courses: list[Course] = await frame.evaluate("""
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

        if not courses:
            logger.info("수강 중인 과목이 없습니다")
            return courses

        unwatched_count = sum(1 for c in courses if c["videoCount"] > 0)
        logger.info("과목 %d개 발견 (미수강 동영상 있는 과목: %d개)", len(courses), unwatched_count)
        for c in courses:
            if c["videoCount"] > 0:
                logger.info("  • %s — 미수강 %d개", c["name"], c["videoCount"])
            else:
                logger.info("  • %s — 수강 완료", c["name"])

        return courses

    async def get_lectures(
        self, page: Page, course_id: str, course_name: str = ""
    ) -> list[Lecture]:
        """과목의 주차학습 페이지에서 강의 목록 반환"""
        url = f"{self._base_url}/courses/{course_id}/external_tools/71"
        logger.info("주차학습 페이지 로드 중... (course %s)", course_id)
        await page.goto(url, wait_until="load")
        await self._sso_login_if_needed(page)
        if "external_tools/71" not in page.url:
            await page.goto(url, wait_until="load")

        frame = await self._get_tool_content_frame(page)

        # "모두 펼치기" 클릭하여 전체 주차 확장
        try:
            expand_btn = await frame.query_selector('text="모두 펼치기"')
            if expand_btn:
                await expand_btn.click()
                # 클릭 후 강의 아이템이 나타날 때까지 대기 (최대 10초)
                await frame.wait_for_selector(
                    ".xnmb-module_item-outer-wrapper", timeout=10000
                )
                logger.info("전체 주차 펼침")
        except Exception:
            pass

        lectures: list[Lecture] = await frame.evaluate("""
            () => {
                const items = document.querySelectorAll('.xnmb-module_item-outer-wrapper');
                return Array.from(items).map(item => {
                    const link = item.querySelector('a.xnmb-module_item-left-title');
                    if (!link) return null;

                    const iconEl = item.querySelector('i.xnmb-module_item-icon');
                    const iconClasses = iconEl ? iconEl.className : '';
                    const isVideo = iconClasses.includes('movie') || iconClasses.includes('readystream');
                    const itemType = isVideo ? 'movie'
                        : iconClasses.includes('file') ? 'file'
                        : iconClasses.includes('assignment') ? 'assignment'
                        : 'other';

                    const href = link.href || '';
                    const title = link.textContent?.trim() || '';

                    const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
                    const textParts = [];
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (t) textParts.push(t);
                    }
                    const text = textParts.join(' ');

                    const isCompleted = text.includes('완료') && !text.includes('미완료');

                    const startMatch = text.match(/시작\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                    let startDate = null;
                    if (startMatch) {
                        const now = new Date();
                        startDate = new Date(now.getFullYear(), parseInt(startMatch[1]) - 1, parseInt(startMatch[2]));
                    }

                    const endMatch = text.match(/(?:마감|종료)\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                    let deadline = null;
                    if (endMatch) {
                        const now = new Date();
                        deadline = new Date(now.getFullYear(), parseInt(endMatch[1]) - 1, parseInt(endMatch[2]));
                    }

                    const allTimes = [...text.matchAll(/(\\d+):(\\d{2})(?!\\d)/g)];
                    let durationSec = 0;
                    for (const m of allTimes) {
                        const mins = parseInt(m[1]);
                        const secs = parseInt(m[2]);
                        if (secs < 60 && mins >= 1 && mins <= 180) {
                            durationSec = mins * 60 + secs;
                        }
                    }

                    return { title, href, isCompleted, durationSec, itemType, startDate: startDate?.toISOString() || null, deadline: deadline?.toISOString() || null };
                }).filter(item => {
                    if (!item || !item.href) return false;
                    if (item.itemType !== 'movie') return false;
                    if (item.durationSec <= 0) return false;
                    if (item.startDate) {
                        const today = new Date();
                        today.setHours(0, 0, 0, 0);
                        if (new Date(item.startDate) > today) return false;
                    }
                    return true;
                });
            }
        """)

        for lec in lectures:
            lec["courseName"] = course_name

        unwatched = [lec for lec in lectures if not lec["isCompleted"]]
        completed = [lec for lec in lectures if lec["isCompleted"]]
        logger.info(
            "강의 %d개 (미수강 %d / 수강완료 %d)", len(lectures), len(unwatched), len(completed)
        )
        for lec in lectures:
            m, s = divmod(lec["durationSec"], 60)
            status = "V" if lec["isCompleted"] else " "
            logger.info("  %s %s (%d:%02d)", status, lec["title"], m, s)

        return lectures

    # ── 강의 재생/다운로드 ──────────────────────────────────

    def _is_target_video_url(self, url: str) -> bool:
        """재생 중인 강의 영상 URL인지 판별"""
        if not url.endswith(".mp4"):
            return False
        if "commons.ssu.ac.kr" not in url and "commonscdn.com" not in url:
            return False
        return "intro.mp4" not in url and "media_files" in url

    async def _enter_lecture_page(self, page: Page, lecture: Lecture) -> Frame | None:
        """강의 페이지 진입 → iframe 대기 → 이어보기 처리 → commons frame 반환"""
        await page.goto(lecture["href"], wait_until="load")

        await self._get_tool_content_frame(page, lecture_page=True)

        # commons iframe이 실제 URL로 로드될 때까지 대기
        commons = None
        for _ in range(60):  # 최대 30초
            commons = self._find_commons_frame(page)
            if commons:
                break
            await asyncio.sleep(0.5)

        if not commons:
            logger.error("commons.ssu.ac.kr iframe을 찾을 수 없음")
            return None

        # commons 플레이어 초기화 대기 (재생 버튼 또는 이어보기 다이얼로그)
        try:
            await commons.wait_for_selector(
                ".vc-front-screen-play-btn, .confirm-ok-btn",
                timeout=SELECTOR_TIMEOUT_MS,
                state="visible",
            )
        except Exception:
            # 디버그: commons 내부 상태 확인
            try:
                state = await commons.evaluate("""() => ({
                    playBtn: !!document.querySelector('.vc-front-screen-play-btn'),
                    video: !!document.querySelector('video'),
                    bodyLen: document.body?.innerHTML?.length || 0,
                    bodyText: document.body?.innerText?.substring(0, 100) || ''
                })""")
                logger.warning("commons 플레이어 초기화 타임아웃: %s", state)
            except Exception:
                logger.warning("commons 플레이어 초기화 타임아웃 (상태 확인 실패)")

        # "이전에 시청했던 XX:XX부터 이어서 보시겠습니까?" 다이얼로그 처리
        try:
            ok_btn = await commons.query_selector(".confirm-ok-btn")
            if ok_btn and await ok_btn.is_visible():
                await ok_btn.click()
                logger.info("이어보기 다이얼로그 → '예' (이어서 재생)")
                await asyncio.sleep(1)
        except Exception:
            pass

        return commons

    async def _click_play_and_capture_url(self, page: Page, commons: Frame) -> str | None:
        """재생 버튼 클릭 + 비디오 URL 캡처"""
        captured_video_url: dict[str, str | None] = {"url": None}

        def on_request(request: Request):
            if captured_video_url["url"] is None and self._is_target_video_url(request.url):
                captured_video_url["url"] = request.url

        page.on("request", on_request)

        try:
            await commons.wait_for_selector(
                ".vc-front-screen-play-btn", timeout=SELECTOR_TIMEOUT_MS
            )
            await asyncio.sleep(1)
            await commons.click(".vc-front-screen-play-btn")
            logger.info("재생 시작")
        except Exception as e:
            logger.error("재생 버튼 클릭 실패: %s", e)
            page.remove_listener("request", on_request)
            return None

        # 재생 버튼 클릭 후 이어보기 다이얼로그가 뜰 수 있음
        try:
            ok_btn = await commons.wait_for_selector(
                ".confirm-ok-btn",
                timeout=RESUME_DIALOG_POST_PLAY_TIMEOUT_MS,
                state="visible",
            )
            if ok_btn:
                await ok_btn.click()
                logger.info("이어보기 다이얼로그 → '예' (이어서 재생)")
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

    async def _monitor_playback(self, commons: Frame, title: str, duration_sec: int) -> bool:
        """재생 진행 모니터링. 수강 완료 시 True 반환."""
        start_time = datetime.now()
        timeout_sec = duration_sec + PLAYBACK_TIMEOUT_BUFFER_SEC
        last_log_time = 0.0

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
                pct = (
                    (progress["currentTime"] / progress["duration"] * 100)
                    if progress["duration"]
                    else 0
                )
                cur_m, cur_s = divmod(int(progress["currentTime"]), 60)
                dur_m, dur_s = divmod(int(progress["duration"]), 60)

                if (
                    progress["currentTime"] - last_log_time >= PLAYBACK_LOG_INTERVAL_SEC
                    or pct >= 99
                ):
                    logger.info(
                        "[%d:%02d / %d:%02d] %.1f%% | %sx",
                        cur_m,
                        cur_s,
                        dur_m,
                        dur_s,
                        pct,
                        progress["rate"],
                    )
                    last_log_time = progress["currentTime"]

                if progress["ended"] or pct >= PLAYBACK_COMPLETION_THRESHOLD:
                    logger.info("[DONE] %s 수강 완료!", title)
                    await asyncio.sleep(3)
                    return True

                if progress["paused"] and progress["currentTime"] > 1:
                    logger.warning("일시정지 감지 (%.1f%%), 재개 시도...", pct)
                    with contextlib.suppress(Exception):
                        await commons.evaluate("""
                            () => {
                                const videos = document.querySelectorAll('video');
                                for (const v of videos) {
                                    if (v.duration > 10 && v.paused) v.play();
                                }
                            }
                        """)

            if elapsed > timeout_sec:
                logger.warning("타임아웃 (%.0fs). 다음 강의로 이동.", elapsed)
                return False

            await asyncio.sleep(5)

    async def process_lecture(
        self, page: Page, lecture: Lecture, *, defer_transcript: bool = False
    ) -> ProcessResult:
        """강의 처리: 미수강이면 재생+출석, 수강완료면 다운로드만"""
        title = lecture["title"]
        duration_sec = lecture["durationSec"]
        course_name = lecture.get("courseName", "unknown")
        is_completed = lecture.get("isCompleted", False)

        m, s = divmod(duration_sec, 60)
        print(f"\n{'─' * 50}")
        if is_completed:
            logger.info("[DL] %s (수강완료 — 다운로드만)", title)
        else:
            logger.info("[PLAY] %s (%d:%02d)", title, m, s)
        print(f"{'─' * 50}")

        commons = await self._enter_lecture_page(page, lecture)
        if not commons:
            return {"attended": False, "download_only": False, "mp4": None, "txt": None}

        video_url = await self._click_play_and_capture_url(page, commons)

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
                logger.info("비디오 정지 (수강완료 — URL 캡처 완료)")
            except Exception:
                pass

        # 다운로드+전사 시작
        transcript_task = None
        if video_url:
            logger.info("영상 URL 캡처 완료")
            transcript_task = asyncio.create_task(
                download_and_transcribe(
                    video_url,
                    course_name,
                    title,
                    referer="https://commons.ssu.ac.kr/",
                )
            )
        else:
            logger.info("영상 URL 미감지 — 스크립트 추출 건너뜀")

        # 미수강: 재생 진행 모니터링
        attended = False
        if not is_completed:
            attended = await self._monitor_playback(commons, title, duration_sec)

        # 다운로드/전사 완료 대기 (defer_transcript=True면 백그라운드 수집)
        if defer_transcript:
            if transcript_task:
                self._pending_tasks.append(transcript_task)
            return {
                "attended": attended,
                "download_only": is_completed,
                "mp4": None,
                "txt": None,
            }

        mp4 = None
        txt = None
        if transcript_task:
            if not transcript_task.done():
                logger.info("스크립트 추출 완료 대기 중...")
            transcript_result = await transcript_task
            mp4 = transcript_result.get("mp4")
            txt = transcript_result.get("txt")

        return {
            "attended": attended,
            "download_only": is_completed,
            "mp4": mp4,
            "txt": txt,
        }

    async def drain_tasks(self) -> list[TranscriptResult]:
        """대기 중인 백그라운드 다운로드/전사 작업 완료 대기"""
        if not self._pending_tasks:
            return []
        results = await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()
        valid: list[TranscriptResult] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.error("백그라운드 작업 실패: %s", r)
            else:
                valid.append(r)  # type: ignore[arg-type]
        return valid
