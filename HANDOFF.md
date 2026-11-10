# HANDOFF — 다른 컴/다른 날에도 이어가기

> 세션을 마치거나 큰 작업을 끝낼 때 이 파일을 2~5줄만 갱신하세요.

## 현재 상태 (2026-11-10)

- **일일 텔레그램**: `option_status.py` — 보험 비유 상태 요약 3~6줄만 (방향은 약한 기울기만 조건부).
- 상세 근거·수치·면책: 스냅샷 `option_status` (+ `option_insight`는 저장만).
- yfinance 체인 기준. IV 없으면 `band_pct` 폴백. LLM off.

## 다음에 할 일

1. 배포 후 텔레그램 톤 확인·임계값(`settings.json` `option_status`) 조정
2. Volume 스냅으로 상관 표본 축적
3. Borrow Fee(ORTEX) 선택

## 명령

`python main.py --no-save --no-telegram` · `python test_option_status.py` · `python test_option_insight.py`
