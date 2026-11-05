# HANDOFF — 다른 컴/다른 날에도 이어가기

> 세션을 마치거나 큰 작업을 끝낼 때 이 파일을 2~5줄만 갱신하세요.

## 현재 상태 (2026-11-03)

- **일일 본문**: `option_insight.py` — ①어제 옵션→오늘 주가(해석·단정금지) ②현가 위·아래 분포+시나리오 ③과거 셋업. 레짐은 한 줄 보조.
- 숏배팅/인과 단정 금지. 콜≠매수. 주간 방향 리포트(`regime` 빈도) 유지.
- SPY `_market/SPY/`. LLM off.

## 다음에 할 일

1. 배포 후 텔레그램 톤·스팸 여부 확인
2. Volume 스냅으로 상관 표본 축적
3. Borrow Fee(ORTEX) 선택

## 명령

`python main.py --no-save --no-telegram` · `python test_option_insight.py` · `python test_option_events.py`
