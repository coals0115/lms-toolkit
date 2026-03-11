"""설정 상수 및 환경변수 로딩"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv, set_key

load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    """환경변수의 불리언 값을 안전하게 파싱"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _find_default_chrome_path() -> str | None:
    """운영체제별 기본 Chrome 실행 파일 경로를 탐색"""
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    elif sys.platform.startswith("linux"):
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    else:
        candidates = []

    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


@dataclass
class SchoolConfig:
    name: str
    display_name: str
    base_url: str
    userid: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


# 학교별 인증 (기존 USERID/PASSWORD는 SSU 폴백 = 하위호환)
SSU_USERID = os.getenv("SSU_USERID") or os.getenv("USERID")
SSU_PASSWORD = os.getenv("SSU_PASSWORD") or os.getenv("PASSWORD")
KCU_USERID = os.getenv("KCU_USERID")
KCU_PASSWORD = os.getenv("KCU_PASSWORD")

SCHOOL_CONFIGS: dict[str, SchoolConfig] = {
    "ssu": SchoolConfig(
        name="ssu",
        display_name="숭실대",
        base_url="https://canvas.ssu.ac.kr",
        userid=SSU_USERID,
        password=SSU_PASSWORD,
    ),
    "kcu": SchoolConfig(
        name="kcu",
        display_name="숭실사이버대",
        base_url="https://lms.kcu.ac",
        userid=KCU_USERID,
        password=KCU_PASSWORD,
    ),
}

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"


def update_credentials(school: str, userid: str, password: str) -> None:
    """학교별 USERID/PASSWORD를 .env 파일에 저장하고 설정도 갱신"""
    prefix = school.upper()
    set_key(str(_ENV_PATH), f"{prefix}_USERID", userid)
    set_key(str(_ENV_PATH), f"{prefix}_PASSWORD", password)

    config = SCHOOL_CONFIGS.get(school)
    if config:
        config.userid = userid
        config.password = password


# 파일 경로
PROJECT_DIR = Path(__file__).parent.parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"

# Obsidian vault (플러그인용)
VAULT_PATH = os.getenv("VAULT_PATH", "")

# 브라우저
_chrome_path_env = os.getenv("CHROME_PATH", "").strip()
CHROME_PATH = _chrome_path_env or _find_default_chrome_path()
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
HEADLESS = _env_flag("LMS_HEADLESS", default=False)

# 타임아웃 (밀리초)
LOGIN_TIMEOUT_MS = 10_000
SELECTOR_TIMEOUT_MS = 15_000
IFRAME_TIMEOUT_MS = 20_000
RESUME_DIALOG_TIMEOUT_MS = 5_000
RESUME_DIALOG_POST_PLAY_TIMEOUT_MS = 10_000

# 재생 모니터링
PLAYBACK_LOG_INTERVAL_SEC = 30
PLAYBACK_COMPLETION_THRESHOLD = 99.5
PLAYBACK_TIMEOUT_BUFFER_SEC = 60

# 다운로드
DOWNLOAD_CHUNK_SIZE = 65_536
DOWNLOAD_REPORT_INTERVAL = 50 * 1024 * 1024
