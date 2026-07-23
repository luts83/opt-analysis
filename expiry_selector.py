"""만기일 선택 로직 (이번주 위클리 / 다음주 위클리 / 월간).

규칙:
1. Ticker(ticker).options 는 만기일 문자열이 오름차순 정렬된 튜플.
2. this_week = 정렬된 리스트의 1번째 값 (index 0)
3. next_week = 정렬된 리스트의 2번째 값 (index 1)
4. monthly  = "해당 월의 세 번째 금요일" 날짜와 정확히 일치하면서,
              next_week 보다 뒤에 있는 첫 번째 만기일.
              (요일 체크가 아니라, 계산된 3번째 금요일 '날짜'와 정확히 같은지 비교)
5. 중복 방지: 계산된 월간이 this_week/next_week 와 같으면 다음 달 3번째 금요일로.
              (next_week 보다 뒤라는 조건으로 자연히 보장되지만 명시적으로도 처리)
"""
from __future__ import annotations

import datetime as dt


def third_friday(year: int, month: int) -> dt.date:
    """해당 연/월의 세 번째 금요일 날짜를 반환한다.

    계산: 그 달 1일 → 첫 번째 금요일 → +14일.
    """
    first = dt.date(year, month, 1)
    # weekday(): 월=0 ... 금=4 ... 일=6
    offset = (4 - first.weekday()) % 7
    first_friday = first + dt.timedelta(days=offset)
    return first_friday + dt.timedelta(days=14)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def select_from_expiries(expiries: list[str]) -> dict:
    """만기일 문자열 리스트(오름차순)에서 3개 만기를 선택한다.

    네트워크 없이 순수 로직만 담당 → 단위 테스트 대상.
    """
    if len(expiries) < 2:
        raise ValueError("만기일이 2개 미만이라 선택할 수 없습니다.")

    exp_sorted = sorted(expiries)
    exp_set = set(exp_sorted)

    this_week = exp_sorted[0]
    next_week = exp_sorted[1]

    # 월간 후보 탐색: this_week 의 월부터 시작해 3번째 금요일을 차례로 검사
    start = dt.datetime.strptime(this_week, "%Y-%m-%d").date()
    year, month = start.year, start.month

    monthly: str | None = None
    for _ in range(15):  # 최대 15개월 앞까지 탐색 (안전장치)
        tf = third_friday(year, month).isoformat()
        # 조건: 실제 만기 목록에 정확히 존재 + next_week 보다 뒤
        #       (뒤라는 조건이 this_week/next_week 와의 중복을 자동 방지)
        if tf in exp_set and tf > next_week:
            monthly = tf
            break
        year, month = _next_month(year, month)

    if monthly is None:
        raise ValueError("조건에 맞는 월간 만기를 찾지 못했습니다.")

    return {"this_week": this_week, "next_week": next_week, "monthly": monthly}


def select_expiries(ticker: str) -> dict:
    """yfinance 에서 만기 목록을 가져와 3개 만기를 선택한다.

    Returns:
        {"this_week": "YYYY-MM-DD", "next_week": "YYYY-MM-DD", "monthly": "YYYY-MM-DD"}
    """
    import yfinance as yf

    options = yf.Ticker(ticker).options
    if not options:
        raise ValueError(f"{ticker}: 옵션 만기 목록이 비어 있습니다.")
    return select_from_expiries(list(options))
