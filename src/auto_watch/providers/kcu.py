"""숭실사이버대 KCU LMS Provider"""

import asyncio
import contextlib
import logging

from playwright.async_api import Frame, Page, Request

from ..config import (
    IFRAME_TIMEOUT_MS,
    LOGIN_TIMEOUT_MS,
    PLAYBACK_COMPLETION_THRESHOLD,
    PLAYBACK_LOG_INTERVAL_SEC,
    PLAYBACK_TIMEOUT_BUFFER_SEC,
    SchoolConfig,
)
from ..exceptions import LoginError
from ..transcription import download_and_transcribe
from ..types import Course, Lecture, ProcessResult

logger = logging.getLogger(__name__)

# KCU 상수
_PORTAL_LOGIN_URL = "https://portal.kcu.ac/html/main/ssoko.html?returnUrl=https://lms.kcu.ac/login"
_DASHBOARD_URL = "https://lms.kcu.ac/dashBoard/std"
_COURSE_LIST_URL = "https://lms.kcu.ac/atnlcSubj/list"
_LECT_ROOM_URL = "https://lms.kcu.ac/atnlcSubj/lectRoom"
_WEEK_LECT_API_URL = "https://lms.kcu.ac/common/lect/selectWeekLectInfo"

_DEFAULT_DURATION_SEC = 1800  # 30분 기본값 (API에서 재생시간 미제공)
_MAX_WEEKS = 15


