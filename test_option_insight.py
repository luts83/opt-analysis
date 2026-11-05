"""옵션↔주가 인사이트 본문 테스트."""
from __future__ import annotations

import option_insight as oi


def test_interpret_put_down_not_short_bet():
    price = {"return_pct": -3.5, "available": True}
    primary = {
        "strike": 23.5,
        "side": "PUT",
        "result": "support_fail",
        "detail": "풋 거래 4000",
    }
    text = oi.interpret_overlap(price=price, primary=primary, prev_events=[primary])
    assert "단정 불가" in text
    assert "숏배팅으로 단정하지 않음" in text
    assert "하방" in text


def test_interpret_call_down_not_bullish():
    price = {"return_pct": -4.0, "available": True}
    primary = {"strike": 25.0, "side": "CALL", "result": "breakout_fail", "detail": "V/OI 12"}
    text = oi.interpret_overlap(price=price, primary=primary, prev_events=[primary])
    assert "콜≠매수" in text


def test_distribution_above_below():
    snap = {
        "spot": 100.0,
        "oi_stale_raw": False,
        "metrics": {
            "oi_available": True,
            "top_call_volume": [
                {"strike": 105.0, "volume": 5000},
                {"strike": 95.0, "volume": 1000},
            ],
            "top_put_volume": [
                {"strike": 95.0, "volume": 8000},
                {"strike": 110.0, "volume": 500},
            ],
            "top_voi": [],
            "levels": {},
        },
    }
    d = oi.build_distribution(snap, 100.0)
    assert d["available"]
    assert any(x["strike"] == 105.0 for x in d["above"])
    assert any(x["strike"] == 95.0 for x in d["below"])


def test_format_has_three_sections():
    today = {
        "date": "2026-11-02",
        "spot": 22.0,
        "regular_close": 22.0,
        "oi_stale_raw": True,
        "metrics": {
            "top_call_volume": [{"strike": 24.0, "volume": 3000}],
            "top_put_volume": [{"strike": 21.0, "volume": 5000}],
            "top_voi": [],
            "levels": {},
        },
    }
    prev = {
        "date": "2026-10-30",
        "spot": 24.0,
        "metrics": {
            "top_call_volume": [],
            "top_put_volume": [
                {"strike": 23.5, "volume": 9000, "expiry": "2026-11-07", "oi": 1000}
            ],
            "top_voi": [],
        },
        "day_over_day": {"volume_mult": 1.2},
        "volume_anomaly": {},
    }
    import option_events as oe

    oe_result = oe.build_report_mode(
        ticker="IREN",
        date="2026-11-02",
        prev_snap=prev,
        today_snap=today,
        today_ohlc={
            "open": 24.0,
            "high": 24.2,
            "low": 21.8,
            "close": 22.0,
            "return_pct": -8.3,
        },
        dod={"volume_mult": 1.0},
        vol_anom={},
        save=False,
    )
    body = oi.format_daily_insight(
        ticker="IREN",
        date="2026-11-02",
        today_snap=today,
        prev_snap=prev,
        oe_result=oe_result,
        regime={"available": True, "regime_ko": "급락중", "slope_pct": -40},
        spy_regime=None,
    )
    assert "① 어제 옵션판 → 오늘 주가" in body
    assert "② 지금 주가 기준 옵션 분포" in body
    assert "숏배팅으로 단정하지 않음" in body or "단정 불가" in body
    assert "시나리오" in body
    assert "투자 조언 아님" in body


if __name__ == "__main__":
    for t in [
        test_interpret_put_down_not_short_bet,
        test_interpret_call_down_not_bullish,
        test_distribution_above_below,
        test_format_has_three_sections,
    ]:
        t()
        print(f"  PASS  {t.__name__}")
    print("\n4개 테스트 통과")
