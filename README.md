# IREN Options Report

옵션 체인 수집 → 지표/인사이트 → 일일·주간 리포트 → 텔레그램(Railway 봇) 발송.

이어하기·에이전트 맥락: [`HANDOFF.md`](HANDOFF.md), [`AGENTS.md`](AGENTS.md)

## 새 컴퓨터에서 시작 (권장)

```bash
git pull                          # 또는 clone
./scripts/setup.sh                # venv + deps + .env 템플릿
# 다른 컴의 .env 또는 Railway Variables 에서 키 붙여넣기
./scripts/doctor.sh               # 막히는 항목 점검
make report-preview               # 스모크 테스트
```

공통 명령: `make help` (`setup`, `doctor`, `report`, `bot`, `weekly`, `test` …)

## 목적

계산한 숫자(현재가, OI, 볼륨, 예상 밴드 등)를 Finviz 등과 눈으로 검증하고,
일일/주간 리포트와 자기학습 피드백을 운영한다.

## 요구 환경

- Python 3.11+ (Dockerfile / `.python-version` 기준 3.11)
- `requirements.txt` (yfinance, pandas, openai, …)

## 설치 (수동)

`./scripts/setup.sh` 를 쓸 수 없으면:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## API 키 / 이메일 설정 (.env)

`.env` 에 키/인증정보를 넣는다. (`.env` 는 git 에 커밋되지 않음 — **컴마다 한 번** 복사)

- `OPENAI_API_KEY` — ChatGPT 자연어 해설용. 없으면 규칙 기반 폴백.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — 봇·발송 (운영 주력)
- `EMAIL_SENDER` / `EMAIL_APP_PASSWORD` / `EMAIL_RECIPIENTS` — 이메일(선택)

### Gmail 앱 비밀번호 발급

1. Google 계정 2단계 인증(2FA) 활성화
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 생성(16자리)
3. 그 값을 `EMAIL_APP_PASSWORD` 에 넣는다 (일반 로그인 비번 아님!)

## 실행

```bash
python main.py                   # 수집 + 스냅샷 저장 + 리포트(+AI 해설) 출력
python main.py --no-save         # 저장 없이 미리보기
python main.py --ticker IREN     # 특정 종목만
```

## 주간 검증 리포트 (백테스트)

그 주 월요일(그 주 첫 스냅샷) 예측을 **실제 주간 OHLC** 와 비교해 채점한다.
항목별 정확도(밴드/저항/지지/방향) + 종합 성적(A~F, 100점) + 최근 4주 추이 + AI 해설.

```bash
python weekly.py                 # 오늘이 속한 주 검증 + 스냅샷 저장 + 이메일
python weekly.py --date 2026-07-24   # 해당 날짜가 속한 주 검증
python weekly.py --no-email --no-save # 미리보기
```

채점 방식(요약):

| 항목 | 채점 | 가중치 |
|---|---|---|
| 밴드(범위) | 실제 고/저가가 예상 밴드 안이면 100, 이탈폭만큼 감점 | 35% |
| 방향 | 예상 심리(강세/약세) vs 실제 주간 수익률 부호 일치 | 30% |
| 저항선 | 실제 고가가 예상 저항에 가까울수록 고득점 | 17.5% |
| 지지선 | 실제 저가가 예상 지지에 가까울수록 고득점 | 17.5% |

주간 스냅샷은 `snapshots/<TICKER>/weekly/<주말금요일>.json` 에 쌓인다.

## 배포 (Railway — 권장)

**서비스는 하나만** 있으면 됩니다. (`python bot.py` 상시 실행)

- `/report` 수동 요청
- 화~토 아침 자동 발송 (봇 안 스케줄, UTC 05:07 ≈ 영국 여름 06:07)

### 설정

