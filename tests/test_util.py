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


class _FakePage:
    def __init__(self):
        self.main_frame = object()
        self.handlers: dict[str, list] = {}

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def remove_listener(self, event, fn):
        self.handlers[event].remove(fn)

    def emit(self, event, arg):
        for fn in list(self.handlers.get(event, [])):
            fn(arg)


class _Req:
    def __init__(self, url):
        self.url = url


class TestRequestUrlCapture:
    def _capture(self):
        from src.auto_watch.util import RequestUrlCapture

        page = _FakePage()
        return page, RequestUrlCapture(page, lambda u: u.endswith(".mp4"))

    def test_ignores_requests_from_previous_page(self):
        page, cap = self._capture()
        page.emit("request", _Req("http://x/old.mp4"))  # 이전 강의가 아직 스트리밍 중
        page.emit("framenavigated", page.main_frame)
        page.emit("request", _Req("http://x/new.mp4"))
        assert cap.url == "http://x/new.mp4"

    def test_iframe_navigation_does_not_reset(self):
        page, cap = self._capture()
        page.emit("framenavigated", page.main_frame)
        page.emit("request", _Req("http://x/a.mp4"))
        page.emit("framenavigated", object())  # 플레이어 iframe 로드
        assert cap.url == "http://x/a.mp4"

    def test_close_removes_listeners(self):
        page, cap = self._capture()
        cap.close()
        assert all(not v for v in page.handlers.values())

    def test_later_main_frame_navigation_keeps_url(self):
        page, cap = self._capture()
        page.emit("framenavigated", page.main_frame)
        page.emit("request", _Req("http://x/a.mp4"))
        page.emit("framenavigated", page.main_frame)  # SPA history 이동
        assert cap.url == "http://x/a.mp4"
