"""CLI 사용자 인터페이스 및 유틸리티 함수"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime


def select_mode() -> str:
    """시작 시 모드 선택. 'watch' 또는 'download' 반환."""
    print("\n모드를 선택하세요:")
    print("  [1] 자동 수강 — 미수강 동영상 재생 + 다운로드/전사")
    print("  [2] 다운로드  — 과목 선택 → 강의 다운로드/전사만")

    while True:
        try:
            choice = input("\n번호 (1/2): ").strip()
        except EOFError:
            return "watch"

        if choice == "1":
            return "watch"
        if choice == "2":
            return "download"
        print("  1 또는 2를 입력하세요.")


def select_courses(courses: list[dict]) -> list[dict]:
    """과목 목록 표시 → 사용자 선택 (주차학습 페이지 미진입)"""
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  과목 {len(courses)}개:")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    for i, c in enumerate(courses, 1):
        if c["videoCount"] > 0:
            print(f"  [{i}] {c['name']} (미수강 {c['videoCount']}개)")
        else:
            print(f"  [{i}] {c['name']} (수강 완료)")

    print()

    while True:
        try:
            choice = input("번호 / all / q: ").strip().lower()
        except EOFError:
            return []

        if choice == "q":
            return []

        if choice == "all":
            return courses

        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(courses):
                    selected.append(courses[idx - 1])
                else:
                    print(f"  [WARN] {idx}번은 범위 밖 (1~{len(courses)})")
            if selected:
                return selected
            print("  유효한 번호를 입력하세요.")
        except ValueError:
            print("  숫자, all, q 중 하나를 입력하세요.")


def _format_duration(total_sec: int) -> str:
    """초를 H:MM:SS 또는 M:SS 형식으로 변환"""
    total_m, total_s = divmod(total_sec, 60)
    total_h, total_m = divmod(total_m, 60)
    if total_h:
        return f"{total_h}:{total_m:02d}:{total_s:02d}"
    return f"{total_m}:{total_s:02d}"


def _group_by_course(lectures: list[dict]) -> OrderedDict:
    """강의를 과목별로 그룹핑 (입력 순서 유지)"""
    groups = OrderedDict()  # type: OrderedDict[str, list[dict]]
    for lec in lectures:
        cn = lec.get("courseName", "unknown")
        if cn not in groups:
            groups[cn] = []
        groups[cn].append(lec)
    return groups


def _display_lectures(all_lectures: list[dict], expanded: bool) -> list[dict]:
    """강의 목록 표시. 번호가 매겨진 강의 리스트(visible)를 반환."""
    unwatched = [l for l in all_lectures if not l.get("isCompleted")]
    completed = [l for l in all_lectures if l.get("isCompleted")]
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  전체 강의 {len(all_lectures)}개 (미수강 {len(unwatched)} / 수강완료 {len(completed)}):")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    groups = _group_by_course(all_lectures)
    visible = []  # 번호가 매겨진 강의 (선택 가능)
    total_sec = 0
    unwatched_sec = 0

    for course_name, items in groups.items():
        course_unwatched = [l for l in items if not l.get("isCompleted")]
        course_completed = [l for l in items if l.get("isCompleted")]

        # 과목 헤더
        if expanded:
            if course_unwatched:
                header = f"{course_name} (미수강 {len(course_unwatched)} / 전체 {len(items)})"
            else:
                header = f"{course_name} (전체 {len(items)})"
        else:
            if course_unwatched:
                header = f"{course_name} (미수강 {len(course_unwatched)})"
            else:
                header = course_name
        print(f"\n  ── {header} ──")

        for lec in items:
            is_done = lec.get("isCompleted", False)
            m, s = divmod(lec["durationSec"], 60)
            total_sec += lec["durationSec"]
            if not is_done:
                unwatched_sec += lec["durationSec"]

            if is_done and not expanded:
                continue  # 접힌 상태: 수강완료 개별 표시 안 함

            visible.append(lec)
            idx = len(visible)
            status = "V" if is_done else " "

            d_day = ""
            if not is_done and lec.get("deadline"):
                deadline_dt = datetime.fromisoformat(
                    lec["deadline"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
                days_left = (deadline_dt - today).days
                d_day = f" D-{days_left}" if days_left >= 0 else f" D+{abs(days_left)}"

            print(f"  [{idx:2d}] {status}  {lec['title']} ({m}:{s:02d}){d_day}")

        # 접힌 상태: 수강완료 요약
        if not expanded and course_completed:
            print(f"  [ +{len(course_completed)} 수강완료 ]")

    # 재생시간
    if expanded:
        print(f"\n  총 재생시간: {_format_duration(total_sec)} (미수강: {_format_duration(unwatched_sec)})")
    else:
        print(f"\n  총 재생시간: {_format_duration(unwatched_sec)} (미수강)")

    # 미수강 0개 안내
    if not unwatched:
        print("\n  미수강 강의가 없습니다. 수강완료 강의를 다운로드하려면 'e'를 입력하세요.")

    print()
    return visible


def select_lectures(all_lectures: list[dict], download_mode: bool = False) -> list[dict]:
    """강의 목록 표시 (접기/펼치기) + 사용자 선택 → 선택된 강의 리스트 반환"""
    if not all_lectures:
        return []

    unwatched = [l for l in all_lectures if not l.get("isCompleted")]
    expanded = download_mode
    visible = _display_lectures(all_lectures, expanded=expanded)

    while True:
        try:
            if download_mode:
                prompt = "번호 / all / b(이전) / q: "
            elif expanded:
                prompt = "번호 / all / q: "
            else:
                prompt = "번호 / all / q / e(펼치기): "
            choice = input(prompt).strip().lower()
        except EOFError:
            return []

        if choice == "q":
            return []

        if choice == "b" and download_mode:
            return "back"

        if choice == "all":
            if download_mode:
                return all_lectures
            if unwatched:
                return unwatched
            print("  미수강 강의가 없습니다. 'e'로 수강완료 강의를 펼쳐보세요.")
            continue

        if choice == "e" and not expanded and not download_mode:
            expanded = True
            visible = _display_lectures(all_lectures, expanded=True)
            continue

        # 번호 파싱
        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(visible):
                    selected.append(visible[idx - 1])
                else:
                    print(f"  [WARN] {idx}번은 범위 밖 (1~{len(visible)})")
            if selected:
                return selected
            print("  유효한 번호를 입력하세요.")
        except ValueError:
            valid_cmds = "숫자, all, q, e" if not expanded else "숫자, all, q"
            print(f"  {valid_cmds} 중 하나를 입력하세요.")


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
