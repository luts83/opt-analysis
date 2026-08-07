#!/usr/bin/env bash
# 로컬 개발 환경이 막힘 없이 쓸 수 있는지 점검한다.
#   ./scripts/doctor.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ok=0
warn=0
fail=0

pass() { echo "✅ $1"; ok=$((ok + 1)); }
note() { echo "⚠  $1"; warn=$((warn + 1)); }
die()  { echo "❌ $1"; fail=$((fail + 1)); }

echo "=== opt-analysis doctor ==="
echo

# git
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  BRANCH="$(git branch --show-current)"
  pass "git ok (branch: $BRANCH)"
  if git remote get-url origin >/dev/null 2>&1; then
    git fetch --quiet origin 2>/dev/null || true
    LOCAL="$(git rev-parse @)"
    REMOTE="$(git rev-parse @{u} 2>/dev/null || true)"
    if [[ -n "${REMOTE:-}" && "$LOCAL" != "$REMOTE" ]]; then
      note "origin 과 다름 → git pull (또는 push) 하세요"
    else
      pass "origin 과 동기화됨 (또는 upstream 없음)"
    fi
  fi
else
  die "git 저장소가 아님"
fi

# venv
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
  pass "venv: .venv"
else
  die "venv 없음 → ./scripts/setup.sh 실행"
  PY="python3"
fi

# python version
if [[ -x "$PY" ]]; then
  VER="$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "?")"
  MAJOR="${VER%%.*}"
  MINOR="${VER#*.}"
  if [[ "$VER" != "?" ]] && (( MAJOR > 3 || (MAJOR == 3 && MINOR >= 11) )); then
    pass "Python $VER"
  else
    die "Python 3.11+ 필요 (현재: $VER)"
  fi
fi

# packages
if [[ -x .venv/bin/python ]]; then
  if .venv/bin/python -c 'import yfinance, pandas, openai, dotenv' 2>/dev/null; then
    pass "핵심 패키지 import OK"
  else
    die "패키지 누락 → source .venv/bin/activate && pip install -r requirements.txt"
  fi
fi

# .env
if [[ ! -f .env ]]; then
  die ".env 없음 → cp .env.example .env 후 키 입력"
else
  pass ".env 존재"
  # shellcheck disable=SC1091
  set -a
  # shell 이 깨지지 않도록 단순 키만 검사
  set +a
  get_env() {
    # KEY=value 형태에서 value 추출 (주석/# 무시)
    grep -E "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true
  }
  OPENAI="$(get_env OPENAI_API_KEY)"
  TG_TOKEN="$(get_env TELEGRAM_BOT_TOKEN)"
  TG_CHAT="$(get_env TELEGRAM_CHAT_ID)"

  if [[ -z "$OPENAI" || "$OPENAI" == sk-xxxx* ]]; then
    note "OPENAI_API_KEY 미설정 → AI 해설은 규칙 기반 폴백"
  else
    pass "OPENAI_API_KEY 설정됨"
  fi

  if [[ -z "$TG_TOKEN" || "$TG_TOKEN" == 123456:* ]]; then
    note "TELEGRAM_BOT_TOKEN 미설정 → 로컬 봇/발송 불가 (콘솔 리포트는 OK)"
  else
    pass "TELEGRAM_BOT_TOKEN 설정됨"
  fi

  if [[ -z "$TG_CHAT" || "$TG_CHAT" == 123456789 ]]; then
    note "TELEGRAM_CHAT_ID 미설정"
  else
    pass "TELEGRAM_CHAT_ID 설정됨"
  fi
fi

# snapshots
if [[ -d snapshots ]]; then
  pass "snapshots/ 존재"
else
  note "snapshots/ 없음 (첫 실행 시 생성됨)"
fi

# settings
if [[ -f settings.json ]]; then
  pass "settings.json 존재"
else
  die "settings.json 없음"
fi

echo
echo "=== 결과: OK=$ok  WARN=$warn  FAIL=$fail ==="
if (( fail > 0 )); then
  echo "막히는 항목을 고친 뒤 다시 ./scripts/doctor.sh"
  exit 1
fi
if (( warn > 0 )); then
  echo "경고만 있으면 콘솔 개발은 가능합니다. 키는 다른 컴/.env 또는 Railway Variables 에서 복사하세요."
  exit 0
fi
echo "개발 준비 완료."
