"""영상 다운로드 및 음성-텍스트 전사"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import requests as req_lib

from .cli import _safe_filename
from .config import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_REPORT_INTERVAL,
    DOWNLOAD_TIMEOUT,
    OUTPUT_DIR,
    PROJECT_DIR,
    USER_AGENT,
)
from .types import TranscriptResult

if TYPE_CHECKING:
    from src.audio_pipeline.transcriber import WhisperTranscriber

logger = logging.getLogger(__name__)

# --- Whisper 싱글턴 ---
_whisper_instance: WhisperTranscriber | None = None


def _get_whisper() -> WhisperTranscriber:
    global _whisper_instance
    if _whisper_instance is None:
        from src.audio_pipeline.transcriber import WhisperTranscriber

        _whisper_instance = WhisperTranscriber()
    return _whisper_instance


# --- 동시성 제한 (lazy init: import 시점에 이벤트 루프 없을 수 있음) ---
_download_sem: asyncio.Semaphore | None = None
_transcribe_sem: asyncio.Semaphore | None = None


def _get_download_sem() -> asyncio.Semaphore:
    global _download_sem
    if _download_sem is None:
        _download_sem = asyncio.Semaphore(2)
    return _download_sem


def _get_transcribe_sem() -> asyncio.Semaphore:
    global _transcribe_sem
    if _transcribe_sem is None:
        _transcribe_sem = asyncio.Semaphore(1)
    return _transcribe_sem


def _download_mp4(video_url: str, mp4_path, referer: str) -> str:
    """HTTP 직접 다운로드 (MP4)"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
    }
    resp = req_lib.get(video_url, stream=True, headers=headers, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    last_report = 0
    part_path = _part_path(mp4_path)
    try:
        with open(part_path, "wb") as f:
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
        part_path.replace(mp4_path)
    finally:
        part_path.unlink(missing_ok=True)
    return str(mp4_path)


def _part_path(mp4_path: Path) -> Path:
    # 완성된 파일만 .mp4 이름을 갖게 해야 "이미 받았으면 건너뛰기"가 반쯤 받은 파일을 완성본으로 착각하지 않는다.
    return mp4_path.with_name(mp4_path.stem + ".part.mp4")


async def _download_hls(video_url: str, mp4_path) -> str:
    """ffmpeg로 HLS(m3u8) → MP4 변환 (진행률 실시간 출력)"""
    import re

    part_path = _part_path(mp4_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-rw_timeout",
        str(DOWNLOAD_TIMEOUT[1] * 1_000_000),
        "-i",
        video_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(part_path),
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
        part_path.unlink(missing_ok=True)
        stderr_text = b"".join(stderr_chunks[-20:]).decode(errors="replace")
        raise RuntimeError(f"ffmpeg 실패: {stderr_text[-500:]}")
    part_path.replace(mp4_path)
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

    if txt_path.exists():
        logger.info("스크립트: 이미 있음 — 건너뜀 (%s)", txt_path.name)
        result["txt"] = str(txt_path)
        if mp4_path.exists():
            result["mp4"] = str(mp4_path)
        return result

    # 1. 다운로드 (동시 2개 제한)
    try:
        if mp4_path.exists():
            logger.info("다운로드: 이미 있음 — 건너뜀")
            result["mp4"] = str(mp4_path)
        else:
            async with _get_download_sem():
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

    # 2. mp4 → wav → txt (동시 1개 제한 — CPU 집중)
    try:

        def _transcribe():
            import time

            from src.audio_pipeline.converter import convert_mp4_to_wav

            wav_path = course_dir / f"{safe_title}.wav"

            logger.info("스크립트: [1/3] mp4 → wav 변환 중...")
            convert_mp4_to_wav(str(mp4_path), str(wav_path))

            logger.info("스크립트: [2/3] Whisper 모델 로딩...")
            transcriber = _get_whisper()

            logger.info("스크립트: [3/3] 음성 → 텍스트 전사 중...")
            t_start = time.time()
            transcriber.transcribe(str(wav_path), str(txt_path))
            elapsed = time.time() - t_start
            em, es = divmod(int(elapsed), 60)
            logger.info("스크립트: 전사 완료 (%d분 %d초)", em, es)

            wav_path.unlink(missing_ok=True)
            return str(txt_path)

        async with _get_transcribe_sem():
            result["txt"] = await loop.run_in_executor(None, _transcribe)
        logger.info("스크립트: 저장 완료 → %s", txt_path.relative_to(PROJECT_DIR))
    except Exception:
        logger.exception("전사 실패")

    return result
