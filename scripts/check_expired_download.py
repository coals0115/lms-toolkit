"""출석 마감 강의 다운로드 속도 테스트. 결과를 /tmp/test_result.txt에 기록.

실행: PYTHONPATH=. uv run python scripts/check_expired_download.py
"""

import asyncio
import time

from playwright.async_api import async_playwright

from src.auto_watch.browser import setup_browser
from src.auto_watch.provider import get_provider

import sys
import logging

LOG = "/tmp/test_result.txt"

# stderr 로그도 파일에 기록
file_handler = logging.FileHandler("/tmp/test_result_debug.log", mode="w")
file_handler.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, logging.StreamHandler(sys.stderr)])

def log(msg):
    print(msg)
    with open(LOG, "a") as f:
        f.write(msg + "\n")


async def main():
    with open(LOG, "w") as f:
        f.write("")

    provider = get_provider("ssu")
    async with async_playwright() as p:
        page, browser, _ctx = await setup_browser(p)  # headed
        try:
            await provider.login(page)
            log("login ok")
            lectures = await provider.get_lectures(page, "43262", "자료구조")
            log(f"lectures: {len(lectures)}")
            target = [l for l in lectures if l["isCompleted"]][0]
            log(f"target: {target['title']}")
            t0 = time.perf_counter()
            result = await provider.process_lecture(page, target, defer_transcript=True)
            elapsed = time.perf_counter() - t0
            log(f"result: {elapsed:.1f}s dl={result.get('download_only')} mp4={result.get('mp4')}")
            tasks = await provider.drain_tasks()
            for t in tasks:
                log(f"  task: mp4={t.get('mp4')}")
            log("OK" if elapsed < 15 else "SLOW")
        except Exception as e:
            log(f"ERROR: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
