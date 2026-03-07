# LMS 자동 수강 & 스크립트 추출

숭실대(Canvas LMS) + 숭실사이버대(KCU) 동영상 강의를 자동으로 수강 처리하고, 강의 스크립트를 추출하는 CLI 도구입니다. LMSProvider Protocol 기반 멀티 LMS 구조.

## 주요 기능

- **멀티 LMS 지원**: 숭실대(SSU) / 숭실사이버대(KCU) 학교 선택
- **자동 수강 모드**: 미수강 과목만 스캔 → 재생으로 출석 인정 + 다운로드/전사
- **다운로드 모드**: 과목 선택 → lazy 스캔 → 다운로드/전사만 (재생/출석 없음)
- **movie 타입 필터**: 동영상 강의만 표시 (PDF/과제 자동 제외)
- 재생과 동시에 **MP4 다운로드 + 음성→텍스트 전사** (병렬 처리)
- KCU: 2배속 기본, 수강 가능 주차 자동 필터링

## 요구 사항

- Python 3.11 이상
- ffmpeg
- Google Chrome (Mac 기준 `/Applications/Google Chrome.app/`)

## 빠른 시작

### 설치 (최초 1회)

```bash
bash install.sh
```

Homebrew, Python, ffmpeg, Playwright 등 필요한 모든 것을 자동 설치하고, 숭실대 학번/비밀번호를 입력받아 `.env` 파일을 생성합니다. 숭실사이버대(KCU) 계정도 선택적으로 함께 등록할 수 있습니다.

### 실행

```bash
./run.sh          # 또는
uv run lms-toolkit        # uv가 PATH에 있는 경우
```

### 수동 설치 (install.sh 없이)

<details>
<summary>펼치기</summary>

```bash
git clone https://github.com/<your-repo>/lms-toolkit.git
cd lms-toolkit

brew install ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run python -m playwright install chromium
```

프로젝트 루트에 `.env` 파일 생성:

```
# 숭실대 (USERID/PASSWORD도 하위호환 지원)
SSU_USERID=학번
SSU_PASSWORD=비밀번호

# 숭실사이버대
KCU_USERID=학번
KCU_PASSWORD=비밀번호
```

실행:

```bash
uv run lms-toolkit
```

</details>

## 사용 흐름

실행하면 먼저 학교를 선택하고, 이어서 모드를 선택합니다:

```
학교를 선택하세요:
  [1] 숭실대 (SSU)
  [2] 숭실사이버대 (KCU)
  [q] 종료

번호: 1

모드를 선택하세요:
  [1] 자동 수강 — 미수강 동영상 재생 + 다운로드/전사
  [2] 다운로드  — 과목 선택 → 강의 다운로드/전사만
  [q] 종료

번호 (1/2) / q(종료):
```

### 자동 수강 모드 (1)

미수강 동영상이 있는 과목만 스캔하여 강의 목록을 표시합니다:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  전체 강의 6개 (미수강 3 / 수강완료 3):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [ 1]    1주 1강 강의 개요 (28:47)
  [ 2]    1주 2강 뉴스·SNS·광고 속 숫자 읽기 기초 (34:47)
  [ 3]    1주 3강 그래프·지표를 볼 때의 기본 관점 (24:02)
  [ 4] V  2주 1강 ... (30:00)
  ...

번호 / all(전체) / b(이전) / q(종료) / e(펼치기):
```

### 다운로드 모드 (2)

과목 목록만 먼저 표시하고 (빠름), 선택한 과목의 강의만 스캔합니다.

> 다운로드 모드에서는 미수강/수강완료 상관없이 다운로드/전사만 수행합니다.

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
│   ├── main.py                # 오케스트레이터 (학교 선택 → 모드 → 실행)
│   ├── config.py              # 환경변수 + 상수 + 학교별 설정 (SchoolConfig)
│   ├── types.py               # TypedDict (Course, Lecture 등)
│   ├── exceptions.py          # 커스텀 예외 계층
│   ├── log.py                 # 로깅 설정
│   ├── browser.py             # Playwright 브라우저 설정
│   ├── provider.py            # LMSProvider Protocol + 팩토리
│   ├── providers/             # 학교별 Provider 구현
│   │   ├── ssu.py             # 숭실대 (Canvas LMS)
│   │   └── kcu.py             # 숭실사이버대 (KCU LMS)
│   ├── plugin.py              # 플러그인 인프라 (entry_points 기반)
│   ├── transcription.py       # 영상 다운로드 + 음성→텍스트 전사
│   └── cli.py                 # CLI UI + 유틸리티
└── audio_pipeline/
    ├── converter.py           # MP4 → WAV (ffmpeg)
    └── transcriber.py         # WAV → TXT (faster-whisper)
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
uv run python -m playwright install chromium
```

### faster-whisper 모델

첫 실행 시 turbo 모델(~1.5GB)을 HuggingFace에서 자동 다운로드합니다. 네트워크 연결을 확인하세요.

### ModuleNotFoundError

uv를 통해 실행하면 패키지가 자동으로 설치됩니다:

```bash
uv run lms-toolkit
```
