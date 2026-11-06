"""과제·퀴즈·시험 목록을 학교 구분 없이 같은 모양으로 맞춘다"""

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .types import Assignment, Notice

KST = timezone(timedelta(hours=9))

CANVAS_KINDS = {"assignment": "과제", "quiz": "퀴즈", "discussion_topic": "토론"}

_KCU_SKIP = {"출석", "합계"}


def _canvas_due(value: str | None) -> str | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(KST).strftime("%Y-%m-%dT%H:%M")


def from_canvas_planner(items: Iterable[Mapping[str, Any]], base_url: str) -> list[Assignment]:
    """Canvas /api/v1/planner/items 응답 → Assignment"""
    out: list[Assignment] = []
    for item in items:
        kind = CANVAS_KINDS.get(item.get("plannable_type", ""))
        if not kind:
            continue
        plannable = item.get("plannable") or {}
        sub = item.get("submissions") or {}
        out.append(
            Assignment(
                school="ssu",
                course=_canvas_course(item.get("context_name") or ""),
                kind=kind,
                title=(plannable.get("title") or plannable.get("name") or "").strip(),
                start=None,
                due=_canvas_due(item.get("plannable_date")),
                submitted=bool(sub.get("submitted")) if sub else None,
                missing=bool(sub.get("missing")) if sub else False,
                status="",
                url=base_url + (item.get("html_url") or ""),
            )
        )
    return out


def from_canvas_undated(
    items: Iterable[Mapping[str, Any]], course_name: str, base_url: str, now: datetime
) -> list[Assignment]:
    """Canvas /courses/:id/assignments?bucket=undated 응답 → Assignment

    planner 는 마감일 없는 과제를 안 준다.
    교수가 마감을 수업 시간에만 말하는 과제(피지컬미디어 실습)를 놓치지 않게,
    열린(unlock_at 이 지난) 것만 날짜 없이 넣는다.
    """
    out: list[Assignment] = []
    for item in items:
        if item.get("due_at") or not item.get("published", True):
            continue
        unlock = item.get("unlock_at")
        if unlock and datetime.fromisoformat(unlock.replace("Z", "+00:00")) > now:
            continue
        sub = item.get("submission") or {}
        if sub.get("workflow_state") in ("submitted", "graded", "pending_review"):
            continue  # 낸 것은 알릴 게 없다
        out.append(
            Assignment(
                school="ssu",
                course=_canvas_course(course_name),
                kind="과제",
                title=(item.get("name") or "").strip(),
                start=_canvas_due(unlock),
                due=None,
                submitted=False if sub else None,
                missing=False,
                status="",
                url=item.get("html_url") or base_url,
            )
        )
    return out


def _canvas_course(name: str) -> str:
    # "선형대수 (대면+사전녹화) (2150151201)" → "선형대수"
    return re.sub(r"\s*\([^)]*\)", "", name).strip() or name


def _kcu_dt(value: str) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", value.strip())
    return f"{m.group(1)}T{m.group(2)}" if m else None


def from_kcu_eval_rows(course: str, rows: Iterable[list[str]], url: str) -> list[Assignment]:
    """KCU 학습평가응시 표의 줄(셀 텍스트 목록) → Assignment

    열: 평가항목, 차수, 적용주차, 제목, 시작일시, 마감일시, 제한시간, 응시, 응시현황, 적용비율
    """
    out: list[Assignment] = []
    for cells in rows:
        cells = [c.strip() for c in cells]
        if len(cells) < 9 or not cells[0] or cells[0] in _KCU_SKIP:
            continue
        kind, seq, week, title, start, end, _limit, _btn, status = cells[:9]
        if kind.startswith("기타"):
            continue
        label = title or f"{kind} {seq}차" + (f" ({week}주)" if week and week != "-" else "")
        out.append(
            Assignment(
                school="kcu",
                course=course,
                kind=kind,
                title=label,
                start=_kcu_dt(start),
                due=_kcu_dt(end),
                submitted=_kcu_submitted(status),
                missing=False,
                status=status,
                url=url,
            )
        )
    return out


def _kcu_submitted(status: str) -> bool | None:
    if not status or status == "-":
        return None
    if "미" in status:
        return False
    return any(k in status for k in ("완료", "응시", "제출"))


def kcu_attendance(card_text: str) -> dict[str, int]:
    """대시보드 과목 카드 텍스트에서 지각·미수강 수"""
    result = {}
    for key, label in (("late", "지각"), ("unwatched", "미수강")):
        m = re.search(rf"{label}\s*:\s*(\d+)", card_text)
        if m:
            result[key] = int(m.group(1))
    return result


def html_to_text(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    for a, b in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
        text = text.replace(a, b)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def from_canvas_announcements(
    items: Iterable[Mapping[str, Any]], course_names: Mapping[str, str]
) -> list[Notice]:
    """Canvas /api/v1/announcements 응답 → Notice"""
    return [
        Notice(
            school="ssu",
            course=_canvas_course(course_names.get(item.get("context_code", ""), "")),
            id=str(item["id"]),
            title=(item.get("title") or "").strip(),
            posted=_canvas_due(item.get("posted_at") or item.get("created_at")),
            body=html_to_text(item.get("message") or ""),
            url=item.get("html_url") or "",
        )
        for item in items
    ]


def kcu_notice_body(page_text: str) -> str:
    """KCU 과목공지 상세 화면 텍스트에서 본문만 — 「게시글 번호」 줄 다음부터 「목록」 앞까지"""
    m = re.search(r"게시글 번호:[^\n]*\n(.*?)\n목록\n", page_text, re.S)
    body = m.group(1) if m else page_text
    return re.sub(r"\n\s*\n+", "\n", body).strip()
