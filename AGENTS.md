# AGENTS.md — Cursor / AI 에이전트용 프로젝트 지도

이 저장소는 **옵션 체인 기반 일일/주간 리포트** (티커: IREN, TSLA, SPCX).
배포는 Railway 상시 `python bot.py` + (백업) GitHub Actions.

## 새 세션 / 새 컴퓨터에서 할 일

1. `git pull`
2. `./scripts/setup.sh` (이미 되어 있으면 `./scripts/doctor.sh`)
3. `.env`에 시크릿이 없으면 **다른 컴의 `.env` 또는 Railway Variables**에서 복사
4. 이어서 할 일: [`HANDOFF.md`](HANDOFF.md) 확인
5. 세션 끝이나 큰 작업 후: `HANDOFF.md`의 "현재 상태 / 다음에 할 일"을 짧게 갱신

## 자주 쓰는 명령

| 명령 | 용도 |
|---|---|
| `make setup` / `make doctor` | 환경 구성 / 점검 |
| `make report-preview` | 콘솔 미리보기 (저장 X) |
| `make report` | 수집+스냅샷+발송 |
| `make bot` | 텔레그램 봇 (로컬) |
| `make weekly-preview` | 주간 검증 미리보기 |
| `make test` | `test_expiry_selector.py` |

## 아키텍처 (한 줄씩)

| 파일 | 역할 |
|---|---|
| `settings.json` | 티커·임계값·LLM/텔레그램 플래그 |
| `config.py` | settings + `.env` 로드 |
| `data_fetch.py` | yfinance 옵션/가격 |
| `expiry_selector.py` | 이번주/다음주/월간 만기 |
| `metrics.py` | V/OI, 밴드, OI 밀집, anomalies |
| `insights.py` / `llm.py` | 규칙 인사이트 + OpenAI 해설 |
| `learning.py` | 일일 예측 채점·자기학습 |
| `report_builder.py` / `report_evidence.py` / `report_polish.py` | 리포트 조립·근거·다듬기 |
| `snapshot_store.py` | `snapshots/<TICKER>/` JSON |
| `main.py` | 일일 엔트리 |
| `weekly.py` / `weekly_metrics.py` | 주간 검증 |
| `bot.py` / `telegram_notify.py` | 텔레그램 수동+스케줄 |
| `events.py` | 실적/뉴스 등 |

## 규칙

- **한국어**로 사용자와 대화
- `.env` / 시크릿 **절대 커밋 금지** (`.gitignore`에 `.env`)
- 스냅샷 JSON은 운영 데이터 — 불필요한 대량 리포맷 금지
- README의 "Phase 0 / 다음 단계"는 구식일 수 있음. **실제 기준은 이 파일 + HANDOFF.md**
- 프로덕션 스케줄: 봇 내장 (화~토 아침 UTC 05:07 근처). Railway Cron은 비움
- 최소 변경 원칙: 요청 범위만 수정, 무관한 리팩터/문서 남발 금지

## 시크릿

로컬: `.env` (`OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, 선택적 이메일)
운영: Railway Variables + Volume (`SNAPSHOTS_DIR=/data/snapshots`)
