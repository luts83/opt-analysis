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

## 현재 상태 (2026-08-07)

- `main` HEAD: 일일 스냅샷 커밋까지 반영된 상태. 워킹트리 클린 기준.
- 동작 중인 기능: 일일 리포트, AI 해설, 초보자용 핵심 3가지, 근거·학습 강제, 텔레그램 `/report` 봇, 주간 검증, Railway 배포.
- 로컬 마찰 제거: `scripts/setup.sh`, `scripts/doctor.sh`, `Makefile`, `AGENTS.md`, 이 파일.

## 다음에 할 일 (후보)

- [ ] (비어 있음 — 작업 시작하면 여기 체크/메모)
- 예: `/weekly` 텔레그램 명령, README Phase 문구 정리, 종목 추가 등

## 방금 한 일 / 주의

- (세션 끝낼 때 짧게 적기)
- 예: "리포트 X 섹션 수정 중, 미커밋" / "Railway 재배포 필요"
