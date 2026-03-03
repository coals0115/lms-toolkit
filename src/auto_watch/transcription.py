"""영상 다운로드 및 음성-텍스트 전사"""

import asyncio
import traceback

import requests as req_lib

from .config import PROJECT_DIR, OUTPUT_DIR
from .cli import _safe_filename


async def download_and_transcribe(video_url: str, course_name: str, title: str) -> dict:
    """영상 다운로드 + 음성→텍스트 전사 (재생과 병렬 실행)"""
    loop = asyncio.get_running_loop()
    result = {"mp4": None, "txt": None}

    course_dir = OUTPUT_DIR / _safe_filename(course_name)
    course_dir.mkdir(parents=True, exist_ok=True)

    safe_title = _safe_filename(title)
    mp4_path = course_dir / f"{safe_title}.mp4"
    txt_path = course_dir / f"{safe_title}.txt"

    # 1. 다운로드
    try:
        def _download():
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://commons.ssu.ac.kr/",
            }
            resp = req_lib.get(video_url, stream=True, headers=headers)
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_report = 0
            with open(mp4_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        # 50MB마다 중간 보고
                        if total and downloaded - last_report >= 50 * 1024 * 1024:
                            pct = downloaded / total * 100
                            print(f"  ├ 다운로드: {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct:.0f}%)")
                            last_report = downloaded
            return str(mp4_path)

        print(f"  ├ 다운로드: 시작...")
        result["mp4"] = await loop.run_in_executor(None, _download)
        size_mb = mp4_path.stat().st_size / (1024 * 1024)
        print(f"  ├ 다운로드: 완료 ({size_mb:.1f}MB)")
    except Exception as e:
        print(f"  ├ 다운로드: 실패 — {e}")
        traceback.print_exc()
        return result

    # 2. mp4 → wav → txt
    try:
        def _transcribe():
            import time
            from src.audio_pipeline.converter import convert_mp4_to_wav
            from src.audio_pipeline.transcriber import WhisperTranscriber

            wav_path = course_dir / f"{safe_title}.wav"

            print(f"  ├ 스크립트: [1/3] mp4 → wav 변환 중...")
            convert_mp4_to_wav(str(mp4_path), str(wav_path))

            print(f"  ├ 스크립트: [2/3] Whisper 모델 로딩...")
            transcriber = WhisperTranscriber()

            print(f"  ├ 스크립트: [3/3] 음성 → 텍스트 전사 중...")
            t_start = time.time()
            transcriber.transcribe(str(wav_path), str(txt_path))
            elapsed = time.time() - t_start
            em, es = divmod(int(elapsed), 60)
            print(f"  ├ 스크립트: 전사 완료 ({em}분 {es}초)")

            wav_path.unlink(missing_ok=True)
            return str(txt_path)

        result["txt"] = await loop.run_in_executor(None, _transcribe)
        print(f"  └ 스크립트: 저장 완료 → {txt_path.relative_to(PROJECT_DIR)}")
    except Exception as e:
        print(f"  └ 스크립트: 전사 실패 — {e}")
        traceback.print_exc()

    return result
