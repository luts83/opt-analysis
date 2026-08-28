# HANDOFF — 다른 컴/다른 날에도 이어가기

> 세션을 마치거나 큰 작업을 끝낼 때 이 파일을 2~5줄만 갱신하세요.
> 코드·스냅샷은 git, 시크릿은 `.env`(로컬) / Railway(운영)로 동기화합니다.

## 새 컴퓨터 체크리스트

```bash
git clone <repo> && cd opt-analysis   # 또는 기존 클론에서 git pull
./scripts/setup.sh
# .env 에 키 붙여넣기 (아래 "시크릿")
./scripts/doctor.sh
make report-preview                   # 스모크
```

## 시크릿 (git에 없음)

| 키 | 어디서 가져오나 |
|---|---|
| `OPENAI_API_KEY` | 다른 컴 `.env` 또는 Railway Variables |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 동일 |
| `EMAIL_*` | 이메일 쓸 때만 (텔레그램이 주력) |

복사 후 `./scripts/doctor.sh`로 확인.

## 현재 상태 (2026-08-15)

- 일일 리포트 v2: 한눈에→어제옵션→오늘주가(화살표)→반응가격→옵션변화→🟢🟡⚪해석→검증→누적(간소).
- 목표: 예측 문장 생산이 아니라 어제옵션→오늘옵션→오늘주가→패턴기록→과거비교.
- LLM은 초보자 2~3줄(`generate_experiment_blurb`)만. 본문 골격은 규칙 조립.
- 과적합 가드(`pattern_store`): 최소표본 전엔 예측 가중치 금지.

## 다음에 할 일 (후보)

- [ ] `/report IREN` 또는 `make report-preview`로 문구 확인 후 커밋·푸시
- [ ] Railway에 weekly 서비스가 남아 있으면 Actions와 이중 발송 여부 확인
- [x] 메일 발송 끄기(`settings.json` email.enabled: false) + Actions `--no-email`
- [x] 일일 Actions 이중 cron(05:07+05:37) 제거 → 05:07 1회, 발송은 Railway 봇만

## 방금 한 일 / 주의

- `insights.py` → `report_flow.assemble_experiment_report` 고정. 예측형 섹션 순서 폐기.
- 주간 이중 cron 수정은 이미 반영됨 (`weekly-report.yml`).
