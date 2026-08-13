"""expiry_selector 단위 테스트.

pytest 로 실행: pytest test_expiry_selector.py
또는 직접 실행:  python test_expiry_selector.py
"""
from __future__ import annotations

import datetime as dt

from expiry_selector import select_from_expiries, third_friday


# 지시서에 주어진 예시 만기 리스트
SAMPLE_EXPIRIES = [
    "2026-07-24",  # this_week
    "2026-07-31",  # next_week
    "2026-08-07",
    "2026-08-14",
    "2026-08-21",  # ← 8월 세 번째 금요일 = monthly 여야 함
    "2026-08-28",
    "2026-09-18",  # 9월 세 번째 금요일
    "2026-10-16",
]


def test_third_friday_basic():
    assert third_friday(2026, 8) == dt.date(2026, 8, 21)
    assert third_friday(2026, 7) == dt.date(2026, 7, 17)
    assert third_friday(2026, 9) == dt.date(2026, 9, 18)


def test_select_sample():
    result = select_from_expiries(SAMPLE_EXPIRIES, as_of=dt.date(2026, 7, 20))
    assert result["this_week"] == "2026-07-24"
    assert result["next_week"] == "2026-07-31"
    assert result["monthly"] == "2026-08-21"
    assert "zero_dte" not in result


def test_monthly_skips_when_equal_to_weekly():
    """월간 후보가 위클리와 겹치면 다음 달로 넘어가야 한다.

    this_week=8/21(3번째 금), next_week=8/28 인 경우,
    8월 3번째 금요일(8/21)은 next_week 보다 앞이므로 스킵되고
    9월 3번째 금요일(9/18)이 선택돼야 한다.
    """
    expiries = ["2026-08-21", "2026-08-28", "2026-09-04", "2026-09-18", "2026-10-16"]
    result = select_from_expiries(expiries, as_of=dt.date(2026, 8, 20))
    assert result["this_week"] == "2026-08-21"
    assert result["next_week"] == "2026-08-28"
    assert result["monthly"] == "2026-09-18"


def test_zero_dte_skipped_from_this_week():
    """만기 당일은 this_week 가 아니라 zero_dte."""
    result = select_from_expiries(SAMPLE_EXPIRIES, as_of=dt.date(2026, 7, 24))
    assert result["zero_dte"] == "2026-07-24"
    assert result["this_week"] == "2026-07-31"
    assert result["next_week"] == "2026-08-07"


def test_past_expiries_skipped():
    result = select_from_expiries(SAMPLE_EXPIRIES, as_of=dt.date(2026, 8, 13))
    assert result["this_week"] == "2026-08-14"
    assert result["next_week"] == "2026-08-21"
    assert "zero_dte" not in result


def _run_all():
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed}개 테스트 통과")


if __name__ == "__main__":
    _run_all()
