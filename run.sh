#!/bin/bash

# LMS Summarizer CLI 실행 스크립트

cd "$(dirname "$0")"

# PYTHONPATH 설정
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# 가상환경 활성화 및 실행
source .venv/bin/activate
python src/main.py
