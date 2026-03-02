# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고할 가이드를 제공합니다.

## 프로젝트 개요

LMS Summarizer는 성신여대 LMS(Canvas)에서 동영상 강의를 자동으로 다운로드하고, faster-whisper를 사용해 텍스트로 변환한 후, Google Gemini API(google-genai SDK)를 사용해 요약을 생성하는 PySide6 기반 GUI 애플리케이션입니다. PyInstaller를 사용해 독립 실행 가능한 Mac .app으로 패키징할 수 있습니다.

## Python 환경 설정

**중요**: 이 프로젝트는 Python 3.11 이상이 필요합니다. faster-whisper(CTranslate2)와 PySide6(Qt6)를 사용합니다.

### 환경 설정 명령어

```bash
# 가상환경 생성
python3 -m venv .venv

# 가상환경 활성화 (Mac)
source .venv/bin/activate

# 가상환경 활성화 (Windows)
.venv/bin/Activate.ps1

# 의존성 설치
pip3 install -r requirements.txt

# Playwright 브라우저 설치
playwright install

# 새 패키지 추가 후 requirements 업데이트
pip3 freeze >> requirements.txt
```

### 시스템 의존성

- **ffmpeg**: 동영상을 오디오로 변환하기 위해 필요
  ```bash
  brew install ffmpeg
  ```

## 설정

### 환경 변수 (.env)

LMS 로그인 및 API 접근을 위해 필요한 인증 정보:

```
USERID=(학번)
PASSWORD=(비밀번호)
RETURNZERO_CLIENT_ID=(선택, ReturnZero STT API용)
RETURNZERO_CLIENT_SECRET=(선택, ReturnZero STT API용)
GOOGLE_API_KEY=(필수, Gemini 요약용)
```

### 선택적 설정 (user_settings.json)

처리할 동영상 URL을 미리 설정:

```json
{
  "video": [
    "https://canvas.ssu.ac.kr/courses/35082/modules/items/3160477?return_url=/courses/35082/external_tools/71"
  ]
}
```

## 애플리케이션 실행

### CLI 모드

```bash
# 프로젝트 루트에서 실행
python src/main.py
```

### GUI 모드

```bash
# GUI 애플리케이션 실행
python src/gui/main.py
```

### Mac 앱 빌드

```bash
# PyInstaller로 독립 실행 Mac .app 빌드
./build_mac_pyinstaller.sh
```

이 명령어는 `dist/LMS-Summarizer.app`과 `dist/LMS-Summarizer-Mac.zip`을 생성합니다.

## 테스트

### 테스트 실행

```bash
# 단일 테스트 파일 실행
python test/audio_pipeline_test.py
```

### PYTHONPATH 설정

테스트 실행 시 `ModuleNotFoundError`가 발생하면 PYTHONPATH를 설정하세요:

```bash
export PYTHONPATH="${PYTHONPATH}:/path/to/lms-summarizer/src"
```

Python은 엄격한 프로젝트 루트 개념이 없어서, `src/`에서의 상대 임포트를 위해 명시적인 경로 설정이 필요합니다.

## 아키텍처

### 3단계 파이프라인 아키텍처

애플리케이션은 각 단계가 이전 단계의 출력을 처리하는 순차적 파이프라인 설계를 따릅니다:

**1. 비디오 파이프라인** (`src/video_pipeline/`)
- **목적**: LMS에서 동영상 강의 다운로드
- **주요 구성요소**:
  - `login.py`: 성신여대 Canvas 인증 처리
  - `video_parser.py`: 강의 페이지에서 동영상 URL과 제목 추출
  - `download_video.py`: MP4 파일 다운로드
  - `pipeline.py`: Playwright 브라우저 자동화 조율
- **출력**: downloads 디렉토리에 MP4 동영상 파일

**2. 오디오 파이프라인** (`src/audio_pipeline/`)
- **목적**: 동영상을 텍스트 전사로 변환
- **주요 구성요소**:
  - `converter.py`: ffmpeg를 사용해 MP4를 WAV로 변환 (16kHz 샘플레이트)
  - `transcriber.py`: faster-whisper (turbo 모델, int8 양자화)를 사용한 음성-텍스트 변환
  - `pipeline.py`: 변환 → 전사 워크플로우 관리
- **출력**: 전사된 강의 내용이 담긴 TXT 파일

