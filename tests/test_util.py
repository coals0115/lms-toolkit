"""util.py 단위 테스트"""

from src.auto_watch.util import format_duration


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert format_duration(125) == "2:05"

    def test_hours(self):
        assert format_duration(3661) == "1:01:01"

    def test_zero(self):
        assert format_duration(0) == "0:00"

    def test_exact_hour(self):
        assert format_duration(3600) == "1:00:00"

    def test_over_one_hour(self):
        # 1h 23m 45s = 5025 seconds
        assert format_duration(5025) == "1:23:45"

    def test_float_input(self):
        # video.duration은 float이라 int 캐스팅 필요
        assert format_duration(125.7) == "2:05"
