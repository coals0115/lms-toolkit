"""transcription.py 다운로드 견고성 테스트"""

import shutil
import subprocess
from typing import ClassVar

import pytest
import requests

from src.auto_watch import transcription


class _FakeResp:
    def __init__(self, chunks, fail_after=None, status=200, content_length=None):
        self.status_code = status
        length = sum(len(c) for c in chunks) if content_length is None else content_length
        self.headers = {"Content-Length": str(length)}
        self._chunks = chunks
        self._fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.ConnectionError("끊김")
            yield c


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(transcription, "DOWNLOAD_RETRY_BACKOFF_SEC", 0)


@pytest.fixture(autouse=True)
def no_range_server(monkeypatch):
    """기본은 Range 를 모르는 서버 — 한 줄 다운로드 테스트가 실제 네트워크로 HEAD 를 보내지 않게"""

    class _Head:
        status_code = 405
        headers: ClassVar[dict] = {}

    monkeypatch.setattr(transcription.req_lib, "head", lambda url, **kw: _Head())


class _RangeServer:
    """bytes Range 를 받는 가짜 서버. fail_once 에 든 시작 위치의 첫 요청은 중간에 끊는다."""

    def __init__(self, body: bytes, etag="v1", fail_once=(), ignore_range=False):
        self.body = body
        self.etag = etag
        self.fail_once = set(fail_once)
        self.ignore_range = ignore_range
        self.ranges: list[str] = []

    def head(self, url, **kw):
        class _H:
            status_code = 200
            headers: ClassVar[dict] = {
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(self.body)),
                "ETag": self.etag,
            }

        return _H()

    def get(self, url, **kw):
        rng = kw["headers"].get("Range")
        self.ranges.append(rng)
        if self.ignore_range or not rng:
            return _FakeResp([self.body])
        a, b = rng.removeprefix("bytes=").split("-")
        start, end = int(a), int(b) if b else len(self.body) - 1
        piece = self.body[start : end + 1]
        half = [piece[: len(piece) // 2], piece[len(piece) // 2 :]]
        if start in self.fail_once:
            self.fail_once.discard(start)
            return _FakeResp(half, fail_after=1, status=206)
        return _FakeResp(half, status=206)


@pytest.fixture
def small_parallel(monkeypatch):
    monkeypatch.setattr(transcription, "DOWNLOAD_PARALLEL_MIN_BYTES", 10)
    monkeypatch.setattr(transcription, "DOWNLOAD_PARALLEL_MIN_SEGMENT", 10)
    monkeypatch.setattr(transcription, "DOWNLOAD_PARALLEL_CONNECTIONS", 4)


def _serve(monkeypatch, server):
    monkeypatch.setattr(transcription.req_lib, "head", server.head)
    monkeypatch.setattr(transcription.req_lib, "get", server.get)


class TestDownloadParallel:
    BODY = bytes(range(256)) * 4  # 1024 bytes

    def test_splits_into_ranges_and_reassembles(self, tmp_path, monkeypatch, small_parallel):
        server = _RangeServer(self.BODY)
        _serve(monkeypatch, server)
        part = tmp_path / "a.part.mp4"

        assert transcription._download_mp4_parallel("http://x", part, "", "t") is True

        assert part.read_bytes() == self.BODY
        assert len(server.ranges) == 4
        assert sorted(tmp_path.iterdir()) == [part]  # 구간 파일은 합친 뒤 지운다

    def test_continues_after_existing_part(self, tmp_path, monkeypatch, small_parallel):
        server = _RangeServer(self.BODY)
        _serve(monkeypatch, server)
        part = tmp_path / "a.part.mp4"
        part.write_bytes(self.BODY[:300])

        transcription._download_mp4_parallel("http://x", part, "", "t")

        assert part.read_bytes() == self.BODY
        assert min(int(r.removeprefix("bytes=").split("-")[0]) for r in server.ranges) == 300

    def test_small_remainder_falls_back(self, tmp_path, monkeypatch):
        server = _RangeServer(self.BODY)  # 기본 기준(32MB)보다 작다
        _serve(monkeypatch, server)
        assert (
            transcription._download_mp4_parallel("http://x", tmp_path / "a.part.mp4", "", "t")
            is False
        )
        assert server.ranges == []

    def test_no_range_support_falls_back(self, tmp_path, small_parallel):
        assert (
            transcription._download_mp4_parallel("http://x", tmp_path / "a.part.mp4", "", "t")
            is False
        )

    def test_changed_file_is_rejected(self, tmp_path, monkeypatch, small_parallel):
        _serve(
            monkeypatch, _RangeServer(self.BODY, ignore_range=True)
        )  # If-Range 불일치 → 200 전체
        with pytest.raises(OSError):
            transcription._download_mp4_parallel("http://x", tmp_path / "a.part.mp4", "", "t")

    async def test_failed_segment_resumes_on_retry(
        self, tmp_path, monkeypatch, small_parallel, no_backoff
    ):
        server = _RangeServer(self.BODY, fail_once={256})
        _serve(monkeypatch, server)
        out = tmp_path / "a.mp4"

        await transcription._download_with_retry("http://x", out, "t", "", hls=False)

        assert out.read_bytes() == self.BODY
        assert [p.name for p in tmp_path.iterdir()] == ["a.mp4"]
        # 끊긴 구간(256-511)은 받은 128바이트 뒤부터, 나머지 구간은 다시 안 받는다
        assert server.ranges.count("bytes=384-511") == 1
        assert len(server.ranges) == 5


class TestDownloadMp4:
    def test_timeout_is_set(self, tmp_path, monkeypatch):
        seen = {}

        def fake_get(url, **kw):
            seen.update(kw)
            return _FakeResp([b"abc"])

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        transcription._download_mp4("http://x", tmp_path / "a.part.mp4", "", "t")
        assert seen.get("timeout")

    def test_resumes_from_part_with_range(self, tmp_path, monkeypatch):
        part = tmp_path / "a.part.mp4"
        part.write_bytes(b"ab")
        seen = {}

        def fake_get(url, **kw):
            seen.update(kw)
            return _FakeResp([b"cd"], status=206)

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        transcription._download_mp4("http://x", part, "", "t")

        assert seen["headers"]["Range"] == "bytes=2-"
        assert part.read_bytes() == b"abcd"

    def test_restarts_when_server_ignores_range(self, tmp_path, monkeypatch):
        part = tmp_path / "a.part.mp4"
        part.write_bytes(b"stale")
        monkeypatch.setattr(transcription.req_lib, "get", lambda url, **kw: _FakeResp([b"full"]))

        transcription._download_mp4("http://x", part, "", "t")

        assert part.read_bytes() == b"full"

    def test_short_body_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            transcription.req_lib,
            "get",
            lambda url, **kw: _FakeResp([b"abc"], content_length=10),
        )
        with pytest.raises(OSError):
            transcription._download_mp4("http://x", tmp_path / "a.part.mp4", "", "t")

    def test_client_error_is_not_retryable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            transcription.req_lib, "get", lambda url, **kw: _FakeResp([], status=403)
        )
        with pytest.raises(transcription._NonRetryableError):
            transcription._download_mp4("http://x", tmp_path / "a.part.mp4", "", "t")