**3. 요약 파이프라인** (`src/summarize_pipeline/`)
- **목적**: 전사 내용의 구조화된 요약 생성
- **주요 구성요소**:
  - `summarizer.py`: Google Gemini API를 사용한 텍스트 요약
  - `pipeline.py`: 텍스트 파일 처리 및 요약 저장
- **출력**: 한국어 강의 요약이 담긴 `*_summarized.txt` 파일

### GUI 아키텍처 (`src/gui/`)

관심사가 명확히 분리된 PySide6(Qt6) 기반 모듈형 GUI:

- **`config/`**: 애플리케이션 상수 및 설정
- **`core/`**: 비즈니스 로직 (검증자, 파일 관리, 모듈 로딩)
- **`ui/`**: 뷰 레이어
  - `components/`: 재사용 가능한 위젯 (입력 필드, 버튼, 로그 영역)
  - `windows/`: 메인 애플리케이션 창
- **`workers/`**: 비동기 작업을 위한 백그라운드 스레드 워커
- **`tests/`**: GUI 컴포넌트 테스트

### 설정 및 세팅 (`src/user_setting.py`)

이중 모드 설정 시스템:
- **CLI 모드**: `.env`와 `user_settings.json`에서 로드
- **GUI 모드**: `gui_inputs` 딕셔너리로 전달된 GUI 입력값 사용
- 처리 전 Canvas LMS URL 검증

## 주요 디자인 패턴

### 파이프라인 패턴
각 단계는 이전 단계의 입력을 받아 다음 단계를 위한 출력을 반환하는 `process()` 또는 `process_sync()` 메서드를 구현합니다. 이를 통해 모듈식 테스팅과 유연한 조합이 가능합니다.

### 이중 실행 모드
모든 파이프라인은 다음 두 가지를 지원합니다:
- **비동기 모드**: Playwright 작업을 위한 네이티브 async/await
- **동기 모드**: CLI 호환성을 위한 `asyncio.run()`을 사용한 `process_sync()` 래퍼

### 다운로드 디렉토리 주입
파이프라인은 출력 경로를 하드코딩하지 않습니다. `downloads_dir` 속성은 부모 조율자(main.py 또는 GUI)에 의해 설정되어, 유연한 출력 위치 설정이 가능합니다.

## 외부 서비스 의존성

### faster-whisper
- **모델**: `turbo` (= large-v3-turbo, 첫 실행 시 HuggingFace Hub에서 자동 다운로드)
- **위치**: `~/.cache/huggingface/hub/`
- **설정**: `device="cpu"`, `compute_type="int8"` (CPU int8 양자화)
- **참고**: PyInstaller 빌드 시 `--add-data` 플래그로 번들링됨

### Google Gemini API
- **SDK**: `google-genai` (신규 통합 SDK, `google-generativeai`는 EOL)
- **모델**: `gemini-2.5-flash` (1M 토큰 컨텍스트)
- **설정**: .env에 `GOOGLE_API_KEY` 필요

### Playwright
- **목적**: LMS 인증 및 동영상 추출을 위한 브라우저 자동화
- **브라우저**: Chromium (디버깅을 위해 가시적 Chrome 실행)
- **설정**: Mac의 `/Applications/Google Chrome.app/` 경로 하드코딩

## 일반적인 문제

### 테스트에서 Import 오류
**문제**: `ModuleNotFoundError: No module named 'audio_pipeline'`
**해결**: 테스트가 `src/`에서 상대 임포트를 사용합니다. 테스트 실행 전 PYTHONPATH에 src 디렉토리를 포함시키세요.

### faster-whisper 모델 다운로드 실패
**문제**: 모델 다운로드가 느리거나 실패
**해결**: HuggingFace Hub에서 turbo 모델(~1.5GB)을 다운로드합니다. 네트워크 연결을 확인하세요.

### Playwright 브라우저를 찾을 수 없음
**문제**: 브라우저 실행 파일을 찾을 수 없음
**해결**: pip install 후 `playwright install`을 실행하고, Mac의 예상 경로에 Chrome이 설치되어 있는지 확인하세요.

## 개발 노트

- **Cursor/Copilot 규칙 없음**: 이 프로젝트에는 .cursor/rules/ 또는 .github/copilot-instructions.md 파일이 없습니다.
- **한국어 언어**: 사용자 대면 메시지, 요약 및 문서는 한국어로 작성됩니다.
- **Mac 우선**: 빌드 스크립트와 브라우저 경로는 Mac 전용입니다. Windows는 venv 활성화만 지원하며 패키징은 지원하지 않습니다.