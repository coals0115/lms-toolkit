from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod

import requests
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()

# https://developers.rtzr.ai/docs/stt-file/


def transcribe_wav_to_text(wav_path: str, txt_path: str, engine="whisper"):
    if engine == "whisper":
        transcriber = WhisperTranscriber()
    elif engine == "returnzero":
        transcriber = ReturnZeroTranscriber()
    else:
        raise ValueError("지원하지 않는 엔진입니다")

    transcriber.transcribe(wav_path, txt_path)


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, wav_path: str, txt_path: str):
        pass


class WhisperTranscriber(Transcriber):
    def __init__(self, model_name="turbo"):
        import sys
        import os

        device = "cpu"
        compute_type = "int8"

        # .app 번들 내부의 모델 확인
        if getattr(sys, 'frozen', False):
            model_path = os.path.join(sys._MEIPASS, 'whisper_models', model_name)
            if os.path.exists(model_path):
                print(f"[INFO] 번들된 Whisper 모델 사용: {model_path}")
                self.model = WhisperModel(model_path, device=device, compute_type=compute_type)
                return

        # 기본 경로에서 모델 로드 (첫 실행 시 자동 다운로드)
        print(f"[INFO] faster-whisper 모델 로드 중: {model_name}")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: str, txt_path: str):
        try:
            segments, info = self.model.transcribe(wav_path, language="ko", beam_size=5)
            duration = info.duration  # 전체 오디오 길이(초)

            texts = []
            last_report = 0
            for segment in segments:
                texts.append(segment.text)
                # 60초마다 진행상황 표시
                if duration and segment.end - last_report >= 60:
                    pct = segment.end / duration * 100
                    seg_m, seg_s = divmod(int(segment.end), 60)
                    dur_m, dur_s = divmod(int(duration), 60)
                    print(f"  ├ 스크립트: 전사 {seg_m}:{seg_s:02d}/{dur_m}:{dur_s:02d} ({pct:.0f}%)")
                    last_report = segment.end

            text = "".join(texts)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[Whisper] 변환 완료: {txt_path}")
            print(f"[Whisper] 감지된 언어: {info.language} (확률: {info.language_probability:.2f})")
        except Exception as e:
            print(f"[ERROR] 변환 실패: {e}")
            raise e


class ReturnZeroTranscriber(Transcriber):
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.getenv("RETURNZERO_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("RETURNZERO_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise ValueError("RETURNZERO_CLIENT_ID/SECRET 환경변수가 필요합니다")
        self.token = self._authenticate()

    def _authenticate(self) -> str:
        # 인증 토큰 발급
        resp = requests.post(
            "https://openapi.vito.ai/v1/authenticate",
            data={"client_id": self.client_id, "client_secret": self.client_secret},
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        print("[ReturnZero] 인증 토큰 발급 성공")
        return token

    def _submit_job(self, wav_path: str) -> str:
        # 변환 요청
        with open(wav_path, "rb") as f:
            files = {
                "file": (os.path.basename(wav_path), f),
                "config": (
                    None,
                    '{"model_name":"whisper","language":"ko"}',
                    "application/json",
                ),
            }
            headers = {
                "Authorization": f"Bearer {self.token}",
                "accept": "application/json",
            }
            response = requests.post(
                "https://openapi.vito.ai/v1/transcribe",
                headers=headers,
                files=files,
            )
        response.raise_for_status()
        return response.json()["id"]  # 이게 transcribe_id

    def _poll_until_complete(self, transcribe_id: str, timeout=180, interval=5) -> dict:
        # 변환 완료 대기
        url = f"https://openapi.vito.ai/v1/transcribe/{transcribe_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        start = time.time()

        while time.time() - start < timeout:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            print(f"[Polling] 현재 상태: {status}")
            if status == "completed":
                return data
            elif status == "failed":
                raise RuntimeError(f"[ERROR] 변환 실패: {data.get('error')}")
            time.sleep(interval)

        raise TimeoutError("음성 변환 시간 초과")

    def _parse_text(self, data: dict) -> str:
        # 분리된 결과값을 합치기
        utterances = data.get("results", {}).get("utterances", [])
        messages = [utterance.get("msg", "") for utterance in utterances]
        return " ".join(messages)

    def transcribe(self, wav_path: str, txt_path: str):
        try:
            transcribe_id = self._submit_job(wav_path)
            data = self._poll_until_complete(transcribe_id)
            text = self._parse_text(data)

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)

            txt_raw_path = txt_path.replace(".txt", "_raw_rtzr.txt")
            with open(txt_raw_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=4))

            print(f"[ReturnZero] 텍스트 변환 완료: {txt_path}")

        except Exception as e:
            print(f"[ERROR] 변환 실패: {e}")
            raise e
