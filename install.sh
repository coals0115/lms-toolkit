#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────
# LMS Summarizer 설치 스크립트
# ─────────────────────────────────────────────

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# 색상
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
info() { echo -e "  ${YELLOW}[설치중]${NC} $1"; }
fail() { echo -e "  ${RED}[오류]${NC} $1"; }

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD} LMS Summarizer 설치${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ─────────────────────────────────────────────
# 1/6: Homebrew
# ─────────────────────────────────────────────
echo -e "${BOLD}[1/6] Homebrew 확인...${NC}"

if command -v brew &>/dev/null; then
    ok "Homebrew 이미 설치됨"
else
    info "Homebrew 설치 중... (비밀번호 입력이 필요할 수 있습니다)"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Apple Silicon PATH 설정
    if [[ "$(uname -m)" == "arm64" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi

    if command -v brew &>/dev/null; then
        ok "Homebrew 설치 완료"
    else
        fail "Homebrew 설치에 실패했습니다."
        exit 1
    fi
fi

# ─────────────────────────────────────────────
# 2/6: uv
# ─────────────────────────────────────────────
echo -e "${BOLD}[2/6] uv 확인...${NC}"

if command -v uv &>/dev/null; then
    ok "uv $(uv --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
else
    info "uv 설치 중..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    ok "uv 설치 완료"
fi

# ─────────────────────────────────────────────
# 3/6: ffmpeg
# ─────────────────────────────────────────────
echo -e "${BOLD}[3/6] ffmpeg 확인...${NC}"

if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg 이미 설치됨"
else
    info "ffmpeg 설치 중... (시간이 좀 걸릴 수 있습니다)"
    brew install ffmpeg
    ok "ffmpeg 설치 완료"
fi

# ─────────────────────────────────────────────
# 4/6: Google Chrome
# ─────────────────────────────────────────────
echo -e "${BOLD}[4/6] Google Chrome 확인...${NC}"

CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ -x "$CHROME_PATH" ]; then
    ok "Google Chrome 확인됨"
else
    echo ""
    fail "Google Chrome이 설치되어 있지 않습니다."
    echo "     이 프로그램은 Chrome 브라우저가 반드시 필요합니다."
    echo "     https://www.google.com/chrome/ 에서 설치 후 다시 실행해주세요."
    echo ""
    exit 1
fi

# ─────────────────────────────────────────────
# 5/6: uv sync + Playwright
# ─────────────────────────────────────────────
echo -e "${BOLD}[5/6] Python 패키지 설치...${NC}"

info "의존성 동기화 중..."
uv sync
ok "패키지 설치 완료"

info "Chromium 설치 중..."
uv run python -m playwright install chromium 2>&1 | tail -1
ok "Playwright 설정 완료"

# ─────────────────────────────────────────────
# 6/6: .env 파일
# ─────────────────────────────────────────────
echo -e "${BOLD}[6/6] 환경 설정...${NC}"

CREATE_ENV=true

if [ -f ".env" ]; then
    echo ""
    echo "  .env 파일이 이미 존재합니다."
    read -p "  덮어쓰시겠습니까? (y/N): " overwrite
    if [[ "$overwrite" != [yY] ]]; then
        ok "기존 .env 유지"
        CREATE_ENV=false
    fi
fi

if [ "$CREATE_ENV" = true ]; then
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD} 숭실대 LMS 로그인 정보 입력${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    read -p "  학번: " userid
    read -sp "  비밀번호 (화면에 표시되지 않습니다): " password
    echo ""

    cat > .env << EOF
USERID=${userid}
PASSWORD=${password}
EOF

    ok ".env 파일 생성 완료"
fi

# ─────────────────────────────────────────────
# 완료
# ─────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD} 설치 완료!${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  실행 방법:"
echo "    uv run lms"
echo ""
echo "  참고: 첫 실행 시 AI 모델 다운로드로"
echo "        약 1.5GB 추가 다운로드가 발생합니다."
echo ""
