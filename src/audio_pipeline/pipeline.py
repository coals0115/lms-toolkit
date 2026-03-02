import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from src.audio_pipeline.converter import convert_mp4_to_wav
from src.audio_pipeline.transcriber import transcribe_wav_to_text
import os
from pathlib import Path


def _process_single_file(args: tuple) -> str:
    """ProcessPoolExecutor에서 호출되는 독립 함수 (pickle 가능해야 함)"""
    mp4_path, sample_rate, remove_wav = args

    if not mp4_path.endswith(".mp4"):
        raise ValueError("mp4 파일만 처리 가능합니다.")

    filename = Path(mp4_path).stem
    downloads_dir = os.path.dirname(mp4_path)
    txt_path = os.path.join(downloads_dir, f"{filename}.txt")

    print(f"[INFO] 변환 시작: {mp4_path}")
    start_time = time.time()
    wav_path = os.path.join(downloads_dir, f"{filename}.wav")
    convert_mp4_to_wav(mp4_path, wav_path, sample_rate)
    print(f"[INFO] 텍스트 변환 시작: {wav_path}")
    transcribe_wav_to_text(wav_path, txt_path)
    print(f"[DONE] 텍스트 저장 완료: {txt_path}")
    end_time = time.time()
    print(f"[INFO] 소요 시간: {end_time - start_time:.1f}초 ({filename})")

    if remove_wav:
        os.remove(wav_path)
        print(f"[INFO] 임시 파일 삭제됨: {wav_path}")

    return txt_path


class AudioToTextPipeline:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate

    def process(self, mp4_path: str, remove_wav: bool = True) -> str:
        # return txt_path
        if not mp4_path.endswith(".mp4"):
            raise ValueError("mp4 파일만 처리 가능합니다.")

        # 파일명 추출
        filename = Path(mp4_path).stem
        # mp4 파일이 있는 디렉토리에 txt 파일 저장
        downloads_dir = os.path.dirname(mp4_path)
        txt_path = os.path.join(downloads_dir, f"{filename}.txt")

        print(f"[INFO] 변환 시작: {mp4_path}")
        start_time = time.time()
        wav_path = os.path.join(downloads_dir, f"{filename}.wav")
        convert_mp4_to_wav(mp4_path, wav_path, self.sample_rate)
        print(f"[INFO] 텍스트 변환 시작: {wav_path}")
        transcribe_wav_to_text(wav_path, txt_path)
        print(f"[DONE] 텍스트 저장 완료: {txt_path}")
        end_time = time.time()
        print(f"[INFO] 총 소요 시간: {end_time - start_time}초")

        # 중간 파일인 wav 삭제
        if remove_wav:
            os.remove(wav_path)
            print(f"[INFO] 임시 파일 삭제됨: {wav_path}")

        return txt_path

    def process_batch(self, mp4_paths: list[str], max_workers: int = 3, remove_wav: bool = True) -> list[str]:
        """여러 MP4 파일을 병렬로 텍스트 변환

        Args:
            mp4_paths: 변환할 MP4 파일 경로 리스트
            max_workers: 동시에 실행할 최대 프로세스 수 (기본값: 3)
            remove_wav: 중간 WAV 파일 삭제 여부 (기본값: True)

        Returns:
            성공적으로 변환된 TXT 파일 경로 리스트
        """
        if not mp4_paths:
            print("[WARN] 변환할 파일이 없습니다.")
            return []

        print(f"[INFO] {len(mp4_paths)}개의 파일을 {max_workers}개의 워커로 병렬 변환합니다.")
        start_time = time.time()

        txt_paths = []
        args_list = [(path, self.sample_rate, remove_wav) for path in mp4_paths]

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_single_file, args): args[0] for args in args_list}

            for future in as_completed(futures):
                mp4_path = futures[future]
                try:
                    txt_path = future.result()
                    txt_paths.append(txt_path)
                except Exception as e:
                    print(f"[ERROR] 변환 실패 ({mp4_path}): {e}")

        end_time = time.time()
        print(f"[INFO] 총 {len(txt_paths)}/{len(mp4_paths)}개 파일 변환 완료 (총 {end_time - start_time:.1f}초)")
        return txt_paths
