# CLAUDE.md

## 프로젝트 개요

숭실대(Canvas LMS) + 숭실사이버대(KCU) 동영상 강의를 자동 수강 처리 + 스크립트 추출하는 CLI 도구. LMSProvider Protocol 기반 멀티 LMS 구조. 두 가지 모드 제공: 자동 수강(watch) / 다운로드(download). `src/auto_watch/` 패키지가 핵심.

## 실행

```bash
./run.sh                          # 가장 간단
uv run lms-toolkit                # project.scripts 엔트리포인트
uv run python -m src.auto_watch   # 모듈 직접 실행
```

## 개발

```bash
uv sync --extra dev               # dev 의존성 설치
uv run ruff check src/            # 린트
uv run ruff format src/           # 포매팅
uv run mypy src/                  # 타입 체크
uv run pytest -v                  # 테스트
```

## 환경 변수 (.env)

```
# 숭실대 (필수, install.sh에서 자동 생성)
SSU_USERID=(학번)
SSU_PASSWORD=(비밀번호)

# 숭실사이버대 (선택, install.sh에서 선택 입력 또는 수동 추가)
KCU_USERID=(학번)
KCU_PASSWORD=(비밀번호)

# 레거시 호환: USERID/PASSWORD도 SSU 폴백으로 지원
CHROME_PATH=(선택, Chrome 경로 오버라이드)
```

## 비직관적 사실

- **Python >=3.11** (uv + pyproject.toml 기반)
- 기본은 headed, 필요 시 `--headless` 또는 `LMS_HEADLESS=1`로 headless 실행 가능
- **Chrome 경로**: 기본 `/Applications/Google Chrome.app/`, `CHROME_PATH` 환경변수로 오버라이드 가능
- **Whisper 모델 캐시**: `~/.cache/huggingface/hub/` (~1.5GB)
- 출력: `output/과목명/` 에 `.mp4` + `.txt` 저장. WAV는 전사 후 자동 삭제
- **로깅**: 시스템 로그는 stderr (logging 모듈), CLI 대면 출력만 stdout (print)

## 플러그인 구조

- `src/auto_watch/plugin.py`에 `entry_points` 기반 plugin discovery 구현
- **학업 관리(tracker) 기능**은 별도 private 패키지 `lms-tracker`로 분리됨 (경로: `../lms-tracker/`)
  - 목적: 자동 수강이 출석을 찍어주지만 실제 공부 여부는 별도 추적 필요 (출석 vs 공부 2트랙)
- tracker 관련 작업 시 `lms-tracker` 패키지에서 수정할 것 (`lms-toolkit`에는 tracker 코드 없음)

## 규칙

- `src/auto_watch/`, `install.sh`, `run.sh`, `pyproject.toml` 변경 시 영향받는 문서를 같이 업데이트:
  - CLI 출력/흐름 변경 → `README.md` (사용 예시, 사용 흐름)
  - 함수명/구조 변경 → `spec.md` (수정 대상 파일, 함수명 표)
  - 마일스톤 완료 → `spec.md` 체크마크(✅) 갱신
- 사용자 대면 메시지 및 문서는 한국어
- Mac 전용 (Windows 미지원)

## Git 커밋

- **코드 변경 후 항상 커밋한다.** 작업 단위(논리적으로 독립된 변경)마다 나눠서 커밋
- Conventional Commits 형식: `<type>(<scope>): <한국어 설명>`
  - type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
  - scope: 생략 가능. 마일스톤이 있으면 `feat(M2.5):` 형태로 표기
- 커밋 메시지는 한국어, 첫 줄 72자 이내
- 하나의 커밋에 하나의 관심사 (docs와 구현 코드를 섞지 않는다)
