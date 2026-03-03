# LMS 자동 수강 & 스크립트 추출

숭실대학교 LMS(Canvas)의 미수강 동영상 강의를 자동으로 수강 처리하고, 강의 스크립트를 추출하는 CLI 도구입니다.

## 주요 기능

- 마이페이지에서 **미수강 강의 자동 감지**
- 1x 배속 재생으로 **출석 인정 처리**
- 재생과 동시에 **MP4 다운로드 + 음성→텍스트 전사** (병렬 처리)
- 강의 선택 재생 (번호 지정 / 전체 / 10초 타임아웃 시 자동 전체)

## 요구 사항

- Python 3.9 이상
- ffmpeg
- Google Chrome (Mac 기준 `/Applications/Google Chrome.app/`)

## 빠른 시작

### 1. 환경 설정

```bash
git clone https://github.com/<your-repo>/lms-summarizer.git
cd lms-summarizer

python3 -m venv .venv
source .venv/bin/activate

pip3 install -r requirements.txt
playwright install chromium
```

### 2. ffmpeg 설치

```bash
brew install ffmpeg
```

### 3. .env 설정

프로젝트 루트에 `.env` 파일 생성:

```
USERID=학번
PASSWORD=LMS비밀번호
```

### 4. 실행

```bash
python -m src.auto_watch
```

## 사용 흐름

```
실행 → LMS 로그인 → 미수강 강의 감지 → 목록 표시
                                          ↓
                                    번호 선택 (또는 all)
                                          ↓
                              강의 재생 (출석) + 다운로드 + 전사
                                          ↓
                                    output/에 결과 저장
```

실행하면 미수강 강의 목록이 표시됩니다:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  미수강 강의 3개:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [1] 데이터베이스 — 7주차 강의 (24:30) D-3
  [2] 운영체제 — 중간고사 범위 정리 (15:22) D-5
  [3] 컴퓨터네트워크 — TCP/IP (32:10) D-7

  총 재생시간: 1:12:02

재생할 번호 (예: 1,2 / all / q) [10초 후 자동 all]:
```

## 출력 파일

결과물은 `output/과목명/` 폴더에 저장됩니다:

```
output/
└── 데이터베이스/
    ├── 7주차 강의.mp4    # 다운로드된 동영상
    └── 7주차 강의.txt    # 전사된 스크립트
```

> 중간에 생성되는 WAV 파일은 전사 완료 후 자동 삭제됩니다.

## 프로젝트 구조

```
src/
├── auto_watch/                # 핵심 패키지
│   ├── __main__.py            # python -m src.auto_watch 진입점
│   ├── main.py                # 오케스트레이터
│   ├── config.py              # 환경변수 + 상수
│   ├── browser.py             # Playwright 브라우저 설정 + SSO 로그인
│   ├── courses.py             # 미수강 과목/강의 탐색
│   ├── player.py              # 강의 재생 + 수강 처리
│   ├── transcription.py       # 영상 다운로드 + 음성→텍스트 전사
│   └── cli.py                 # CLI UI + 유틸리티
├── audio_pipeline/
│   ├── converter.py           # MP4 → WAV (ffmpeg)
│   └── transcriber.py         # WAV → TXT (faster-whisper)
└── summarize_pipeline/        # AI 요약 (예정)
    └── summarizer.py
```

## 사용 기술

| 역할 | 기술 |
|------|------|
| LMS 자동화 | Playwright (Chromium) |
| 음성 인식 | faster-whisper (turbo 모델, CPU int8) |
| AI 요약 | Google Gemini API (예정) |

## 트러블슈팅

### Playwright 브라우저

```bash
playwright install chromium
```

### faster-whisper 모델

첫 실행 시 turbo 모델(~1.5GB)을 HuggingFace에서 자동 다운로드합니다. 네트워크 연결을 확인하세요.

### ModuleNotFoundError

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```
