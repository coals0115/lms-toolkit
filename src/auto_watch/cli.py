"""CLI 사용자 인터페이스 및 유틸리티 함수"""

import re
from collections import OrderedDict
from datetime import datetime

from .config import SCHOOL_CONFIGS


def select_school() -> str:
    """학교 선택 메뉴. 계정이 하나만 설정되어 있으면 자동 선택."""
    available = [c for c in SCHOOL_CONFIGS.values() if c.userid and c.password]

    if not available:
        # 계정 없어도 일단 선택하게 함 (이후 로그인에서 에러 처리)
        available = list(SCHOOL_CONFIGS.values())

    if len(available) == 1:
        return available[0].name

    print("\n학교를 선택하세요:")
    for i, config in enumerate(available, 1):
        print(f"  [{i}] {config.display_name}")
    print("  [q] 종료")

    while True:
        try:
            choice = input("\n번호: ").strip().lower()
        except EOFError:
            return available[0].name

        if choice == "q":
            raise SystemExit(0)

        try:
            idx = int(choice)
            if 1 <= idx <= len(available):
                return available[idx - 1].name
        except ValueError:
            pass
        print(f"  1~{len(available)} 또는 q를 입력하세요.")


def select_mode(plugins=None) -> str:
    """시작 시 모드 선택. 내장 모드 + 플러그인 모드를 동적으로 표시."""
    builtin = [
        ("watch", "자동 수강 — 미수강 동영상 재생 + 다운로드/전사"),
        ("download", "다운로드  — 과목 선택 → 강의 다운로드/전사만"),
    ]

    menu_items = []  # (key, mode_id, label)
    for i, (mode_id, label) in enumerate(builtin, 1):
        menu_items.append((str(i), mode_id, label))

    if plugins:
        for plugin in plugins:
            idx = len(menu_items) + 1
            menu_items.append((str(idx), plugin.name, plugin.menu_entry.label))

    print("\n모드를 선택하세요:")
    for key, _, label in menu_items:
        print(f"  [{key}] {label}")
    print("  [q] 종료")

    key_to_mode = {key: mode_id for key, mode_id, _ in menu_items}
    valid_keys = "/".join(k for k, _, _ in menu_items)

    while True:
        try:
            choice = input(f"\n번호 ({valid_keys}) / q(종료): ").strip().lower()
        except EOFError:
            return "quit"

        if choice in key_to_mode:
            return key_to_mode[choice]
        if choice == "q":
            return "quit"
        print(f"  {valid_keys}, 또는 q를 입력하세요.")


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
            choice = input("번호 / all(전체) / b(이전) / q(종료): ").strip().lower()
        except EOFError:
            return []

        if choice == "q":
            return []

        if choice == "b":
            return "back"

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
            print("  숫자, all, b, q 중 하나를 입력하세요.")


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
    unwatched = [lec for lec in all_lectures if not lec.get("isCompleted")]
    completed = [lec for lec in all_lectures if lec.get("isCompleted")]
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(
        f"  전체 강의 {len(all_lectures)}개 (미수강 {len(unwatched)} / 수강완료 {len(completed)}):"
    )
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    groups = _group_by_course(all_lectures)
    visible = []  # 번호가 매겨진 강의 (선택 가능)
    total_sec = 0
    unwatched_sec = 0

    for course_name, items in groups.items():
        course_unwatched = [it for it in items if not it.get("isCompleted")]
        course_completed = [it for it in items if it.get("isCompleted")]

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
        total_fmt = _format_duration(total_sec)
        unwatched_fmt = _format_duration(unwatched_sec)
        print(f"\n  총 재생시간: {total_fmt} (미수강: {unwatched_fmt})")
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

    unwatched = [lec for lec in all_lectures if not lec.get("isCompleted")]
    expanded = download_mode
    visible = _display_lectures(all_lectures, expanded=expanded)

    while True:
        try:
            if expanded or download_mode:
                prompt = "번호 / all(전체) / b(이전) / q(종료): "
            else:
                prompt = "번호 / all(전체) / b(이전) / q(종료) / e(펼치기): "
            choice = input(prompt).strip().lower()
        except EOFError:
            return []

        if choice == "q":
            return []

        if choice == "b":
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
            valid_cmds = "숫자, all, b, q, e" if not expanded else "숫자, all, b, q"
            print(f"  {valid_cmds} 중 하나를 입력하세요.")


def _safe_filename(name: str) -> str:
    """파일시스템에 안전한 파일명으로 변환"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()