class TestDownloadWithRetry:
    async def test_interrupted_download_resumes_on_retry(self, tmp_path, monkeypatch, no_backoff):
        responses = iter([_FakeResp([b"ab", b"cd"], fail_after=1), _FakeResp([b"cd"], status=206)])
        ranges = []

        def fake_get(url, **kw):
            ranges.append(kw["headers"].get("Range"))
            return next(responses)

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        out = tmp_path / "a.mp4"
        await transcription._download_with_retry("http://x", out, "t", "", hls=False)

        assert ranges == [None, "bytes=2-"]
        assert out.read_bytes() == b"abcd"
        assert [p.name for p in tmp_path.iterdir()] == ["a.mp4"]

    async def test_gives_up_but_keeps_part_for_next_run(self, tmp_path, monkeypatch, no_backoff):
        calls = []

        def fake_get(url, **kw):
            calls.append(1)
            return _FakeResp([b"ab", b"cd"], fail_after=1 if len(calls) == 1 else 0)

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        with pytest.raises(requests.ConnectionError):
            await transcription._download_with_retry(
                "http://x", tmp_path / "a.mp4", "t", "", hls=False
            )

        assert len(calls) == transcription.DOWNLOAD_MAX_ATTEMPTS
        assert [p.name for p in tmp_path.iterdir()] == ["a.part.mp4"]

    async def test_client_error_fails_without_retry(self, tmp_path, monkeypatch, no_backoff):
        calls = []

        def fake_get(url, **kw):
            calls.append(1)
            return _FakeResp([], status=404)

        monkeypatch.setattr(transcription.req_lib, "get", fake_get)
        with pytest.raises(transcription._NonRetryableError):
            await transcription._download_with_retry(
                "http://x", tmp_path / "a.mp4", "t", "", hls=False
            )
        assert len(calls) == 1


