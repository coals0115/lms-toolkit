# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고할 가이드를 제공합니다.

## 프로젝트 개요

숭실대 LMS(Canvas)의 미수강 동영상 강의를 자동으로 수강 처리하고, 강의 스크립트를 추출하는 CLI 도구입니다. `src/auto_watch.py`가 유일한 진입점이며, 마이페이지 미수강 감지 → 1x 재생(출석) → MP4 다운로드 → 음성 전사를 병렬로 처리합니다.

## Python 환경 설정

**중요**: 이 프로젝트는 Python 3.11 이상이 필요합니다. faster-whisper(CTranslate2)를 사용합니다.

### 환경 설정 명령어

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
playwright install chromium
```

### 시스템 의존성

- **ffmpeg**: 동영상을 오디오로 변환하기 위해 필요
  ```bash
  brew install ffmpeg
  ```

## 설정

### 환경 변수 (.env)

```
USERID=(학번)
PASSWORD=(비밀번호)
```

## 실행

```bash
python -m src.auto_watch
```

## 출력

결과물은 `output/과목명/` 폴더에 저장됩니다:

```
output/
└── 과목명/
    ├── 강의제목.mp4    # 다운로드된 동영상
    └── 강의제목.txt    # 전사된 스크립트
```

WAV 파일은 전사 완료 후 자동 삭제됩니다.

## 아키텍처

### 핵심 모듈

**`src/auto_watch.py`** — 단일 파일에 전체 흐름 구현:
- `setup_browser()`: Playwright headed 브라우저 설정 (봇 탐지 우회)
- `login_if_needed()`: SSO 로그인
- `get_unwatched_courses()`: 마이페이지에서 미수강 과목 감지
- `get_unwatched_lectures()`: 주차학습 페이지에서 미수강 강의 목록
- `select_lectures()`: CLI 선택 UI (번호/all/q, 10초 타임아웃)
- `watch_lecture()`: 강의 재생 + 진행률 모니터링
- `download_and_transcribe()`: MP4 다운로드 → WAV 변환 → Whisper 전사 (재생과 병렬)

### 보조 모듈

**`src/audio_pipeline/`** — auto_watch.py에서 import하여 사용:
- `converter.py`: ffmpeg로 MP4 → WAV 변환 (16kHz)
- `transcriber.py`: faster-whisper로 음성 → 텍스트 전사

**`src/summarize_pipeline/`** — AI 요약 (향후 연동 예정):
- `summarizer.py`: Google Gemini API 요약
- `pipeline.py`: 텍스트 파일 처리

## 외부 서비스 의존성

### faster-whisper
- **모델**: `turbo` (첫 실행 시 HuggingFace Hub에서 자동 다운로드, ~1.5GB)
- **캐시 위치**: `~/.cache/huggingface/hub/`
- **설정**: `device="cpu"`, `compute_type="int8"`

### Playwright
- **목적**: LMS 인증 및 동영상 재생을 위한 브라우저 자동화
- **브라우저**: headed Chrome (`/Applications/Google Chrome.app/`)
- **봇 탐지 우회**: webdriver 속성 제거, 커스텀 user-agent

## Spec 동기화 규칙

`src/auto_watch.py`의 사용자 대면 동작(CLI 흐름, 옵션, 출력 형식 등)을 변경할 때 `spec.md`도 함께 업데이트할 것. 코드 변경과 같은 커밋에 포함.

## 개발 노트

- **한국어 언어**: 사용자 대면 메시지 및 문서는 한국어로 작성됩니다.
- **Mac 전용**: 브라우저 경로가 Mac 하드코딩입니다.
