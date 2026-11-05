"""레짐·일일 브리프·주간 방향 테스트."""
from __future__ import annotations

import regime as r


def test_classify_crash_and_uptrend():
    crash = [40 + i * 0.4 for i in range(12)] + [45, 40, 35, 30, 26, 22]
    info = r.classify_regime(crash)
    assert info["available"]
    assert info["regime"] in ("crash", "downtrend")
    assert info["drawdown_pct"] < -10

    up = [100 + i * 1.2 for i in range(25)]
    u = r.classify_regime(up)
    assert u["regime"] == "uptrend"


def test_option_line_never_says_bullish_from_cpr():
    snap = {
        "spot": 50.0,
        "metrics": {
            "top_call_volume": [{"strike": 50.0, "volume": 9000}],
            "top_put_volume": [],
        },
        "day_over_day": {"volume_mult": 1.1},
        "volume_anomaly": {},
        "oi_stale_raw": False,
    }
    line = r.option_status_line(snap)
    assert "강세" not in line
    assert "매수" not in line or "방향≠매수" in line


def test_daily_brief_sideways_defers():
    flat = [100 + (i % 2) * 0.1 for i in range(25)]
    reg = r.classify_regime(flat)
    reg["day_return_pct"] = 0.2
    reg["option_line"] = "옵션: 특이 없음"
    reg["last"] = 100.1
    body = r.format_daily_brief("TSLA", "2026-09-10", reg)
    assert "일일 브리프" in body
    assert "주간 리포트 참고" in body or reg["regime"] != "sideways"


def test_empirical_forward_insufficient_without_history(tmp_path=None):
    out = r.empirical_forward(
        "NOSUCHTICKERXYZ",
        asof="2026-11-02",
        regime="downtrend",
        dd_bucket="dd20+",
    )
    assert out["available"] is False


def test_weekly_direction_structure():
    # 로컬 스냅이 있으면 실행, 없으면 skip 형태 확인
    from pathlib import Path

    if not Path("snapshots/IREN").exists():
        return
    text, meta = r.format_weekly_direction(
        "IREN", "2026-09-25", "2026-09-21", "2026-09-25"
    )
    assert "주간 방향" in text
    assert "① 이번 주 가격" in text
    assert "③ 방향 판단" in text
    assert "④ 무효화" in text
    assert "상승 확률" not in text


if __name__ == "__main__":
    tests = [
        test_classify_crash_and_uptrend,
        test_option_line_never_says_bullish_from_cpr,
        test_daily_brief_sideways_defers,
        test_empirical_forward_insufficient_without_history,
        test_weekly_direction_structure,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}개 테스트 통과")