class TestSkipExisting:
    @pytest.fixture
    def course(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcription, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(transcription, "PROJECT_DIR", tmp_path)
        course = tmp_path / "과목"
        course.mkdir()
        return course

    @staticmethod
    def _boom(*a, **kw):
        raise AssertionError("다시 하면 안 됨")

    async def test_existing_mp4_and_txt_are_not_redone(self, course, monkeypatch):
        (course / "강의.mp4").write_bytes(b"x")
        (course / "강의.txt").write_text("done", encoding="utf-8")
        monkeypatch.setattr(transcription, "_download_with_retry", self._boom)
        monkeypatch.setattr(transcription, "_get_whisper", self._boom)

        result = await transcription.download_and_transcribe("http://x", "과목", "강의")

        assert result["mp4"] == str(course / "강의.mp4")
        assert result["txt"] == str(course / "강의.txt")
        assert result["skipped"] is True
        assert transcription.transcript_exists("과목", "강의")

    async def test_existing_mp4_only_transcribes(self, course, monkeypatch):
        (course / "강의.mp4").write_bytes(b"x")
        monkeypatch.setattr(transcription, "_download_with_retry", self._boom)

        class _FakeWhisper:
            def transcribe(self, audio_path, txt_path):
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write("전사")

        monkeypatch.setattr(transcription, "_get_whisper", _FakeWhisper)

        result = await transcription.download_and_transcribe("http://x", "과목", "강의")

        assert result["txt"] == str(course / "강의.txt")
        assert result["skipped"] is False
        assert sorted(p.name for p in course.iterdir()) == ["강의.mp4", "강의.txt"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
class TestStoryParts:
    @staticmethod
    def _clip(path, sec):
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={sec}",
                "-f",
                "lavfi",
                "-i",
                f"color=size=64x64:duration={sec}",
                "-shortest",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(path),
            ],
            check=True,
        )

    @staticmethod
    def _duration(path):
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(out.stdout)

    async def test_downloads_each_story_and_concats(self, tmp_path, monkeypatch):
        src = {"http://x/1.mp4": tmp_path / "s1.mp4", "http://x/2.mp4": tmp_path / "s2.mp4"}
        self._clip(src["http://x/1.mp4"], 1)
        self._clip(src["http://x/2.mp4"], 2)
        got = []

        async def fake_retry(url, out, title, referer, hls):
            got.append(url)
            out.write_bytes(src[url].read_bytes())

        monkeypatch.setattr(transcription, "_download_with_retry", fake_retry)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        mp4 = out_dir / "lec.mp4"

        await transcription._download_story_parts(list(src), mp4, "t", "")

        assert got == list(src)
        assert abs(self._duration(mp4) - 3) < 0.3
        assert [p.name for p in out_dir.iterdir()] == ["lec.mp4"]

    async def test_already_downloaded_story_is_not_refetched(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        self._clip(out_dir / "lec.story1.mp4", 1)
        self._clip(tmp_path / "s2.mp4", 1)
        got = []

        async def fake_retry(url, out, title, referer, hls):
            got.append(url)
            out.write_bytes((tmp_path / "s2.mp4").read_bytes())

        monkeypatch.setattr(transcription, "_download_with_retry", fake_retry)
        await transcription._download_story_parts(
            ["http://x/1", "http://x/2"], out_dir / "lec.mp4", "t", ""
        )
        assert got == ["http://x/2"]
