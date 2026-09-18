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
        return [{"mp4": "b.mp4", "txt": "b.txt"}]


async def test_one_failing_lecture_does_not_stop_batch(monkeypatch, capsys):
    monkeypatch.setattr(main, "select_lectures", lambda lectures: lectures)
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    provider = _FlakyProvider()

    await main._run_watch_mode(None, [{"courseId": "1", "name": "과목", "videoCount": 3}], provider)

    assert provider.processed == ["a", "b", "c"]
    assert provider.drained
    out = capsys.readouterr().out
    assert "수강 실패: 1개" in out
    # 셋 중 mp4는 하나만 나왔으니 나머지 둘은 다운로드 실패로 보여야 한다
    assert "다운로드 실패: 2개" in out


async def _no_sleep(_):
    pass
