"""영상 다운로드 및 음성-텍스트 전사"""

import asyncio
import logging

import requests as req_lib

from .cli import _safe_filename
from .config import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_REPORT_INTERVAL,
    OUTPUT_DIR,
    PROJECT_DIR,
    USER_AGENT,
)
from .types import TranscriptResult

logger = logging.getLogger(__name__)


def _download_mp4(video_url: str, mp4_path, referer: str) -> str:
    """HTTP 직접 다운로드 (MP4)"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
    }
    resp = req_lib.get(video_url, stream=True, headers=headers)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    last_report = 0
    with open(mp4_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded - last_report >= DOWNLOAD_REPORT_INTERVAL:
                    pct = downloaded / total * 100
                    logger.info(
                        "다운로드: %dMB / %dMB (%.0f%%)",
                        downloaded // (1024 * 1024),
                        total // (1024 * 1024),
                        pct,
                    )
                    last_report = downloaded
    return str(mp4_path)


async def _download_hls(video_url: str, mp4_path) -> str:
    """ffmpeg로 HLS(m3u8) → MP4 변환 (진행률 실시간 출력)"""
    import re

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(mp4_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # stderr를 실시간 읽으면서 time= 패턴 파싱
    last_log_sec = 0
    stderr_chunks: list[bytes] = []
    assert proc.stderr is not None
    async for line in proc.stderr:
        stderr_chunks.append(line)
        text = line.decode(errors="replace")
        m = re.search(r"time=(\d+):(\d+):(\d+)", text)
        if m:
            sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if sec - last_log_sec >= 30:
                mm, ss = divmod(sec, 60)
                logger.info("다운로드: %d:%02d 처리됨...", mm, ss)
                last_log_sec = sec
    await proc.wait()
    if proc.returncode != 0:
        stderr_text = b"".join(stderr_chunks[-20:]).decode(errors="replace")
        raise RuntimeError(f"ffmpeg 실패: {stderr_text[-500:]}")
    return str(mp4_path)


async def download_and_transcribe(
    video_url: str,
    course_name: str,
    title: str,
    *,
    referer: str = "",
    hls: bool = False,
) -> TranscriptResult:
    """영상 다운로드 + 음성→텍스트 전사 (재생과 병렬 실행)"""
    loop = asyncio.get_running_loop()
    result: TranscriptResult = {"mp4": None, "txt": None}

    course_dir = OUTPUT_DIR / _safe_filename(course_name)
    course_dir.mkdir(parents=True, exist_ok=True)

    safe_title = _safe_filename(title)
    mp4_path = course_dir / f"{safe_title}.mp4"
    txt_path = course_dir / f"{safe_title}.txt"

    # 1. 다운로드
    try:
        logger.info("다운로드: 시작...")
        if hls:
            result["mp4"] = await _download_hls(video_url, mp4_path)
        else:
            result["mp4"] = await loop.run_in_executor(
                None, _download_mp4, video_url, mp4_path, referer
            )
        size_mb = mp4_path.stat().st_size / (1024 * 1024)
        logger.info("다운로드: 완료 (%.1fMB)", size_mb)
    except Exception:
        logger.exception("다운로드 실패")
        return result

    # 2. mp4 → wav → txt
    try:

        def _transcribe():
            import time

            from src.audio_pipeline.converter import convert_mp4_to_wav
            from src.audio_pipeline.transcriber import WhisperTranscriber

            wav_path = course_dir / f"{safe_title}.wav"

            logger.info("스크립트: [1/3] mp4 → wav 변환 중...")
            convert_mp4_to_wav(str(mp4_path), str(wav_path))

            logger.info("스크립트: [2/3] Whisper 모델 로딩...")
            transcriber = WhisperTranscriber()

            logger.info("스크립트: [3/3] 음성 → 텍스트 전사 중...")
            t_start = time.time()
            transcriber.transcribe(str(wav_path), str(txt_path))
            elapsed = time.time() - t_start
            em, es = divmod(int(elapsed), 60)
            logger.info("스크립트: 전사 완료 (%d분 %d초)", em, es)

            wav_path.unlink(missing_ok=True)
            return str(txt_path)

        result["txt"] = await loop.run_in_executor(None, _transcribe)
        logger.info("스크립트: 저장 완료 → %s", txt_path.relative_to(PROJECT_DIR))
    except Exception:
        logger.exception("전사 실패")

    return result
