"""숭실대 LMS 자동 수강 시스템

mypage에서 미수강 동영상 과목을 감지하고,
각 강의를 1x 배속으로 재생하여 수강 인정 처리.

Usage:
    python -m src.auto_watch
"""

import asyncio

from src.auto_watch.main import main

if __name__ == "__main__":
    asyncio.run(main())
