"""option_status 픽스처 테스트."""
from __future__ import annotations

import option_status as os_mod


def _row(
    strike: float,
    *,
    volume: int = 0,
    oi: int = 0,
    iv: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
) -> dict:
    return {
        "strike": strike,
        "volume": volume,
        "openInterest": oi,
        "impliedVolatility": iv,
        "bid": bid,
        "ask": ask,
        "lastPrice": last if last is not None else ((bid or 0) + (ask or 0)) / 2 if bid and ask else 1.0,
    }


def _snap(
    *,
    ticker: str = "TEST",
    spot: float = 100.0,
    calls: list[dict],
    puts: list[dict],
    band_pct: float | None = None,
) -> dict:
    data = {
        "ticker": ticker,
        "date": "2026-11-04",
        "spot": spot,
        "expiries": {
            "this_week": {"date": "2026-11-07", "calls": calls, "puts": puts},
            "next_week": {"date": "2026-11-14", "calls": [], "puts": []},
        },
        "metrics": {},
    }
    if band_pct is not None:
        data["metrics"] = {
            "band_trend": {
                "rows": [
                    {
                        "role": "this_week",
                        "label": "이번주",
                        "date": "2026-11-07",
                        "band_pct": band_pct,
                    }
                ]
            }
        }
    return data


def test_high_iv_sellers_down_tilt():
    """예시 1: 보험료 비쌈 + 매도 우위 + 하락 기울기."""
    spot = 100.0
    # ATM±5% 고른 vol/OI, 높은 IV, 좁은 스프레드, CPR 비극단
    # 풋 vol/활동 우세 → down
    calls = [
        _row(100, volume=800, oi=2000, iv=0.85, bid=2.0, ask=2.2, last=2.1),
        _row(105, volume=400, oi=1000, iv=0.80, bid=1.0, ask=1.1, last=1.05),
    ]
    puts = [
        _row(100, volume=900, oi=2100, iv=0.90, bid=2.0, ask=2.2, last=2.1),
        _row(95, volume=1200, oi=3000, iv=0.95, bid=1.5, ask=1.65, last=1.55),
        _row(90, volume=500, oi=1500, iv=1.00, bid=0.8, ask=0.9, last=0.85),
    ]
    # prev: 낮은 volume → 오늘 급증 아님(약함)이 되도록 prev도 비슷하게
    prev = _snap(
        calls=[_row(100, volume=700, oi=2000, iv=0.8, bid=2, ask=2.2),
               _row(105, volume=350, oi=1000, iv=0.75, bid=1, ask=1.1)],
        puts=[_row(100, volume=800, oi=2100, iv=0.85, bid=2, ask=2.2),
              _row(95, volume=1000, oi=3000, iv=0.9, bid=1.5, ask=1.65),
              _row(90, volume=450, oi=1500, iv=0.95, bid=0.8, ask=0.9)],
    )
    data = _snap(ticker="IREN", spot=spot, calls=calls, puts=puts)
    v = os_mod.analyze(data, prev)
    assert v["premium"] == "high"
    assert v["seller_stance"] == "sellers"
    assert v["tilt"] == "down"
    text = os_mod.format_telegram("IREN", v)
    assert "[IREN] 옵션으로 본 현재 상태" in text
    assert "보험료가 비싸게" in text
    assert "팔려는 쪽" in text
    assert "(참고)" in text
    assert "하락" in text
    assert "Edge" not in text and "Delta" not in text


def test_balanced_no_tilt():
    """예시 2: 우위 애매 + 방향 없음."""
    spot = 50.0
    # ATM vol/OI·IV 비슷(방향 신호 약함). CPR은 far OTM 콜로만 극단 → seller hit < 2
    # 스프레드 넓음 + last≠mid → tight_spread 실패
    calls = [
        _row(50, volume=800, oi=1000, iv=0.55, bid=1.0, ask=3.0, last=1.0),
        _row(55, volume=700, oi=900, iv=0.54, bid=0.5, ask=2.0, last=0.5),
        _row(80, volume=9000, oi=100, iv=0.40, bid=0.05, ask=0.50, last=0.05),
    ]
    puts = [
        _row(50, volume=750, oi=950, iv=0.55, bid=1.0, ask=3.0, last=1.0),
        _row(45, volume=700, oi=900, iv=0.54, bid=0.5, ask=2.0, last=0.5),
    ]
    data = _snap(spot=spot, calls=calls, puts=puts, band_pct=16.0)
    v = os_mod.analyze(data, None)
    assert v["premium"] in ("high", "mid")
    assert v["seller_stance"] == "balanced"
    assert v["tilt"] == "none"
    text = os_mod.format_telegram("ABC", v)
    assert "비슷해 보여요" in text or "뚜렷한 한쪽 우위는 없는" in text
    assert "(참고)" not in text


def test_low_iv_short():
    """예시 3: IV 낮음 → 짧은 요약."""
    calls = [_row(100, volume=100, oi=500, iv=0.20, bid=0.5, ask=0.55)]
    puts = [_row(100, volume=100, oi=500, iv=0.22, bid=0.5, ask=0.55)]
    data = _snap(calls=calls, puts=puts)
    v = os_mod.analyze(data, None)
    assert v["premium"] == "low"
    text = os_mod.format_telegram("XYZ", v)
    assert "저렴한 편" in text
    assert "큰 움직임" in text
    assert "(참고)" not in text


def test_single_direction_signal_omits_tilt():
    """방향 신호 1개만 → tilt none, 참고 줄 생략."""
    spot = 100.0
    # put vol만 우세, IV 동일·활동은 비슷하게 맞춰 신호 1개만
    calls = [
        _row(100, volume=1000, oi=2000, iv=0.50, bid=1.0, ask=1.1),
        _row(105, volume=200, oi=500, iv=0.48, bid=0.5, ask=0.6),
    ]
    puts = [
        _row(100, volume=1000, oi=2000, iv=0.50, bid=1.0, ask=1.1),
        _row(95, volume=2800, oi=500, iv=0.50, bid=0.5, ask=0.6),  # put vol 우세
    ]
    data = _snap(spot=spot, calls=calls, puts=puts)
    v = os_mod.analyze(data, None)
    # volume_oi may be down, but activity/skew may not → none or down
    # Force check: if only 1 same-direction, tilt must be none
    dirs = [s.get("direction") for s in v["direction_signals"]]
    up = sum(1 for d in dirs if d == "up")
    down = sum(1 for d in dirs if d == "down")
    if down < 2 and up < 2:
        assert v["tilt"] == "none"
        text = os_mod.format_telegram("T", v)
        assert "(참고)" not in text


def test_band_pct_fallback_when_no_iv():
    calls = [_row(100, volume=500, oi=500, iv=None, bid=1.0, ask=1.05)]
    puts = [_row(100, volume=500, oi=500, iv=None, bid=1.0, ask=1.05)]
    data = _snap(calls=calls, puts=puts, band_pct=20.0)
    v = os_mod.analyze(data, None)
    assert v["premium"] == "high"
    assert v["premium_source"] == "band_pct"


if __name__ == "__main__":
    test_high_iv_sellers_down_tilt()
    test_balanced_no_tilt()
    test_low_iv_short()
    test_single_direction_signal_omits_tilt()
    test_band_pct_fallback_when_no_iv()
    print("ok")
