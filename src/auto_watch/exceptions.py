"""커스텀 예외 계층"""


class LMSError(Exception):
    """LMS 관련 기본 예외"""


class LoginError(LMSError):
    """로그인 실패"""


class BrowserError(LMSError):
    """브라우저/iframe 관련 오류"""


class PlaybackError(LMSError):
    """강의 재생 오류"""


class DownloadError(LMSError):
    """영상 다운로드 오류"""


class TranscriptionError(LMSError):
    """전사 처리 오류"""
