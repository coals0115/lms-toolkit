"""영상 다운로드 및 음성-텍스트 전사"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import requests as req_lib

from .cli import _safe_filename
from .config import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_MAX_ATTEMPTS,
    DOWNLOAD_PARALLEL_CONNECTIONS,
    DOWNLOAD_PARALLEL_MIN_BYTES,
    DOWNLOAD_PARALLEL_MIN_SEGMENT,
    DOWNLOAD_REPORT_INTERVAL,
    DOWNLOAD_RETRY_BACKOFF_SEC,
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


def output_paths(course_name: str, title: str) -> tuple[Path, Path]:
    course_dir = OUTPUT_DIR / _safe_filename(course_name)
    safe_title = _safe_filename(title)
    return course_dir / f"{safe_title}.mp4", course_dir / f"{safe_title}.txt"


def transcript_exists(course_name: str, title: str) -> bool:
    return output_paths(course_name, title)[1].exists()


def _part_path(path: Path) -> Path:
    # 완성된 파일만 원래 이름을 갖게 해야 "이미 받았으면 건너뛰기"가 반쯤 받은 파일을 완성본으로 착각하지 않는다.
    return path.with_name(f"{path.stem}.part{path.suffix}")


class _NonRetryableError(Exception):
    pass


def _brief(e: Exception) -> str:
    # requests 예외 문자열은 urllib3 예외를 튜플째 품고 있어 한 줄 로그로 쓰기엔 너무 길다
    text = str(e)
    if isinstance(e, req_lib.Timeout) or "timed out" in text:
        return "응답 없음"
    if isinstance(e, req_lib.exceptions.ChunkedEncodingError) or "Connection broken" in text:
        return "연결 끊김"
    return text[:120] or type(e).__name__


def _download_mp4(video_url: str, part_path: Path, referer: str, title: str) -> None:
    """HTTP 직접 다운로드 (MP4). 앞선 시도가 남긴 part 파일이 있으면 Range 로 이어받는다."""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
    }
    offset = part_path.stat().st_size if part_path.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"

    with req_lib.get(video_url, stream=True, headers=headers, timeout=DOWNLOAD_TIMEOUT) as resp:
        if resp.status_code == 416:
            part_path.unlink(missing_ok=True)
            raise OSError("이어받기 범위 거부 — 처음부터 다시 받음")
        if 400 <= resp.status_code < 500:
            raise _NonRetryableError(f"HTTP {resp.status_code}")
        resp.raise_for_status()

        if offset and resp.status_code != 206:
            offset = 0
        if offset:
            logger.info("[%s] 다운로드: %dMB 지점부터 이어받기", title, offset // (1024 * 1024))

        length = int(resp.headers.get("Content-Length", 0))
        total = offset + length if length else 0
        downloaded = offset
        last_report = offset
        with open(part_path, "ab" if offset else "wb") as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded - last_report >= DOWNLOAD_REPORT_INTERVAL:
                        logger.info(
                            "[%s] 다운로드: %dMB / %dMB (%.0f%%)",
                            title,
                            downloaded // (1024 * 1024),
                            total // (1024 * 1024),
                            downloaded / total * 100,
                        )
                        last_report = downloaded
    if total and downloaded < total:
        raise OSError(f"본문이 중간에 끊김 ({downloaded}/{total} bytes)")


def _segment_path(part_path: Path, start: int, end: int) -> Path:
    # 경계를 이름에 넣어 두면, 재시도 때 같은 경계로 다시 쪼개져 받은 만큼 이어받을 수 있다.
    return part_path.with_name(f"{part_path.name}.{start}-{end}")


def _probe_range_support(video_url: str, headers: dict) -> tuple[int, str] | None:
    """(전체 크기, ETag) — 서버가 bytes Range 를 받을 때만. 못 알아내면 None"""
    try:
        resp = req_lib.head(
            video_url, headers=headers, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True
        )
    except req_lib.RequestException:
        return None
    if resp.status_code != 200 or "bytes" not in resp.headers.get("Accept-Ranges", "").lower():
        return None
    try:
        total = int(resp.headers.get("Content-Length", 0))
    except ValueError:
        return None
    return (total, resp.headers.get("ETag", "")) if total > 0 else None


def _download_segment(
    video_url: str, headers: dict, etag: str, start: int, end: int, seg_path: Path, progress
) -> None:
    want = end - start + 1
    have = seg_path.stat().st_size if seg_path.exists() else 0
    if have > want:
        seg_path.unlink()
        have = 0
    if have == want:
        return
    h = dict(headers, Range=f"bytes={start + have}-{end}")
    if etag:
        h["If-Range"] = etag  # 서버 파일이 바뀌었으면 206 대신 200 이 와서 아래에서 걸러진다
    with req_lib.get(video_url, stream=True, headers=h, timeout=DOWNLOAD_TIMEOUT) as resp:
        if 400 <= resp.status_code < 500 and resp.status_code != 416:
            raise _NonRetryableError(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        if resp.status_code != 206:
            raise OSError(f"구간 요청에 {resp.status_code} 응답 — 서버 파일이 바뀌었을 수 있음")
        with open(seg_path, "ab") as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    progress(len(chunk))
    got = seg_path.stat().st_size
    if got != want:
        raise OSError(f"구간이 중간에 끊김 ({start}-{end}: {got}/{want} bytes)")


def _download_mp4_parallel(video_url: str, part_path: Path, referer: str, title: str) -> bool:
    """남은 양이 크고 서버가 Range 를 받으면 구간별로 동시에 받아 part 뒤에 붙인다.
    해당 안 되면 아무것도 안 하고 False — 호출자가 한 줄 다운로드로 넘어간다."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    headers = {"User-Agent": USER_AGENT, "Referer": referer}
    probe = _probe_range_support(video_url, headers)
    if probe is None:
        return False
    total, etag = probe

    offset = part_path.stat().st_size if part_path.exists() else 0
    if offset > total:
        part_path.unlink()
        offset = 0
    remaining = total - offset
    if remaining < DOWNLOAD_PARALLEL_MIN_BYTES:
        return False

    n = max(1, min(DOWNLOAD_PARALLEL_CONNECTIONS, remaining // DOWNLOAD_PARALLEL_MIN_SEGMENT))
    step = -(-remaining // n)
    segments = [(s, min(s + step, total) - 1) for s in range(offset, total, step)]
    keep = {_segment_path(part_path, s, e).name for s, e in segments}
    for stale in part_path.parent.glob(f"{part_path.name}.*-*"):
        if stale.name not in keep:
            stale.unlink()

    lock = threading.Lock()
    state = {
        "done": offset
        + sum(
            _segment_path(part_path, s, e).stat().st_size
            for s, e in segments
            if _segment_path(part_path, s, e).exists()
        ),
        "last": 0,
    }

    def progress(nbytes: int) -> None:
        with lock:
            state["done"] += nbytes
            if state["done"] - state["last"] >= DOWNLOAD_REPORT_INTERVAL:
                state["last"] = state["done"]
                logger.info(
                    "[%s] 다운로드: %dMB / %dMB (%.0f%%, 동시 %d개)",
                    title,
                    state["done"] // (1024 * 1024),
                    total // (1024 * 1024),
                    state["done"] / total * 100,
                    len(segments),
                )

    logger.info(
        "[%s] 다운로드: %dMB 를 %d개 구간으로 동시에 받음%s",
        title,
        remaining // (1024 * 1024),
        len(segments),
        f" ({offset // (1024 * 1024)}MB 지점부터)" if offset else "",
    )
    with ThreadPoolExecutor(len(segments)) as ex:
        futures = [
            ex.submit(
                _download_segment,
                video_url,
                headers,
                etag,
                s,
                e,
                _segment_path(part_path, s, e),
                progress,
            )
            for s, e in segments
        ]
        for fut in futures:
            fut.result()  # 실패한 구간이 있으면 여기서 예외 → 재시도 때 받은 구간은 이어받는다

    import shutil

    with open(part_path, "ab") as out:
        for s, e in segments:
            seg = _segment_path(part_path, s, e)
            with open(seg, "rb") as f:
                shutil.copyfileobj(f, out, 1024 * 1024)
    for s, e in segments:
        _segment_path(part_path, s, e).unlink()
    if part_path.stat().st_size != total:
        size = part_path.stat().st_size
        part_path.unlink()
        raise OSError(f"구간을 합친 크기가 다름 ({size}/{total} bytes) — 처음부터 다시 받음")
    return True


def _download_mp4_fast(video_url: str, part_path: Path, referer: str, title: str) -> None:
    if not _download_mp4_parallel(video_url, part_path, referer, title):
        _download_mp4(video_url, part_path, referer, title)


async def _run_ffmpeg(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return proc.returncode or 0, err.decode(errors="replace").strip()


async def _concat_mp4(parts: list[Path], out_path: Path) -> None:
    """mp4 여러 개를 순서대로 이어 붙인다. 먼저 재인코딩 없이(-c copy), 안 되면 다시 인코딩한다."""
    list_file = out_path.with_name(f"{out_path.name}.concat.txt")
    list_file.write_text(
        "".join(
            f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
            for p in parts
        ),
        encoding="utf-8",
    )
    try:
        base = ["-f", "concat", "-safe", "0", "-i", str(list_file)]
        code, err = await _run_ffmpeg([*base, "-c", "copy", "-f", "mp4", str(out_path)])
        if code != 0:
            logger.warning(
                "이어 붙이기(-c copy) 실패 — 다시 인코딩함: %s", err.splitlines()[-1:] or ""
            )
            code, err = await _run_ffmpeg(
                [
                    *base,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "aac",
                    "-f",
                    "mp4",
                    str(out_path),
                ]
            )
        if code != 0:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg 이어 붙이기 실패: {err.splitlines()[-1:] or ''}")
    finally:
        list_file.unlink(missing_ok=True)


async def _download_story_parts(urls: list[str], mp4_path: Path, title: str, referer: str) -> None:
    """한 강의가 영상 여러 개로 나뉜 경우: 하나씩 받아 두고(이어받기 가능) 이어 붙인다"""
    parts = [
        mp4_path.with_name(f"{mp4_path.stem}.story{i}{mp4_path.suffix}")
        for i in range(1, len(urls) + 1)
    ]
    for i, (url, part) in enumerate(zip(urls, parts, strict=True), 1):
        if part.exists():
            continue
        await _download_with_retry(url, part, f"{title} ({i}/{len(urls)})", referer, hls=False)
    joined = _part_path(mp4_path)
    await _concat_mp4(parts, joined)
    joined.replace(mp4_path)
    for p in parts:
        p.unlink(missing_ok=True)


async def _download_hls(video_url: str, part_path: Path, title: str) -> None:
    """ffmpeg로 HLS(m3u8) → MP4 변환 (진행률 실시간 출력). 이어받기는 안 되고 매번 처음부터."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
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
                logger.info("[%s] 다운로드: %d:%02d 처리됨...", title, mm, ss)
                last_log_sec = sec
    await proc.wait()
    if proc.returncode != 0:
        part_path.unlink(missing_ok=True)
        stderr_text = b"".join(stderr_chunks[-20:]).decode(errors="replace")
        last_line = stderr_text.strip().splitlines()[-1] if stderr_text.strip() else ""
        if re.search(r"Server returned 4\d\d", stderr_text):
            raise _NonRetryableError(f"ffmpeg: {last_line}")
        raise RuntimeError(f"ffmpeg 실패: {last_line}")


async def _download_with_retry(
    video_url: str, mp4_path: Path, title: str, referer: str, hls: bool
) -> None:
    loop = asyncio.get_running_loop()
    part_path = _part_path(mp4_path)
    for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            if hls:
                await _download_hls(video_url, part_path, title)
            else:
                await loop.run_in_executor(
                    None, _download_mp4_fast, video_url, part_path, referer, title
                )
            break
        except _NonRetryableError:
            raise
        except Exception as e:
            if attempt == DOWNLOAD_MAX_ATTEMPTS:
                raise
            backoff = DOWNLOAD_RETRY_BACKOFF_SEC * 2 ** (attempt - 1)
            logger.warning(
                "[%s] 다운로드 중단 (%s) — %d초 후 재시도 (%d/%d)",
                title,
                _brief(e),
                backoff,
                attempt + 1,
                DOWNLOAD_MAX_ATTEMPTS,
            )
            await asyncio.sleep(backoff)
    part_path.replace(mp4_path)


async def download_and_transcribe(
    video_url: str,
    course_name: str,
    title: str,
    *,
    referer: str = "",
    hls: bool = False,
    story_urls: list[str] | None = None,
) -> TranscriptResult:
    """영상 다운로드 + 음성→텍스트 전사 (재생과 병렬 실행).
    story_urls 가 2개 이상이면 한 강의가 영상 여러 개로 나뉜 것 — 다 받아 이어 붙인 뒤 전사한다."""
    loop = asyncio.get_running_loop()
    result: TranscriptResult = {
        "course": course_name,
        "title": title,
        "mp4": None,
        "txt": None,
        "skipped": False,
    }

    mp4_path, txt_path = output_paths(course_name, title)
    mp4_path.parent.mkdir(parents=True, exist_ok=True)

    if txt_path.exists():
        logger.info("[%s] 스크립트: 이미 있음 — 건너뜀", title)
        result["txt"] = str(txt_path)
        result["skipped"] = True
        if mp4_path.exists():
            result["mp4"] = str(mp4_path)
        return result

    # 1. 다운로드 (동시 2개 제한)
    if mp4_path.exists():
        logger.info("[%s] 다운로드: 이미 있음 — 건너뜀", title)
    else:
        try:
            download_sem = _get_download_sem()
            if download_sem.locked():
                logger.info("[%s] 다운로드: 앞 강의 다운로드가 끝나면 시작 (대기 중)", title)
            async with download_sem:
                if story_urls and len(story_urls) > 1 and not hls:
                    logger.info(
                        "[%s] 다운로드: 시작... (영상 %d개를 이어 붙임)", title, len(story_urls)
                    )
                    await _download_story_parts(story_urls, mp4_path, title, referer)
                else:
                    logger.info("[%s] 다운로드: 시작...", title)
                    await _download_with_retry(video_url, mp4_path, title, referer, hls)
            size_mb = mp4_path.stat().st_size / (1024 * 1024)
            logger.info("[%s] 다운로드: 완료 (%.1fMB)", title, size_mb)
        except _NonRetryableError as e:
            logger.error("[%s] 다운로드 실패: %s — 영상 링크가 만료됐을 수 있음", title, e)
            return result
        except OSError as e:
            resumable = _part_path(mp4_path).exists()
            logger.error(
                "[%s] 다운로드 실패: %s%s",
                title,
                _brief(e),
                " — 다음 실행 때 이어받음" if resumable else "",
            )
            return result
        except RuntimeError as e:
            logger.error("[%s] 다운로드 실패: %s", title, e)
            return result
        except Exception:
            logger.exception("[%s] 다운로드 실패", title)
            return result
    result["mp4"] = str(mp4_path)

    # 2. mp4 → txt (동시 1개 제한 — GPU/CPU 집중)
    try:

        def _transcribe():
            txt_part = _part_path(txt_path)

            logger.info("[%s] 스크립트: [1/2] Whisper 모델 로딩...", title)
            transcriber = _get_whisper()

            logger.info("[%s] 스크립트: [2/2] 음성 → 텍스트 전사 중...", title)
            t_start = time.time()
            transcriber.transcribe(str(mp4_path), str(txt_part))
            txt_part.replace(txt_path)
            elapsed = time.time() - t_start
            em, es = divmod(int(elapsed), 60)
            logger.info("[%s] 스크립트: 전사 완료 (%d분 %d초)", title, em, es)
            return str(txt_path)

        transcribe_sem = _get_transcribe_sem()
        if transcribe_sem.locked():
            logger.info("[%s] 스크립트: 앞 강의 전사가 끝나면 시작 (대기 중)", title)
        async with transcribe_sem:
            result["txt"] = await loop.run_in_executor(None, _transcribe)
        logger.info("[%s] 스크립트: 저장 완료 → %s", title, txt_path.relative_to(PROJECT_DIR))
    except Exception:
        logger.exception("[%s] 전사 실패", title)

    return result
