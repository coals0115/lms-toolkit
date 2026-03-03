"""설정 상수 및 환경변수 로딩"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 인증
USERID = os.getenv("USERID")
PASSWORD = os.getenv("PASSWORD")

# LMS URL
BASE_URL = "https://canvas.ssu.ac.kr"
MYPAGE_URL = f"{BASE_URL}/accounts/1/external_tools/67?launch_type=global_navigation"

# 파일 경로
PROJECT_DIR = Path(__file__).parent.parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"
