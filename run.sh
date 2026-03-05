#!/bin/bash

# LMS Toolkit 실행 스크립트

cd "$(dirname "$0")"

# 사전 체크
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/activate" ]; then
    echo "[오류] 아직 설치가 완료되지 않았습니다."
    echo "       먼저 ./install.sh 를 실행해주세요."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "[오류] .env 파일이 없습니다."
    echo "       먼저 ./install.sh 를 실행해주세요."
    exit 1
fi

if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    echo "[오류] Google Chrome이 설치되어 있지 않습니다."
    echo "       https://www.google.com/chrome/ 에서 설치해주세요."
    exit 1
fi

# 가상환경 활성화 및 실행
source .venv/bin/activate
python -m src.auto_watch
