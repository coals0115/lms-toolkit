"""미수강 과목 및 강의 탐색"""

import asyncio

from playwright.async_api import Page

from .config import BASE_URL, MYPAGE_URL
from .browser import login_if_needed, get_tool_content_frame


async def get_unwatched_courses(page: Page) -> list[dict]:
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


async def get_unwatched_lectures(page: Page, course_id: str, course_name: str = "") -> list[dict]:
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
