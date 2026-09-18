import logging
import platform

logger = logging.getLogger(__name__)


# CTranslate2에 Metal 백엔드가 없어 faster-whisper는 Apple GPU를 못 쓴다.
# 같은 오디오 기준 CPU 2.5배속 → mlx 13배속이라 arm64에서는 mlx를 우선한다.
MLX_REPO = "mlx-community/whisper-large-v3-turbo"


class WhisperTranscriber:
    def __init__(self, model_name: str = "turbo") -> None:
        self._use_mlx = False
        if platform.machine() == "arm64":
            try:
                import mlx_whisper  # noqa: F401

                self._use_mlx = True
                logger.info("mlx-whisper 사용 (Apple GPU)")
                return
            except ImportError:
                logger.warning("mlx-whisper 없음 — faster-whisper(CPU)로 대체")

        from faster_whisper import WhisperModel

        logger.info("faster-whisper 모델 로드 중: %s", model_name)
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio_path: str, txt_path: str) -> None:
        if self._use_mlx:
            self._transcribe_mlx(audio_path, txt_path)
            return

        segments, info = self.model.transcribe(audio_path, language="ko", beam_size=5)
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

    def _transcribe_mlx(self, audio_path: str, txt_path: str) -> None:
        import mlx.core as mx
        import mlx_whisper

        try:
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=MLX_REPO, language="ko")
            text = result["text"]
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("Whisper 변환 완료: %s", txt_path)
        finally:
            # mlx는 Metal 버퍼 풀을 스스로 반환하지 않는다. 자동 수강은 전사가 끝나도
            # 브라우저 대기로 프로세스가 몇 시간 살아 있어, 안 지우면 그동안 계속 점유한다.
            freed = mx.get_cache_memory()
            mx.clear_cache()
            if freed:
                logger.info("GPU 캐시 반환: %.1fGB", freed / 1e9)
