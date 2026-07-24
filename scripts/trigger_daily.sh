#!/usr/bin/env bash
# GitHub Actions 일일 리포트를 workflow_dispatch 로 트리거한다.
# cron-job.org / 로컬 crontab 등에서 호출하면 GitHub 내장 schedule 없이도 안정적으로 돈다.
#
# 필요:
#   export GH_TOKEN=github_pat_...   # fine-grained PAT: Actions=Read and write, Contents=Read
#   ./scripts/trigger_daily.sh
set -euo pipefail

REPO="${GITHUB_REPO:-luts83/opt-analysis}"
WORKFLOW="${GITHUB_WORKFLOW:-daily-report.yml}"
REF="${GITHUB_REF:-main}"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN 환경변수가 필요합니다."
  echo "GitHub → Settings → Developer settings → Fine-grained tokens"
  echo "  Repository: ${REPO}"
  echo "  Permissions: Actions (Read and write), Contents (Read)"
  exit 1
fi

url="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"
echo "Triggering ${REPO} / ${WORKFLOW} @ ${REF} ..."
curl -fsS -X POST "$url" \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "{\"ref\":\"${REF}\"}"
echo
echo "OK — Actions 탭에서 실행이 시작됐는지 확인하세요."
