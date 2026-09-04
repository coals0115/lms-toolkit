"""범용 헬퍼"""

from datetime import datetime

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
        self._max_elapsed_sec = (
            duration_sec * PLAYBACK_MAX_DURATION_MULTIPLIER + PLAYBACK_TIMEOUT_BUFFER_SEC
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
