# CLAUDE.md

## 프로젝트 개요

숭실대 LMS 미수강 동영상 강의를 자동 수강 처리 + 스크립트 추출하는 CLI 도구. `src/auto_watch.py`가 유일한 진입점.

## 실행

```bash
python -m src.auto_watch
```

## 환경 변수 (.env)

```
USERID=(학번)
PASSWORD=(비밀번호)
```

## 비직관적 사실

- **Python 3.11+** 필수 (CTranslate2 요구)
- **headed 브라우저 필수**: 헤드리스면 LTI 플레이어 완료 이벤트 미발생
- **Chrome 경로 하드코딩**: `/Applications/Google Chrome.app/`
- **Whisper 모델 캐시**: `~/.cache/huggingface/hub/` (~1.5GB)
- 출력: `output/과목명/` 에 `.mp4` + `.txt` 저장. WAV는 전사 후 자동 삭제

## 규칙

- `src/auto_watch.py`의 사용자 대면 동작 변경 시 `spec.md`도 함께 업데이트할 것
- 사용자 대면 메시지 및 문서는 한국어
- Mac 전용 (Windows 미지원)
