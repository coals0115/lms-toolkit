import json
import logging
import os
import time
from abc import ABC, abstractmethod

import requests
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()
logger = logging.getLogger(__name__)

# https://developers.rtzr.ai/docs/stt-file/


def transcribe_wav_to_text(wav_path: str, txt_path: str, engine: str = "whisper") -> None:
    if engine == "whisper":
        transcriber = WhisperTranscriber()
    elif engine == "returnzero":
        transcriber = ReturnZeroTranscriber()
    else:
        raise ValueError("지원하지 않는 엔진입니다")

    transcriber.transcribe(wav_path, txt_path)


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, wav_path: str, txt_path: str) -> None:
        pass


class WhisperTranscriber(Transcriber):
    def __init__(self, model_name: str = "turbo") -> None:
        import sys

        device = "cpu"
        compute_type = "int8"

        # .app 번들 내부의 모델 확인
        if getattr(sys, "frozen", False):
            model_path = os.path.join(sys._MEIPASS, "whisper_models", model_name)  # type: ignore[attr-defined]
            if os.path.exists(model_path):
                logger.info("번들된 Whisper 모델 사용: %s", model_path)
                self.model = WhisperModel(model_path, device=device, compute_type=compute_type)
                return

        logger.info("faster-whisper 모델 로드 중: %s", model_name)
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: str, txt_path: str) -> None:
        segments, info = self.model.transcribe(wav_path, language="ko", beam_size=5)
        duration = info.duration

        texts = []
        last_report = 0
        for segment in segments:
            texts.append(segment.text)
            if duration and segment.end - last_report >= 60:
                pct = segment.end / duration * 100
                seg_m, seg_s = divmod(int(segment.end), 60)
                dur_m, dur_s = divmod(int(duration), 60)
                logger.info(
                    "스크립트: 전사 %d:%02d/%d:%02d (%.0f%%)",
                    seg_m,
                    seg_s,
                    dur_m,
                    dur_s,
                    pct,
                )
                last_report = segment.end

        text = "".join(texts)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info("Whisper 변환 완료: %s", txt_path)
        logger.info("감지된 언어: %s (확률: %.2f)", info.language, info.language_probability)


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
        logger.info("ReturnZero 인증 토큰 발급 성공")
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

    def _poll_until_complete(
        self,
        transcribe_id: str,
        timeout: int = 180,
        interval: int = 5,
    ) -> dict:
        # 변환 완료 대기
        url = f"https://openapi.vito.ai/v1/transcribe/{transcribe_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        start = time.time()

        while time.time() - start < timeout:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            logger.info("Polling 현재 상태: %s", status)
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

    def transcribe(self, wav_path: str, txt_path: str) -> None:
        transcribe_id = self._submit_job(wav_path)
        data = self._poll_until_complete(transcribe_id)
        text = self._parse_text(data)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        txt_raw_path = txt_path.replace(".txt", "_raw_rtzr.txt")
        with open(txt_raw_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=4))

        logger.info("ReturnZero 텍스트 변환 완료: %s", txt_path)
