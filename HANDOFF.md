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
| `ORTEX_API_KEY` | 선택 · Borrow Fee 연동 시 |

## 현재 상태 (2026-09-23)

- **옵션↔주가 상관 실험** (`option_events.py`): 근거리(±5%) 강한 이벤트만 → t+1 검증.
- Quiet 게이트: `report.quiet_mode=one_line` — 이벤트 없으면 1줄. `skip`으로 미발송 가능.
- 설정: `event_min_score`, `focus_pct` in `settings.json`.
- 저장: `snapshots/_learning/option_events.jsonl`

## 다음에 할 일

1. 배포 후 1주 quiet/event 비율·연결률 관찰
2. 베이스라인(비이벤트 근거리 랜덤) 대비 연결률 (Phase D 보강)
3. Borrow Fee(ORTEX) 선택 연동

## 방금 한 일 / 주의

- 구형 장문 stock_report는 일일 본문에서 분리. main은 event/quiet 본문만 발송.
- 미리보기: `make report-preview` / `python test_option_events.py`
