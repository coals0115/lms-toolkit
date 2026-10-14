"""main.py 배치 흐름 테스트"""

from src.auto_watch import main


class _FlakyProvider:
    def __init__(self):
        self.processed = []
        self.drained = False

    async def get_lectures(self, page, course_id, name):
        return [{"title": t, "durationSec": 60, "courseName": name} for t in ("a", "b", "c")]

    async def process_lecture(self, page, lecture, defer_transcript=False):
        self.processed.append(lecture["title"])
        if lecture["title"] == "a":
            raise TimeoutError("세션 만료")
        return {"attended": True, "download_only": False, "mp4": None, "txt": None}

    async def drain_tasks(self):
        self.drained = True
        return [{"course": "과목", "title": "b", "mp4": "b.mp4", "txt": "b.txt", "skipped": False}]


async def test_one_failing_lecture_does_not_stop_batch(monkeypatch, capsys):
    monkeypatch.setattr(main, "select_lectures", lambda lectures: lectures)
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    provider = _FlakyProvider()

    await main._run_watch_mode(None, [{"courseId": "1", "name": "과목", "videoCount": 3}], provider)

    assert provider.processed == ["a", "b", "c"]
    assert provider.drained
    out = capsys.readouterr().out
    assert "수강 실패: 1개" in out
    # a 는 수강 실패로 이미 셌으니, 영상이 안 나온 c 만 다운로드 실패로 보여야 한다
    assert "다운로드/전사 실패: 1개" in out
    assert "- c: 영상 URL 못 찾음" in out


async def _no_sleep(_):
    pass


class _HangingBrowser:
    def __init__(self):
        self.called = False

    async def close(self):
        self.called = True
        await __import__("asyncio").sleep(3600)


async def test_close_browser_gives_up_when_chrome_hangs(monkeypatch):
    monkeypatch.setattr(main, "BROWSER_CLOSE_TIMEOUT_SEC", 0.05)
    browser = _HangingBrowser()

    await main._close_browser(browser)

    assert browser.called


def test_ctrl_c_during_input_closes_and_exits(monkeypatch, capsys):
    closed = []

    async def fake_main():
        try:
            raise KeyboardInterrupt  # input() 중 Ctrl+C
        finally:
            closed.append(True)

    monkeypatch.setattr(main, "main", fake_main)

    import pytest

    with pytest.raises(SystemExit) as e:
        main.run()
    assert e.value.code == 130
    assert closed
    assert "중단했습니다" in capsys.readouterr().out
