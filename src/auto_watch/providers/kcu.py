"""숭실사이버대 KCU LMS Provider"""

import asyncio
import contextlib
import logging

from playwright.async_api import Frame, Page, Request

from ..config import (
    IFRAME_TIMEOUT_MS,
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

        try:
            # domcontentloaded로 빠르게 로드 후 즉시 학번 탭 클릭
            # (networkidle까지 기다리면 기본 탭(인증서)의 JS가 팝업을 열어버림)
            await page.goto(_PORTAL_LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # 데스크톱: 모든 로그인 방식이 컬럼으로 동시에 보임
            # 모바일: 탭 UI로 전환됨
            # → "학번 로그인" 그룹으로 스코프를 좁혀서 입력
            login_section = page.get_by_role("group", name="학번 로그인")
            await login_section.wait_for(state="visible", timeout=10000)
            logger.info("학번 로그인 섹션 감지")

            userid, password = self.get_credentials()

            await login_section.get_by_placeholder("학번").fill(userid)
            await login_section.get_by_placeholder("비밀번호").fill(password)
            await asyncio.sleep(1)
            logger.info("학번/비밀번호 입력 완료")

            # 로그인 버튼 클릭
            await login_section.get_by_role("button", name="학번 로그인").click()
            logger.info("로그인 버튼 클릭 완료")
        except LoginError:
            raise
        except Exception as e:
            raise LoginError(f"KCU 로그인 중 오류: {e}") from e

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

    async def _get_available_weeks(self, page: Page) -> list[int]:
        """lectRoom 주차목록에서 수강 가능한 주차 번호만 반환.

        주차 사이드바는 swiper 슬라이드 구조:
        div.swiper-slide.weekNo 안에 <p> 태그로 "N주." 제목과
        "미수강"/"강의 시작전" 등 상태 텍스트가 포함됨.
        """
        weeks = await page.evaluate("""
            () => {
                const result = [];
                const slides = document.querySelectorAll('.swiper-slide.weekNo');
                for (const slide of slides) {
                    const text = slide.textContent || '';
                    const weekMatch = text.match(/(\\d+)주\\./);
                    if (!weekMatch) continue;
                    const weekNo = parseInt(weekMatch[1]);
                    // "강의 시작전"이 포함되어 있으면 미개설
                    if (text.includes('강의 시작전') || text.includes('강의시작전')) {
                        continue;
                    }
                    result.push(weekNo);
                }
                return result;
            }
        """)

        if weeks:
            logger.info("수강 가능 주차: %s", weeks)
        else:
            logger.warning("주차 목록 파싱 실패 — 전체 주차 시도")

        return weeks

    async def get_lectures(
        self, page: Page, course_id: str, course_name: str = ""
    ) -> list[Lecture]:
        """주차별 API를 호출하여 전체 강의 목록 반환"""
        import json

        course_meta = json.loads(course_id)
        cose_cd = course_meta["coseCd"]

        # 1. lectRoom 진입 → profId 확보
        prof_id = await self._enter_lect_room(page, course_meta)

        # 2. 수강 가능한 주차만 파싱
        available_weeks = await self._get_available_weeks(page)
        target_weeks = available_weeks if available_weeks else range(1, _MAX_WEEKS + 1)

        # 3. 주차별 강의 정보 API 호출
        lectures: list[Lecture] = []

        for week_no in target_weeks:
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
                if lect_title in ("-", ""):
                    title = f"{week_cnt}주 {lect_no}강"
                else:
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
        logger.info("강의 페이지 로드 중...")
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
        logger.info("강의 페이지 로드 완료")

    async def _wait_for_player_frame(self, page: Page) -> Frame | None:
        """cndIfram(플레이어 iframe) 로드 대기 후 Frame 반환"""
        logger.info("플레이어 로드 중...")
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
            for attempt in range(5):
                await asyncio.sleep(2)
                player_frame = self._find_player_frame(page)
                if player_frame:
                    break
                logger.info("플레이어 재시도 %d/5", attempt + 1)

        if player_frame:
            await player_frame.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)
            logger.info("플레이어 로드 완료")

        return player_frame

    async def _capture_stream_url(self, page: Page, timeout_sec: int = 15) -> str | None:
        """네트워크 요청에서 스트리밍 URL 캡처 (m3u8/mp4/ts)"""
        captured: dict[str, str | None] = {"url": None}

        def on_request(request: Request):
            if captured["url"] is not None:
                return
            url = request.url
            # HLS manifest 또는 직접 비디오 파일
            if ".m3u8" in url or ("/mp4/" in url and ".mp4" in url):
                captured["url"] = url

        page.on("request", on_request)

        for _ in range(timeout_sec * 10):
            if captured["url"]:
                break
            await asyncio.sleep(0.1)

        page.remove_listener("request", on_request)
        return captured["url"]

    async def _extract_video_src(self, player_frame: Frame) -> str | None:
        """플레이어 iframe 내 video 요소에서 직접 소스 URL 추출"""
        try:
            src = await player_frame.evaluate("""
                () => {
                    const video = document.querySelector('video#video-player')
                        || document.querySelector('video');
                    if (!video) return null;
                    // currentSrc가 가장 정확 (실제 로드된 소스)
                    if (video.currentSrc) return video.currentSrc;
                    if (video.src) return video.src;
                    // <source> 태그 확인
                    const source = video.querySelector('source');
                    if (source && source.src) return source.src;
                    return null;
                }
            """)
            if src and not src.startswith("blob:"):
                logger.info("video 요소에서 URL 추출 완료")
                return src
        except Exception as e:
            logger.debug("video src 추출 실패: %s", e)
        return None

    async def _start_playback(self, player_frame: Frame) -> None:
        """플레이어 iframe 내에서 재생 시작 + 2배속 설정"""
        try:
            await player_frame.evaluate("""
                () => {
                    const video = document.querySelector('video#video-player')
                        || document.querySelector('video');
                    if (video) {
                        video.play();
                        video.playbackRate = 2;
                        return true;
                    }
                    return false;
                }
            """)
            logger.info("재생 시작 (2x 배속)")
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
                                if (video && video.paused) {
                                    video.play();
                                    video.playbackRate = 2;
                                }
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

        # 2. 스트림 URL 캡처를 위한 리스너 등록 + 플레이어 iframe 대기
        stream_capture_task = asyncio.create_task(
            self._capture_stream_url(page, timeout_sec=30)
        )

        player_frame = await self._wait_for_player_frame(page)
        if not player_frame:
            logger.error("플레이어 iframe을 찾을 수 없음")
            stream_capture_task.cancel()
            return {"attended": False, "download_only": False, "mp4": None, "txt": None}

        # 3. 재생 시작
        await self._start_playback(player_frame)
        await asyncio.sleep(3)

        # 4. 스트림 URL 캡처 대기
        stream_url = await stream_capture_task

        # 네트워크에서 못 잡으면 player frame에서 직접 추출
        if not stream_url:
            logger.info("네트워크에서 스트림 URL 미감지 — player frame에서 추출 시도")
            stream_url = await self._extract_video_src(player_frame)

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
        if stream_url:
            is_hls = ".m3u8" in stream_url
            logger.info(
                "%s URL 캡처 완료: %s",
                "HLS" if is_hls else "MP4",
                stream_url[:80],
            )
            transcript_task = asyncio.create_task(
                download_and_transcribe(
                    stream_url,
                    course_name,
                    title,
                    hls=is_hls,
                )
            )
        else:
            logger.info("스트림 URL 미감지 - 스크립트 추출 건너뜀")

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
