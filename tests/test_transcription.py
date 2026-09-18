"""transcription.py 다운로드 견고성 테스트"""

import pytest
import requests

from src.auto_watch import transcription


class _FakeResp:
    def __init__(self, chunks, fail_after=None):
        self.headers = {"Content-Length": str(sum(len(c) for c in chunks))}
        self._chunks = chunks
        self._fail_after = fail_after

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.ConnectionError("끊김")
            yield c


class TestDownloadMp4:
    def test_timeout_is_set(self, tmp_path, monkeypatch):
        seen = {}

        def fake_get(url, **kw):
            seen.update(kw)
            return _FakeResp([b"abc"])

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        transcription._download_mp4("http://x", tmp_path / "a.mp4", "")
        assert seen.get("timeout")

    def test_success_writes_final_file_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            transcription.req_lib, "get", lambda url, **kw: _FakeResp([b"ab", b"cd"])
        )
        out = tmp_path / "a.mp4"
        transcription._download_mp4("http://x", out, "")
        assert out.read_bytes() == b"abcd"
        assert [p.name for p in tmp_path.iterdir()] == ["a.mp4"]

    def test_interrupted_download_leaves_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            transcription.req_lib, "get", lambda url, **kw: _FakeResp([b"ab", b"cd"], fail_after=1)
        )
        with pytest.raises(requests.ConnectionError):
            transcription._download_mp4("http://x", tmp_path / "a.mp4", "")
        assert list(tmp_path.iterdir()) == []


class TestSkipExisting:
    async def test_existing_mp4_and_txt_are_not_redone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcription, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(transcription, "PROJECT_DIR", tmp_path)
        course = tmp_path / "과목"
        course.mkdir()
        (course / "강의.mp4").write_bytes(b"x")
        (course / "강의.txt").write_text("done", encoding="utf-8")

        def boom(*a, **kw):
            raise AssertionError("다시 받으면 안 됨")

        monkeypatch.setattr(transcription, "_download_mp4", boom)
        monkeypatch.setattr(transcription, "_get_whisper", boom)

        result = await transcription.download_and_transcribe("http://x", "과목", "강의")
        assert result == {"mp4": str(course / "강의.mp4"), "txt": str(course / "강의.txt")}
