#!/usr/bin/env python3
"""
단일 영상 파일을 텍스트로 변환하는 스크립트
"""
import sys
import os

# src 디렉토리를 Python 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from audio_pipeline.pipeline import AudioToTextPipeline


def main():
    # 영상 파일 경로
    video_path = "/Users/musinsa/PycharmProjects/lms-summarizer/downloads/6주차(총 12주 중) 비전채플 : 인생 몰라요(임헌진 감독).mp4"

    # 출력할 txt 파일 경로 (원하는 경로로 변경 가능)
    # 기본값: 영상과 같은 폴더에 저장됨

    print(f"[INFO] 영상 파일: {video_path}")

    # 파일 존재 확인
    if not os.path.exists(video_path):
        print(f"[ERROR] 파일을 찾을 수 없습니다: {video_path}")
        return

    # 오디오 파이프라인 실행
    pipeline = AudioToTextPipeline()
    try:
        txt_path = pipeline.process(video_path)
        print(f"\n✅ 변환 완료!")
        print(f"📄 저장된 파일: {txt_path}")
    except Exception as e:
        print(f"\n❌ 변환 실패: {e}")


if __name__ == "__main__":
    main()