1. https://railway.com → Deploy from GitHub → `opt-analysis`
2. **Start Command**: `python bot.py`
3. **Cron Schedule: 비움** (예전에 cron 걸었으면 삭제)
4. **Variables**:
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
   - `SNAPSHOTS_DIR=/data/snapshots`
5. **Volume** mount `/data`
6. Deploy 후 텔레그램에 `✅ 리포트 봇 준비됨` 이 오면 성공  
   → 그다음 `/report` 또는 `/help` 테스트

| 명령 | 동작 |
|---|---|
| `/report` | 전체 종목 |
| `/report IREN` | 특정 종목만 |
| `/help` | 안내 |

`TELEGRAM_CHAT_ID` 채팅에서만 동작합니다.

로컬: `python bot.py`

### 주간 리포트 (선택)

나중에 필요하면 서비스/ cron 을 추가하거나, 봇에 `/weekly` 를 붙이면 됩니다.
참고: `railway.weekly.toml`, `railway.bot.toml`(구버전 분리 배포용)

## 단위 테스트

```bash
python test_expiry_selector.py   # 또는: pytest test_expiry_selector.py
```

## 폴더 구조

```
settings.json        # 티커 리스트 + 임계값 + LLM 설정 (종목 추가는 여기 tickers 에)
config.py            # settings.json / .env 로딩
data_fetch.py        # yfinance 로 옵션 체인 + 현재가 수집
expiry_selector.py   # 만기일 선택 (이번주/다음주/월간)
metrics.py           # V/OI, straddle band, OI 밀집, 이상신호(anomalies) 등
insights.py          # 규칙 기반 인사이트 + AI 해설 오케스트레이션
llm.py               # ChatGPT(OpenAI) 자연어 해설 (일일/주간, .env 키 사용, 실패 시 폴백)
snapshot_store.py    # 스냅샷 JSON 저장/로드 (일일 + 주간)
report_builder.py    # 텍스트 리포트 조립
main.py              # 일일 리포트 엔트리포인트
weekly_metrics.py    # 주간 검증 채점(밴드/저항/지지/방향 정확도 + 종합 성적)
weekly.py            # 주간 검증 리포트 엔트리포인트
snapshots/IREN/      # 날짜별 JSON 스냅샷 (+ weekly/ 주간 스냅샷)
.github/workflows/   # GitHub Actions (일일 매평일 + 주간 금요일)
```

## 지표 정의 (요약)

| 지표 | 정의 |
|---|---|
| V/OI | volume / open_interest (분류: <1 조용함 / 1~2 활발 / 2~5 뜨거움 / 5+ 극단) |
| OI 변화율 | (오늘 OI − 어제 OI) / 어제 OI (+100%↑ 신규유입 / −90%↓ 청산) |
| Volume 이상 | 오늘 거래량 ≥ 최근 이력 평균 × 3 |
| OI 밀집 | 만기별 OI 상위 strike (콜=저항, 풋=지지) |
| Straddle 밴드 | 현재가 ± (ATM 콜 lastPrice + ATM 풋 lastPrice) |
| 콜/풋 볼륨비 | 전체 콜 볼륨 합 / 전체 풋 볼륨 합 → 심리(강세/중립/약세) |

## 만기 선택 규칙

- 이번주 = 정렬된 만기 리스트의 1번째, 다음주 = 2번째
- 월간 = "그 달의 세 번째 금요일 날짜"와 정확히 일치하며 다음주보다 뒤인 첫 만기
  (없으면 다음 달 세 번째 금요일로)

## 크로스머신 개발

| 동기화 | 방법 |
|---|---|
| 코드·스냅샷·핸드오프 | `git push` / `git pull` |
| 시크릿 | `.env` 수동 복사 또는 Railway Variables |
| AI 맥락 | `AGENTS.md`, `HANDOFF.md`, `.cursor/rules/` (저장소에 포함) |

세션 끝: `HANDOFF.md`에 "현재 상태 / 다음에 할 일"을 짧게 적어두면 다음 컴에서 바로 이어감.
