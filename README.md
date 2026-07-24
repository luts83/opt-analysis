# IREN Options Report — Phase 0 (로컬 프로토타입)

옵션 체인 데이터를 수집해 지표를 계산하고, 콘솔에 텍스트 리포트를 출력하는 스크립트.
지금은 **Phase 0(로컬 검증)** 단계로, 스케줄러/이메일 발송은 포함하지 않는다.

## 목적

실제로 계산한 숫자(현재가, OI, 볼륨, 예상 밴드 등)가 브라우저(Finviz 등)에서 보던 값과
맞는지 사람이 눈으로 검증하는 것.

## 요구 환경

- Python 3.11+
- 패키지: `yfinance`, `pandas`

## 설치 (가상환경 권장)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## API 키 / 이메일 설정 (.env)

`.env` 에 키/인증정보를 넣는다. (`.env` 는 git 에 커밋되지 않음)

```bash
cp .env.example .env
```

- `OPENAI_API_KEY` — ChatGPT 자연어 해설용. 없으면 규칙 기반으로 폴백.
- `EMAIL_SENDER` / `EMAIL_APP_PASSWORD` / `EMAIL_RECIPIENTS` — 이메일 발송용.

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

## 배포 (GitHub Actions)

`.github/workflows/daily-report.yml` 가 평일마다 자동 실행되어 리포트를 만들고
오늘 스냅샷을 repo 에 커밋한다.

1. GitHub 에 (private) repo 생성 후 이 코드를 push
2. repo → Settings → Secrets and variables → Actions 에 시크릿 등록:
   - `OPENAI_API_KEY`
   - `EMAIL_SENDER` (발신 Gmail)
   - `EMAIL_APP_PASSWORD` (Gmail 앱 비밀번호)
   - `EMAIL_RECIPIENTS` (수신자, 쉼표 구분)
3. Actions 탭 → "Daily Options Report" → Run workflow 로 수동 테스트
4. 이후 스케줄(영국 `Europe/London` 화~토 06:07 / 백업 06:37)로 자동 실행 + 이메일 발송
5. **중요:** GitHub 내장 `schedule` 은 스킵되는 경우가 있어, 안정적으로 쓰려면 아래
   **외부 cron → workflow_dispatch** 를 권장한다.

### 안정적인 자동 발송 (권장: cron-job.org)

GitHub Actions 탭에 `schedule` 실행이 안 뜨면(수동만 성공), 외부에서 깨우면 된다.

1. GitHub → Settings → Developer settings → **Fine-grained personal access token** 생성
   - Repository access: `opt-analysis` only
   - Permissions: **Actions = Read and write**, **Contents = Read**
2. https://cron-job.org 가입 후 새 작업 생성:
   - URL: `https://api.github.com/repos/luts83/opt-analysis/actions/workflows/daily-report.yml/dispatches`
   - Method: **POST**
   - Schedule: 매일(또는 화~토) **06:07 Europe/London**
   - Headers:
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer <방금_만든_PAT>`
     - `X-GitHub-Api-Version: 2022-11-28`
   - Body (JSON): `{"ref":"main"}`
3. 로컬에서 바로 테스트:
   ```bash
   export GH_TOKEN=github_pat_...
   chmod +x scripts/trigger_daily.sh
   ./scripts/trigger_daily.sh
   ```

주간 검증은 `.github/workflows/weekly-report.yml` (토 아침) + 같은 Secrets.
로컬 테스트: `python main.py`  (발송 없이: `python main.py --no-email`)

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

## 다음 단계 (Phase 1, 이번 스코프 아님)

GitHub Actions cron, 이메일(SMTP) 발송, (선택) ChatGPT 해설.