class KCUProvider:
    def __init__(self, config: SchoolConfig):
        self._config = config
        self._base_url = config.base_url

    @property
    def name(self) -> str:
        return "kcu"

    @property
    def display_name(self) -> str:
        return "숭실사이버대"

    def get_credentials(self) -> tuple[str, str]:
        return (self._config.userid or "", self._config.password or "")

    # ── 로그인 ──────────────────────────────────────────────

    async def login(self, page: Page) -> None:
        """KCU 포탈 로그인 → LMS 대시보드 진입"""
        logger.info("KCU 포탈 로그인 시도...")
        await page.goto(_PORTAL_LOGIN_URL, wait_until="networkidle")

        # "학번" 탭 클릭
        try:
            tab_links = await page.query_selector_all("a")
            for link in tab_links:
                text = await link.inner_text()
                if "학번" in text:
                    await link.click()
                    await asyncio.sleep(1)
                    break
        except Exception as e:
            logger.warning("학번 탭 클릭 실패 (무시하고 진행): %s", e)

        # 학번/비밀번호 입력
        await page.wait_for_selector("input#userId", timeout=LOGIN_TIMEOUT_MS)

        userid, password = self.get_credentials()

        await page.fill("input#userId", "")
        await page.type("input#userId", userid, delay=50)

        # 비밀번호 필드 — 특이한 구조이므로 여러 셀렉터 시도
        pw_filled = False
        for pw_selector in ["input#userPw", 'input[placeholder="비밀번호"]']:
            try:
                pw_input = await page.query_selector(pw_selector)
                if pw_input:
                    await pw_input.click()
                    await pw_input.fill("")
                    await pw_input.type(password, delay=50)
                    pw_filled = True
                    break
            except Exception:
                continue

        if not pw_filled:
            raise LoginError("비밀번호 입력 필드를 찾을 수 없습니다")

        await asyncio.sleep(1)

        # 로그인 버튼 클릭
        login_clicked = False
        try:
            login_btn = await page.query_selector("button.btnMoveLectRoom")
            if login_btn:
                await login_btn.click()
                login_clicked = True
        except Exception:
            pass

        if not login_clicked:
            # 텍스트로 버튼 찾기
            try:
                buttons = await page.query_selector_all("button")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "학번 로그인" in text or "로그인" in text:
                        await btn.click()
                        login_clicked = True
                        break
            except Exception:
                pass

        if not login_clicked:
            # JS로 폼 제출 시도
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('button.btnMoveLectRoom')
                        || document.querySelector('button[type="submit"]');
                    if (btn) { btn.click(); return; }
                    const form = document.querySelector('form');
                    if (form) form.submit();
                }
            """)

        # 대시보드 리디렉션 대기
        for i in range(20):
            await asyncio.sleep(2)
            current_url = page.url
            if i % 5 == 0:
                logger.info("리디렉션 대기... (%s)", current_url[:60])

            if "lms.kcu.ac" in current_url and "dashBoard" in current_url:
                break
            if "lms.kcu.ac" in current_url and "login" not in current_url:
                break

        if "login" in page.url or "portal.kcu.ac" in page.url:
            raise LoginError("KCU 로그인 실패 — .env의 KCU_USERID/KCU_PASSWORD 확인 필요")

        logger.info("로그인 성공 — %s", page.url)

    # ── 과목 목록 ─────────────────────────────────────────

    async def get_courses(self, page: Page) -> list[Course]:
        """과목 목록 페이지에서 수강 과목 반환"""
        await page.goto(_COURSE_LIST_URL, wait_until="networkidle")
        await asyncio.sleep(2)

        courses: list[Course] = await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button.btnEntryLect');
                return Array.from(buttons).map(btn => {
                    const coseCd = btn.dataset.coseCd || '';
                    const shyr = btn.dataset.shyr || '';
                    const smstCd = btn.dataset.smstCd || '';
                    const dertCd = btn.dataset.dertCd || '';
                    const user = btn.dataset.user || '';

                    // 과목명: 같은 카드 내 strong 태그
                    const card = btn.closest('.card, [class*="card"], [class*="subject"], [class*="item"]')
                        || btn.parentElement?.parentElement?.parentElement;
                    let name = '';
                    if (card) {
                        const strong = card.querySelector('strong');
                        if (strong) name = strong.textContent.trim();
                    }

                    // courseId: 과목코드 + 메타데이터 (나중에 강의 목록 조회용)
                    const courseId = JSON.stringify({
                        coseCd, shyr, smstCd, dertCd, user
                    });

                    return { name, courseId, videoCount: 1 };
                }).filter(c => c.name);
            }
        """)

        if not courses:
            logger.info("수강 중인 과목이 없습니다")
            return courses

        logger.info("과목 %d개 발견", len(courses))
        for c in courses:
            logger.info("  - %s", c["name"])

        return courses

    # ── 강의 목록 ─────────────────────────────────────────

    async def _enter_lect_room(self, page: Page, course_meta: dict) -> str:
        """lectRoom에 POST로 진입하여 profId(empno) 추출"""
        form_data = {
            "shyr": course_meta["shyr"],
            "smstCd": course_meta["smstCd"],
            "dertCd": course_meta["dertCd"],
            "coseCd": course_meta["coseCd"],
            "weekNo": "01",
            "lectNo": "1",
            "menuCd": "04580",
            "prgmId": "LRN_LM_S_014",
            "menuGrpCd": "new_SSJU",
        }

        # JS로 form POST
        await page.evaluate(
            """
            (params) => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/atnlcSubj/lectRoom';
                for (const [key, val] of Object.entries(params)) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = key;
                    input.value = val;
                    form.appendChild(input);
                }
                document.body.appendChild(form);
                form.submit();
            }
            """,
            form_data,
        )

        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # hidden input에서 profId 추출
        prof_id = await page.evaluate("""
            () => {
                const el = document.querySelector('input[name="profId"]')
                    || document.querySelector('input#profId')
                    || document.querySelector('input[name="empno"]');
                return el ? el.value : '';
            }
        """)

        if not prof_id:
            logger.warning("profId를 찾을 수 없음 — hidden input 탐색 시도")
            prof_id = await page.evaluate("""
                () => {
                    const inputs = document.querySelectorAll('input[type="hidden"]');
                    for (const inp of inputs) {
                        if (inp.name.toLowerCase().includes('prof')
                            || inp.name.toLowerCase().includes('empno')) {
                            return inp.value;
                        }
                    }
                    return '';
                }
            """)

        logger.info("profId(empno): %s", prof_id or "(미확인)")
        return prof_id

    async def get_lectures(
        self, page: Page, course_id: str, course_name: str = ""
    ) -> list[Lecture]:
        """주차별 API를 호출하여 전체 강의 목록 반환"""
        import json

        course_meta = json.loads(course_id)
        cose_cd = course_meta["coseCd"]

        # 1. lectRoom 진입 → profId 확보
        prof_id = await self._enter_lect_room(page, course_meta)

        # 2. 주차별 강의 정보 API 호출
        lectures: list[Lecture] = []

        for week_no in range(1, _MAX_WEEKS + 1):
            week_str = f"{week_no:02d}"
            params = {
                "shyr": course_meta["shyr"],
                "smstCd": course_meta["smstCd"],
                "coseCd": cose_cd,
                "weekNo": week_str,
                "lectNo": "1",
                "empno": prof_id,
                "userAgent": "PC",
                "lectRmPrcsCd": "1",
                "userAuth": "S",
                "cntnTy": "W",
            }

            try:
                result = await page.evaluate(
                    """
                    async (params) => {
                        const body = new URLSearchParams(params).toString();
                        const resp = await fetch('/common/lect/selectWeekLectInfo', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                            body: body,
                        });
                        if (!resp.ok) return null;
                        return await resp.json();
                    }
                    """,
                    params,
                )
            except Exception as e:
                logger.debug("주차 %d API 호출 실패: %s", week_no, e)
                continue

            if not result or not result.get("weekLectInfoList"):
                # 빈 주차면 남은 주차도 없을 가능성 높음 — 하지만 건너뛰고 계속
                continue

            for item in result["weekLectInfoList"]:
                vdo_url = item.get("vdoUrl", "")
                if not vdo_url:
                    continue

                week_cnt = item.get("wkendCnt", week_no)
                lect_no = item.get("lectNo", 1)
                lect_title = item.get("lectTtlNm", "-")
                if lect_title == "-":
                    lect_title = f"{course_name}"

                title = f"{week_cnt}주 {lect_no}강 {lect_title}"

                rtprgs = int(item.get("rtprgsRpblty", "0"))
                is_completed = rtprgs >= 99

                # href에 강의 식별 메타데이터를 JSON으로 인코딩
                lect_meta = json.dumps(
                    {
                        "coseCd": cose_cd,
                        "shyr": course_meta["shyr"],
                        "smstCd": course_meta["smstCd"],
                        "dertCd": course_meta["dertCd"],
                        "weekNo": week_str,
                        "lectNo": str(lect_no),
                        "empno": prof_id,
                        "vdoUrl": vdo_url,
                    }
                )

                lectures.append(
                    Lecture(
                        title=title,
                        href=lect_meta,
                        isCompleted=is_completed,
                        durationSec=_DEFAULT_DURATION_SEC,
                        itemType="movie",
                        courseName=course_name,
                        startDate=None,
                        deadline=None,
                    )
                )

        unwatched = [lec for lec in lectures if not lec["isCompleted"]]
        completed = [lec for lec in lectures if lec["isCompleted"]]
        logger.info(
            "강의 %d개 (미수강 %d / 수강완료 %d)",
            len(lectures),
            len(unwatched),
            len(completed),
        )
        for lec in lectures:
            status = "V" if lec["isCompleted"] else " "
            m, s = divmod(lec["durationSec"], 60)
            logger.info("  %s %s (%d:%02d)", status, lec["title"], m, s)

        return lectures

    # ── iframe 헬퍼 ─────────────────────────────────────────

    def _find_player_frame(self, page: Page) -> Frame | None:
        """page.frames에서 mvapi.kcu.ac 플레이어 iframe 찾기"""
        for f in page.frames:
            if "mvapi.kcu.ac" in f.url:
                return f
        return None

    # ── 강의 재생/다운로드 ──────────────────────────────────

    async def _navigate_to_lect_room(self, page: Page, lect_meta: dict) -> None:
        """POST form으로 lectRoom 페이지에 진입"""
        form_data = {
            "shyr": lect_meta["shyr"],
            "smstCd": lect_meta["smstCd"],
            "dertCd": lect_meta["dertCd"],
            "coseCd": lect_meta["coseCd"],
            "weekNo": lect_meta["weekNo"],
            "lectNo": lect_meta["lectNo"],
            "menuCd": "04580",
            "prgmId": "LRN_LM_S_014",
            "menuGrpCd": "new_SSJU",
        }

        await page.evaluate(
            """
            (params) => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/atnlcSubj/lectRoom';
                for (const [key, val] of Object.entries(params)) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = key;
                    input.value = val;
                    form.appendChild(input);
                }
                document.body.appendChild(form);
                form.submit();
            }
            """,
            form_data,
        )

        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

    async def _wait_for_player_frame(self, page: Page) -> Frame | None:
        """cndIfram(플레이어 iframe) 로드 대기 후 Frame 반환"""
        try:
            await page.wait_for_selector(
                "iframe#cndIfram, iframe.cndIfram", timeout=IFRAME_TIMEOUT_MS
            )
        except Exception:
            logger.warning("플레이어 iframe 셀렉터 대기 타임아웃")

        await asyncio.sleep(3)

        player_frame = self._find_player_frame(page)
        if not player_frame:
            # iframe이 아직 로드되지 않았을 수 있음 — 재시도
            for _ in range(5):
                await asyncio.sleep(2)
                player_frame = self._find_player_frame(page)
                if player_frame:
                    break

        if player_frame:
            await player_frame.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

        return player_frame

    async def _capture_hls_url(self, page: Page, timeout_sec: int = 15) -> str | None:
        """네트워크 요청에서 HLS m3u8 URL 캡처"""
        captured: dict[str, str | None] = {"url": None}

        def on_request(request: Request):
            url = request.url
            if captured["url"] is None and ".m3u8" in url:
                captured["url"] = url

        page.on("request", on_request)

        for _ in range(timeout_sec * 10):
            if captured["url"]:
                break
            await asyncio.sleep(0.1)

        page.remove_listener("request", on_request)
        return captured["url"]

    async def _start_playback(self, player_frame: Frame) -> None:
        """플레이어 iframe 내에서 재생 시작"""
        try:
            await player_frame.evaluate("""
                () => {
                    const video = document.querySelector('video#video-player')
                        || document.querySelector('video');
                    if (video) {
                        video.play();
                        return true;
                    }
                    return false;
                }
            """)
            logger.info("재생 시작 (JS video.play)")
        except Exception:
            # 클릭으로 재생 시도
            try:
                play_btn = await player_frame.query_selector(
                    ".vjs-big-play-button, .vjs-play-control, button[title='Play']"
                )
                if play_btn:
                    await play_btn.click()
                    logger.info("재생 시작 (클릭)")
            except Exception as e:
                logger.warning("재생 시작 실패: %s", e)

    async def _monitor_playback(
        self, page: Page, player_frame: Frame, title: str, duration_sec: int
    ) -> bool:
        """재생 진행 모니터링. 수강 완료 시 True 반환."""
        from datetime import datetime

        start_time = datetime.now()
        timeout_sec = duration_sec + PLAYBACK_TIMEOUT_BUFFER_SEC
        last_log_time = 0.0

        while True:
            elapsed = (datetime.now() - start_time).total_seconds()

            progress = None
            # 먼저 player iframe에서 시도
            try:
                progress = await player_frame.evaluate("""
                    () => {
                        const video = document.querySelector('video#video-player')
                            || document.querySelector('video');
                        if (video && video.duration > 1) {
                            return {
                                currentTime: video.currentTime,
                                duration: video.duration,
                                paused: video.paused,
                                ended: video.ended,
                                rate: video.playbackRate
                            };
                        }
                        return null;
                    }
                """)
            except Exception:
                # iframe이 분리될 수 있음 — 메인 페이지에서 재시도
                player_frame = self._find_player_frame(page)
                if player_frame:
                    with contextlib.suppress(Exception):
                        progress = await player_frame.evaluate("""
                            () => {
                                const video = document.querySelector('video');
                                if (video && video.duration > 1) {
                                    return {
                                        currentTime: video.currentTime,
                                        duration: video.duration,
                                        paused: video.paused,
                                        ended: video.ended,
                                        rate: video.playbackRate
                                    };
                                }
                                return null;
                            }
                        """)

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
                    await asyncio.sleep(5)  # 출석 API 호출 여유
                    return True

                if progress["paused"] and progress["currentTime"] > 1:
                    logger.warning("일시정지 감지 (%.1f%%), 재개 시도...", pct)
                    with contextlib.suppress(Exception):
                        await player_frame.evaluate("""
                            () => {
                                const video = document.querySelector('video#video-player')
                                    || document.querySelector('video');
                                if (video && video.paused) video.play();
                            }
                        """)

            if elapsed > timeout_sec:
                logger.warning("타임아웃 (%.0fs). 다음 강의로 이동.", elapsed)
                return False

            await asyncio.sleep(5)

    async def process_lecture(self, page: Page, lecture: Lecture) -> ProcessResult:
        """강의 처리: 미수강이면 재생+출석, 수강완료면 다운로드만"""
        import json

        title = lecture["title"]
        duration_sec = lecture["durationSec"]
        course_name = lecture.get("courseName", "unknown")
        is_completed = lecture.get("isCompleted", False)
        lect_meta = json.loads(lecture["href"])

        m, s = divmod(duration_sec, 60)
        print(f"\n{'=' * 50}")
        if is_completed:
            logger.info("[DL] %s (수강완료 - 다운로드만)", title)
        else:
            logger.info("[PLAY] %s (%d:%02d)", title, m, s)
        print(f"{'=' * 50}")

        # 1. lectRoom에 POST로 진입
        await self._navigate_to_lect_room(page, lect_meta)

        # 2. HLS URL 캡처를 위한 리스너 등록 + 플레이어 iframe 대기
        hls_capture_task = asyncio.create_task(self._capture_hls_url(page, timeout_sec=30))

        player_frame = await self._wait_for_player_frame(page)
        if not player_frame:
            logger.error("플레이어 iframe을 찾을 수 없음")
            hls_capture_task.cancel()
            return {"attended": False, "download_only": False, "mp4": None, "txt": None}

        # 3. 재생 시작
        await self._start_playback(player_frame)
        await asyncio.sleep(3)

        # 4. HLS URL 캡처 대기
        hls_url = await hls_capture_task

        # HLS URL이 캡처 안됐으면 vdoUrl에서 추출 시도
        if not hls_url:
            vdo_url = lect_meta.get("vdoUrl", "")
            if vdo_url:
                logger.info("네트워크에서 HLS URL 미감지 — vdoUrl에서 추출 시도")
                # vdoUrl 페이지에서 m3u8 URL을 직접 추출할 수도 있음
                # 일단 로그만 남기고 진행
                logger.info("vdoUrl: %s", vdo_url[:80])

        # 수강완료 강의: 즉시 비디오 정지
        if is_completed:
            with contextlib.suppress(Exception):
                await player_frame.evaluate("""
                    () => {
                        const video = document.querySelector('video#video-player')
                            || document.querySelector('video');
                        if (video) video.pause();
                    }
                """)
                logger.info("비디오 정지 (수강완료 - URL 캡처 완료)")

        # 5. 다운로드+전사 시작
        transcript_task = None
        if hls_url:
            logger.info("HLS URL 캡처 완료: %s", hls_url[:80])
            transcript_task = asyncio.create_task(
                download_and_transcribe(
                    hls_url,
                    course_name,
                    title,
                    hls=True,
                )
            )
        else:
            logger.info("HLS URL 미감지 - 스크립트 추출 건너뜀")

        # 6. 미수강: 재생 진행 모니터링 (headed 브라우저에서 진도 보고 자동)
        attended = False
        if not is_completed:
            attended = await self._monitor_playback(page, player_frame, title, duration_sec)

        # 7. 다운로드/전사 완료 대기
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
