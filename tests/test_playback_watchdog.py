"""PlaybackWatchdog: 재생 정체 판정 테스트"""

from datetime import timedelta

import pytest

from src.auto_watch.config import (
    PLAYBACK_MAX_DURATION_MULTIPLIER,
    PLAYBACK_STALL_TIMEOUT_SEC,
    PLAYBACK_TIMEOUT_BUFFER_SEC,
)
from src.auto_watch.util import PlaybackWatchdog

TWO_HOURS = 7200


def _rewind(watchdog: PlaybackWatchdog, seconds: float) -> None:
    """시계를 seconds 만큼 앞당긴 것처럼 내부 기준시각을 뒤로 민다."""
    delta = timedelta(seconds=seconds)
    watchdog._started_at -= delta
    watchdog._last_advance_at -= delta


def test_진행중이면_영상길이를_넘겨도_포기하지_않는다():
    """기존 버그: 2시간 강의가 +60초만 늦어도 99%에서 잘렸다."""
    watchdog = PlaybackWatchdog(TWO_HOURS)
    _rewind(watchdog, TWO_HOURS + 600)  # 영상 길이보다 10분 더 걸린 상황
    watchdog.update(7100.0)  # 방금 재생 위치가 늘었다
    assert watchdog.give_up_reason() is None


def test_재생위치가_멈추면_포기한다():
    watchdog = PlaybackWatchdog(TWO_HOURS)
    watchdog.update(100.0)
    _rewind(watchdog, PLAYBACK_STALL_TIMEOUT_SEC + 1)
    watchdog.update(100.0)  # 같은 위치 = 진행 없음
    assert "정체" in (watchdog.give_up_reason() or "")


def test_관측_실패는_진행으로_치지_않는다():
    watchdog = PlaybackWatchdog(TWO_HOURS)
    _rewind(watchdog, PLAYBACK_STALL_TIMEOUT_SEC + 1)
    watchdog.update(None)
    assert watchdog.give_up_reason() is not None


def test_진행하면_정체_타이머가_초기화된다():
    watchdog = PlaybackWatchdog(TWO_HOURS)
    watchdog.update(100.0)
    _rewind(watchdog, PLAYBACK_STALL_TIMEOUT_SEC - 10)
    watchdog.update(160.0)  # 진행함
    assert watchdog.stalled_sec < 1
    assert watchdog.give_up_reason() is None


def test_절대_상한을_넘으면_포기한다():
    """무한 루프 방지용 안전장치 — 계속 진행 중이어도 상한은 있다."""
    watchdog = PlaybackWatchdog(TWO_HOURS)
    cap = TWO_HOURS * PLAYBACK_MAX_DURATION_MULTIPLIER + PLAYBACK_TIMEOUT_BUFFER_SEC
    _rewind(watchdog, cap + 10)
    watchdog.update(7100.0)  # 진행은 하고 있음
    assert "최대" in (watchdog.give_up_reason() or "")


@pytest.mark.parametrize("position", [0.0, 0.4])
def test_미세한_증가는_진행이_아니다(position):
    """부동소수 흔들림을 진행으로 오인하면 정체를 영영 못 잡는다."""
    watchdog = PlaybackWatchdog(TWO_HOURS)
    watchdog.update(0.0)
    _rewind(watchdog, PLAYBACK_STALL_TIMEOUT_SEC + 1)
    watchdog.update(position)
    assert watchdog.give_up_reason() is not None
