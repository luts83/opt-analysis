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
4. 이후 cron(기본 22:00 UTC = 아침 7시 KST, 평일)에 맞춰 자동 실행 + 이메일 발송

로컬에서 이메일까지 테스트: `python main.py`  (발송 없이: `python main.py --no-email`)

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
llm.py               # ChatGPT(OpenAI) 자연어 해설 (.env 키 사용, 실패 시 폴백)
snapshot_store.py    # 스냅샷 JSON 저장/로드
report_builder.py    # 텍스트 리포트 조립
main.py              # 엔트리포인트
snapshots/IREN/      # 날짜별 JSON 스냅샷이 쌓임
.github/workflows/   # GitHub Actions (매일 자동 실행)
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
