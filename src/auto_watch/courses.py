"""과목 및 강의 탐색"""

import asyncio
import logging

from playwright.async_api import Page

from .browser import get_tool_content_frame, login_if_needed
from .config import BASE_URL, MYPAGE_URL
from .types import Course, CourseStatus, Lecture, WeeklyItem

logger = logging.getLogger(__name__)


async def get_courses(page: Page) -> list[Course]:
    """마이페이지에서 모든 과목 목록 반환"""
    logger.info("마이페이지에서 과목 탐색 중...")
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


async def get_lectures(page: Page, course_id: str, course_name: str = "") -> list[Lecture]:
    """과목의 주차학습 페이지에서 강의 목록 반환 (미수강 + 수강완료)"""
    url = f"{BASE_URL}/courses/{course_id}/external_tools/71"
    logger.info("주차학습 페이지 로드 중... (course %s)", course_id)
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
            logger.info("전체 주차 펼침")
    except Exception:
        pass

    lectures = await frame.evaluate("""
        () => {
            const items = document.querySelectorAll('.xnmb-module_item-outer-wrapper');
            return Array.from(items).map(item => {
                const link = item.querySelector('a.xnmb-module_item-left-title');
                if (!link) return null;

                // 아이콘 클래스로 항목 타입 판별 (movie/readystream = 동영상)
                const iconEl = item.querySelector('i.xnmb-module_item-icon');
                const iconClasses = iconEl ? iconEl.className : '';
                const isVideo = iconClasses.includes('movie') || iconClasses.includes('readystream');
                const itemType = isVideo ? 'movie'
                    : iconClasses.includes('file') ? 'file'
                    : iconClasses.includes('assignment') ? 'assignment'
                    : 'other';

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

                return { title, href, isCompleted, durationSec, itemType, startDate: startDate?.toISOString() || null, deadline: deadline?.toISOString() || null };
            }).filter(item => {
                if (!item || !item.href) return false;
                // 동영상(movie) 타입만 통과
                if (item.itemType !== 'movie') return false;
                if (item.durationSec <= 0) return false;
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


async def get_items_by_week(
    page: Page, course_id: str, course_name: str = ""
) -> CourseStatus:
    """과목의 주차학습 페이지에서 모든 아이템을 주차별로 그룹핑하여 반환.

    get_lectures()와 달리:
    - movie 필터 없음 — assignment, file 등 모든 itemType 포함
    - 모듈 컨테이너 단위로 순회하여 주차(weekNumber) 부착
    - 시작일 미래 필터 없음 — 전체 주차 데이터 수집
    """
    url = f"{BASE_URL}/courses/{course_id}/external_tools/71"
    logger.info("[sync] 주차학습 페이지 로드 중... (course %s)", course_id)
    await page.goto(url, wait_until="networkidle")
    await login_if_needed(page)
    if "external_tools/71" not in page.url:
        await page.goto(url, wait_until="networkidle")

    frame = await get_tool_content_frame(page)

    # "모두 펼치기" 클릭
    try:
        expand_btn = await frame.query_selector('text="모두 펼치기"')
        if expand_btn:
            await expand_btn.click()
            await asyncio.sleep(2)
            logger.info("[sync] 전체 주차 펼침")
    except Exception:
        pass

    items_raw: list[dict] = await frame.evaluate("""
        () => {
            const results = [];
            // DOM 구조: xnmb-module-list > div(주차컨테이너) > [헤더, 아이템컨테이너]
            // 헤더(.xnmb-module-outer-wrapper)와 아이템은 형제 관계
            const moduleList = document.querySelector('.xnmb-module-list');
            if (!moduleList) return results;

            // xnmb-module-list의 직접 자식 중 주차 컨테이너 순회
            for (const weekContainer of moduleList.children) {
                // 헤더 div 건너뛰기
                if (weekContainer.classList?.contains('xnmb-header')) continue;

                // 주차 헤더에서 주차 번호 추출
                const headerEl = weekContainer.querySelector('.xnmb-module-title');
                const headerText = headerEl?.textContent || '';
                const weekMatch = headerText.match(/(\\d+)\\s*주차/);
                const weekNumber = weekMatch ? parseInt(weekMatch[1]) : 0;

                // 아이템 순회 (헤더의 형제 div 안에 위치)
                const itemEls = weekContainer.querySelectorAll(
                    '.xnmb-module_item-outer-wrapper'
                );
                for (const item of itemEls) {
                    const link = item.querySelector('a.xnmb-module_item-left-title');
                    if (!link) continue;

                    // 아이콘으로 타입 판별
                    const iconEl = item.querySelector('i.xnmb-module_item-icon');
                    const iconClasses = iconEl ? iconEl.className : '';
                    const isVideo = iconClasses.includes('movie') || iconClasses.includes('readystream');
                    const itemType = isVideo ? 'movie'
                        : iconClasses.includes('file') ? 'file'
                        : iconClasses.includes('assignment') ? 'assignment'
                        : 'other';

                    const href = link.href || '';
                    const title = link.textContent?.trim() || '';

                    // 텍스트 노드 수집 (innerText 합치기 버그 방지)
                    const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
                    const textParts = [];
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (t) textParts.push(t);
                    }
                    const text = textParts.join(' ');

                    const isCompleted = text.includes('완료') && !text.includes('미완료');

                    // 시작일
                    const startMatch = text.match(/시작\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                    let startDate = null;
                    if (startMatch) {
                        const now = new Date();
                        startDate = new Date(now.getFullYear(), parseInt(startMatch[1]) - 1, parseInt(startMatch[2]));
                    }

                    // 마감일
                    const endMatch = text.match(/(?:마감|종료)\\s+(\\d{1,2})월\\s+(\\d{1,2})일/);
                    let deadline = null;
                    if (endMatch) {
                        const now = new Date();
                        deadline = new Date(now.getFullYear(), parseInt(endMatch[1]) - 1, parseInt(endMatch[2]));
                    }

                    // 재생 시간 (동영상만 — file/assignment는 MM:SS가 마감시간)
                    let durationSec = 0;
                    if (isVideo) {
                        const allTimes = [...text.matchAll(/(\\d+):(\\d{2})(?!\\d)/g)];
                        for (const m of allTimes) {
                            const mins = parseInt(m[1]);
                            const secs = parseInt(m[2]);
                            if (secs < 60 && mins >= 1 && mins <= 180) {
                                durationSec = mins * 60 + secs;
                            }
                        }
                    }

                    results.push({
                        title, href, isCompleted, durationSec, itemType,
                        startDate: startDate?.toISOString() || null,
                        deadline: deadline?.toISOString() || null,
                        weekNumber,
                    });
                }
            }
            return results;
        }
    """)

    # 주차별 그룹핑
    weeks: dict[int, list[WeeklyItem]] = {}
    for item in items_raw:
        item["courseName"] = course_name
        wn = item["weekNumber"]
        if wn not in weeks:
            weeks[wn] = []
        weeks[wn].append(item)

    total_items = len(items_raw)
    total_weeks = len([w for w in weeks if w > 0])
    logger.info(
        "[sync] %s: %d개 아이템, %d주차", course_name or course_id, total_items, total_weeks
    )

    return CourseStatus(
        courseName=course_name,
        courseId=course_id,
        weeks=weeks,
    )
