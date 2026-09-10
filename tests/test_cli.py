"""순수 함수 단위 테스트"""

from src.auto_watch.cli import _safe_filename
from src.auto_watch.config import SCHOOL_CONFIGS
from src.auto_watch.providers.ssu import SSUProvider

_is_target_video_url = SSUProvider(SCHOOL_CONFIGS["ssu"])._is_target_video_url


class TestIsTargetVideoUrl:
    def test_valid_commons_url(self):
        url = "https://commons.ssu.ac.kr/em/media_files/abc123/media.mp4"
        assert _is_target_video_url(url) is True

    def test_valid_cdn_url(self):
        url = "https://some.commonscdn.com/em/media_files/abc123/video.mp4"
        assert _is_target_video_url(url) is True

    def test_reject_intro(self):
        url = "https://commons.ssu.ac.kr/em/media_files/abc123/intro.mp4"
        assert _is_target_video_url(url) is False

    def test_reject_non_mp4(self):
        url = "https://commons.ssu.ac.kr/em/media_files/abc123/video.m3u8"
        assert _is_target_video_url(url) is False

    def test_reject_no_media_files(self):
        url = "https://commons.ssu.ac.kr/em/other_path/abc123/video.mp4"
        assert _is_target_video_url(url) is False

    def test_reject_other_domain(self):
        url = "https://example.com/media_files/abc123/video.mp4"
        assert _is_target_video_url(url) is False


class TestSafeFilename:
    def test_special_chars(self):
        assert _safe_filename('test:file/name*"bad') == "test_file_name__bad"

    def test_normal_name(self):
        assert _safe_filename("정상적인 파일명") == "정상적인 파일명"

    def test_strips_whitespace(self):
        assert _safe_filename("  file  ") == "file"
