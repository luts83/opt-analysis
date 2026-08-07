#!/usr/bin/env bash
# 새 컴퓨터 / 새 클론에서 한 번에 로컬 환경을 준비한다.
#   ./scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "❌ Python 3.11+ 가 필요합니다. (Dockerfile 기준: 3.11)"
  exit 1
fi

VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
MAJOR="${VER%%.*}"
MINOR="${VER#*.}"
if (( MAJOR < 3 || (MAJOR == 3 && MINOR < 11) )); then
  echo "❌ Python 3.11+ 필요 (현재: $VER, 바이너리: $PYTHON_BIN)"
  exit 1
fi

echo "→ Python: $PYTHON_BIN ($VER)"

if [[ ! -d .venv ]]; then
  echo "→ .venv 생성"
  "$PYTHON_BIN" -m venv .venv
else
  echo "→ .venv 이미 있음"
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
echo "→ requirements.txt 설치"
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "→ .env 생성 (.env.example 복사)"
  echo "  ⚠ 다른 컴퓨터의 키를 .env 에 넣으세요 (OPENAI / TELEGRAM 등)"
  echo "  운영 키 참고: Railway Variables"
else
  echo "→ .env 이미 있음 (덮어쓰지 않음)"
fi

echo
echo "✅ 셋업 완료"
echo "   source .venv/bin/activate"
echo "   ./scripts/doctor.sh     # 환경 점검"
echo "   make report-preview     # 리포트 미리보기"
echo "   make bot                # 텔레그램 봇"
