import os
import subprocess


def convert_mp4_to_wav(mp4_path: str, wav_path: str, sample_rate: int = 16000) -> None:
    """MP4를 WAV(mono, 16kHz)로 변환"""
    if not os.path.exists(mp4_path):
        raise FileNotFoundError(f"입력 파일이 존재하지 않음: {mp4_path}")

    command = [
        "ffmpeg",
        "-i",
        mp4_path,
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        wav_path,
        "-y",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 오류: {result.stderr}")
