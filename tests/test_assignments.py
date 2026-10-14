"""과제·공지 정리 함수 테스트 — 입력은 2026-10-14 실제 응답 모양"""

from src.auto_watch import assignments as a


def test_canvas_planner_keeps_only_gradable_and_converts_to_kst():
    items = [
        {
            "plannable_type": "assignment",
            "plannable": {"title": "Q5"},
            "plannable_date": "2026-10-12T14:59:59Z",
            "context_name": "선형대수 (대면+사전녹화) (2150151201)",
            "html_url": "/courses/1/assignments/2",
            "submissions": {"submitted": False, "missing": True},
        },
        {
            "plannable_type": "announcement",
            "plannable": {"title": "개천절 공지"},
            "plannable_date": "2026-10-02T14:53:22Z",
            "context_name": "고급웹프로그래밍 (대면+사전녹화) (2150151301)",
            "submissions": False,
        },
        {
            "plannable_type": "quiz",
            "plannable": {"title": "1주차 퀴즈"},
            "plannable_date": "2026-09-17T14:59:00Z",
            "context_name": "비전채플 (2150101510)",
            "html_url": "/courses/3/quizzes/4",
            "submissions": {"submitted": True, "missing": False},
        },
    ]

    got = a.from_canvas_planner(items, "https://canvas.ssu.ac.kr")

    assert [(x["course"], x["kind"], x["title"], x["due"]) for x in got] == [
        ("선형대수", "과제", "Q5", "2026-10-12T23:59"),
        ("비전채플", "퀴즈", "1주차 퀴즈", "2026-09-17T23:59"),
    ]
    assert got[0]["missing"] and got[0]["submitted"] is False
    assert got[1]["url"] == "https://canvas.ssu.ac.kr/courses/3/quizzes/4"


def test_kcu_eval_rows_skip_attendance_total_and_participation():
    rows = [
        ["시험", "1", "", "", "2026-12-15 19:00", "2026-12-15 19:50", "50분", "", "", "45%"],
        ["퀴즈", "1", "", "", "", "", "-", "", "", "30%"],
        ["출석", "-", "-", "-", "-", "-", "-", "", "-", "20%"],
        ["기타(참여도)", "1", "", "", "", "", "-", "", "", "5%"],
        ["합계", "100%"],
    ]

    got = a.from_kcu_eval_rows("AI를 이용한 미디어 방송", rows, "u")

    assert [(x["kind"], x["title"], x["start"], x["due"]) for x in got] == [
        ("시험", "시험 1차", "2026-12-15T19:00", "2026-12-15T19:50"),
        ("퀴즈", "퀴즈 1차", None, None),
    ]
    assert got[0]["submitted"] is None


def test_kcu_submitted_reads_status_text():
    assert a._kcu_submitted("응시완료") is True
    assert a._kcu_submitted("미응시") is False
    assert a._kcu_submitted("") is None


def test_kcu_attendance_from_card_text():
    card = "과목진도율\n\n43%\n\n과목출석현황\n\n양호\n\n지각: 0\n미수강: 1\n강의"
    assert a.kcu_attendance(card) == {"late": 0, "unwatched": 1}


def test_kcu_notice_body_cuts_header_and_footer():
    text = (
        "과목공지\n퀴즈 실시 사전 안내\n"
        "게시글 번호: 5 신종홍 (교수) 2026-10-12 19:52 조회수: 94\n\n"
        "우리의 수업이 벌써 7주차가 되었네요.\n\n\n\n"
        "* 퀴즈 기간: 10월 24일(토) 낮 12시 ~10월 26일(월) 18시\n\n\n목록\n이전글\n"
    )
    assert a.kcu_notice_body(text) == (
        "우리의 수업이 벌써 7주차가 되었네요.\n"
        "* 퀴즈 기간: 10월 24일(토) 낮 12시 ~10월 26일(월) 18시"
    )


def test_canvas_announcements_strip_html():
    items = [
        {
            "id": 7,
            "title": "시험 일정",
            "message": "<p>중간고사&nbsp;10/21</p><p>범위 1~4장</p>",
            "posted_at": "2026-09-27T12:09:28Z",
            "html_url": "https://canvas.ssu.ac.kr/courses/1/discussion_topics/7",
            "context_code": "course_1",
        }
    ]

    got = a.from_canvas_announcements(items, {"course_1": "선형대수 (대면+사전녹화) (2150151201)"})

    assert got[0]["course"] == "선형대수"
    assert got[0]["body"] == "중간고사 10/21\n범위 1~4장"
    assert got[0]["posted"] == "2026-09-27T21:09"
