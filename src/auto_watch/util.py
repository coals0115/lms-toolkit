"""범용 헬퍼"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .config import (
    PLAYBACK_ADVANCE_EPSILON_SEC,
    PLAYBACK_MAX_DURATION_MULTIPLIER,
    PLAYBACK_STALL_TIMEOUT_SEC,
    PLAYBACK_TIMEOUT_BUFFER_SEC,
)


def format_duration(total_sec: int | float) -> str:
    """초를 H:MM:SS (1시간 이상) 또는 M:SS 형식으로 변환.

    float 입력(예: HTMLVideoElement.duration)을 허용하며 내부에서 int로 절삭.
    """
    total_sec = int(total_sec)
    total_m, s = divmod(total_sec, 60)
    h, m = divmod(total_m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class PlaybackWatchdog:
    """재생이 실제로 진행 중인지 감시.

    경과 시간이 아니라 재생 위치가 늘고 있는지로 판단하므로, 버퍼링으로
    다소 늦어져도 진행만 하고 있으면 강의를 포기하지 않는다. 영상이 정말
    멈춘 경우(정체)와 비정상적으로 오래 걸리는 경우(절대 상한)만 중단한다.
    """

    def __init__(self, duration_sec: float) -> None:
        now = datetime.now()
        self._started_at = now
        self._last_advance_at = now
        self._last_position = -1.0
        self._max_elapsed_sec = 0.0
        self.set_duration(duration_sec)

    def set_duration(self, duration_sec: float) -> None:
        """실제 영상 길이를 뒤늦게 알게 됐을 때 절대 상한을 늘린다 (줄이지는 않음)."""
        self._max_elapsed_sec = max(
            self._max_elapsed_sec,
            duration_sec * PLAYBACK_MAX_DURATION_MULTIPLIER + PLAYBACK_TIMEOUT_BUFFER_SEC,
        )

    def update(self, position: float | None) -> None:
        """관측된 재생 위치를 반영. None(관측 실패)은 진행 없음으로 취급."""
        if position is None or position <= self._last_position + PLAYBACK_ADVANCE_EPSILON_SEC:
            return
        self._last_position = position
        self._last_advance_at = datetime.now()

    @property
    def stalled_sec(self) -> float:
        """마지막으로 재생 위치가 늘어난 뒤 흐른 시간"""
        return (datetime.now() - self._last_advance_at).total_seconds()

    @property
    def elapsed_sec(self) -> float:
        return (datetime.now() - self._started_at).total_seconds()

    def give_up_reason(self) -> str | None:
        """중단해야 하면 사유를, 계속 봐도 되면 None을 반환"""
        if self.stalled_sec > PLAYBACK_STALL_TIMEOUT_SEC:
            return f"재생 정체 {self.stalled_sec:.0f}s"
        if self.elapsed_sec > self._max_elapsed_sec:
            return f"최대 재생 시간 초과 {self.elapsed_sec:.0f}s"
        return None


class RequestUrlCapture:
    """페이지 이동 직전에 걸어두고, 새 페이지가 커밋된 뒤 나간 첫 영상 요청 URL을 잡는다.

    이동 뒤에 걸면 페이지 로드·이어보기 클릭 중 나간 요청을 놓치고, 그냥 먼저 걸면
    아직 스트리밍 중인 이전 강의의 요청을 잡는다.
    """

    def __init__(self, page: Any, predicate: Callable[[str], bool]) -> None:
        self._page = page
        self._predicate = predicate
        self._armed = False
        self.url: str | None = None
        page.on("framenavigated", self._on_navigated)
        page.on("request", self._on_request)

    def _on_navigated(self, frame: Any) -> None:
        # 여기서 url을 초기화하면 안 된다 — SPA history 이동도 framenavigated다
        if frame is self._page.main_frame:
            self._armed = True

    def _on_request(self, request: Any) -> None:
        if self._armed and self.url is None and self._predicate(request.url):
            self.url = request.url

    async def wait(self, timeout_sec: float) -> str | None:
        for _ in range(int(timeout_sec * 10)):
            if self.url:
                break
            await asyncio.sleep(0.1)
        return self.url

    def close(self) -> None:
        self._page.remove_listener("framenavigated", self._on_navigated)
        self._page.remove_listener("request", self._on_request)
