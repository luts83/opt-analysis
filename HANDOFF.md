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

## 현재 상태 (2026-08-13)

- 일일 리포트 해석 개편 + 과적합 가드: 단일 사례는 학습 후보만, 최소 8표본·적중률·최근가중 상한 후에만 확률 소폭 가산. IREN $50을 목표가로 배우지 않음.
- 테스트: `python test_expiry_selector.py` + `python test_price_levels.py` (IREN 8/12→8/13 $49.19 케이스).
- Railway 재배포는 커밋·푸시 후.

## 다음에 할 일 (후보)

- [ ] 실제 `/report IREN` 로 문구 확인 후 커밋
- [ ] 메일 발송 끄기(`settings.json` email.enabled)는 아직 미적용

## 방금 한 일 / 주의

- `price_levels.py` 신규. 리포트 순서: 쉽게말하면 → 가격 → 신호 → 지도 → 시나리오 → 근거.
