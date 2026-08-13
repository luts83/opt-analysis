"""IREN 8/12 → 8/13($49.19) 사례 + 용어/확장 로직 테스트."""
from __future__ import annotations

import price_levels as pl
import report_evidence as ev
from events import news_is_relevant


def _iren_812_base() -> dict:
    """사용자 제공 IREN 8/12 스케치."""
    return {
        "oi_available": True,
        "oi_source": "실시간",
        "total_open_interest": 4840 + 8759,
        "call_put_volume_ratio": 2.3,
        "sentiment": "콜 거래 우세",
        "expiry_metrics": {
            "this_week": {
                "date": "2026-08-14",
                "straddle": {"lower": 41, "upper": 46, "band_pct": 11},
                "call_oi_clusters": [{"strike": 50, "oi": 4840}],
                "put_oi_clusters": [{"strike": 38, "oi": 8759}],
            }
        },
        "top_call_volume": [
            {"strike": 44, "volume": 13298, "oi": 1350, "voi": 9.858, "expiry": "2026-08-14", "type": "CALL"},
            {"strike": 43, "volume": 8000, "oi": 523, "voi": 15.291, "expiry": "2026-08-14", "type": "CALL"},
            {"strike": 45, "volume": 3000, "oi": 605, "voi": 4.965, "expiry": "2026-08-14", "type": "CALL"},
            {"strike": 50, "volume": 2500, "oi": 4840, "voi": 5.191, "expiry": "2026-08-14", "type": "CALL"},
        ],
        "top_put_volume": [
            {"strike": 42, "volume": 1200, "oi": 800, "voi": 1.5, "expiry": "2026-08-14", "type": "PUT"},
            {"strike": 38, "volume": 400, "oi": 8759, "voi": 0.05, "expiry": "2026-08-14", "type": "PUT"},
        ],
        "top_voi": [
            {"strike": 43, "type": "CALL", "voi": 15.291, "volume": 8000, "oi": 523},
            {"strike": 44, "type": "CALL", "voi": 9.858, "volume": 13298, "oi": 1350},
            {"strike": 50, "type": "CALL", "voi": 5.191, "volume": 2500, "oi": 4840},
            {"strike": 45, "type": "CALL", "voi": 4.965, "volume": 3000, "oi": 605},
        ],
    }


def test_iren_expansion_not_single_next_resist():
    spot = 43.67
    levels = pl.build_levels(_iren_812_base(), spot)
    exp = levels["expansion_up"]
    assert exp is not None
    assert exp["break_level"] == 44
    z0, z1 = exp["zone"]
    assert z0 == 45
    assert 46 <= z1 <= 48
    assert exp["magnet"] == 50
    assert "상단 확장" in exp["note"]
    assert "49" in exp["note"] or "50" in exp["note"]


def test_iren_near_price_outranks_far_oi():
    spot = 43.67
    levels = pl.build_levels(_iren_812_base(), spot)
    ranked = levels["ranked"]
    assert ranked
    top = ranked[0]["strike"]
    assert abs(top - 44) <= 1.0 or abs(top - 43) <= 1.0
    assert top != 50


def test_no_sell_language():
    spot = 43.67
    levels = pl.build_levels(_iren_812_base(), spot)
    blob = ev.levels_block(levels, spot) + ev.plain_talk_block(
        {"ticker": "IREN", "spot": spot, "date": "2026-08-12"},
        {"levels": levels, **_iren_812_base()},
    )
    assert "팔겠다" not in blob
    assert "사겠다" not in blob
    assert "강한 저항" not in blob or "확인" in blob


def test_band_not_used_as_ceiling():
    sig = pl.band_breakout_signal(_iren_812_base(), 49.19)
    assert sig is not None
    assert sig["side"] == "up"
    assert "확장" in sig["text"]


def test_role_flip_after_breakout():
    prev = {"spot": 43.67, "metrics": {"levels": {
        "near_resistance": [{"strike": 44, "volume": 13298}],
    }}}
    base = _iren_812_base()
    levels = pl.build_levels(
        base, 49.19, prev=prev, today_ohlc={"high": 49.19, "low": 44.5}
    )
    flipped = [x for x in levels.get("flipped_to_support") or [] if abs(x["strike"] - 44) < 0.05]
    assert flipped
    assert "지지 후보" in flipped[0]["kind"]


def test_low_confidence_no_strong_sr():
    base = _iren_812_base()
    base["oi_available"] = False
    base["oi_source"] = "데이터 없음"
    base["total_open_interest"] = 0
    levels = pl.build_levels(base, 20.26)
    assert levels["low_confidence"] is True
    assert levels["strong_resistance"] == []
    assert levels["expansion_up"] is None
    banner = ev.low_confidence_banner({"levels": levels, "low_confidence": True})
    assert "신뢰도 낮음" in banner


def test_news_relevance_filter():
    assert news_is_relevant({"title": "Klarna expands U.S. merchant reach"}, "KLAR")
    assert not news_is_relevant({"title": "Ellington Financial to Report Q2 Earnings"}, "KLAR")
    assert not news_is_relevant({"title": "Maximus MMS Q3 Earnings Beat Estimates"}, "KLAR")
    assert news_is_relevant({"title": "Iris Energy announces new bitcoin miners"}, "IREN")


def test_scenario_notes_expansion_as_candidate_not_top_rule():
    """확장 구간은 오늘 지도로 보여 주되, 단일 사례로 1순위 규칙이 되면 안 됨."""
    import events

    spot = 43.67
    base = _iren_812_base()
    base["levels"] = pl.build_levels(base, spot)
    nxt = events.next_session_scenarios(base, spot, data={"spot": spot, "market_session": "closed"})
    names = " ".join(s["name"] for s in nxt["scenarios"])
    assert "상승" in names
    assert "상단 확장" not in names or "학습 후보" in str(nxt)
    rise = next(s for s in nxt["scenarios"] if "상승" in s["name"])
    assert "학습 후보" in (rise.get("watch") or "") or rise.get("confidence_note") == "학습 후보"
    assert "45" in (rise.get("watch") or "")
    # 1순위가 확장 확정이면 안 됨
    assert "상단 확장" not in nxt["scenarios"][0]["name"]


def test_plain_talk_has_analogy_and_levels():
    spot = 43.67
    levels = pl.build_levels(_iren_812_base(), spot)
    text = ev.plain_talk_block(
        {"ticker": "IREN", "spot": spot, "date": "2026-08-12"},
        {"levels": levels, **_iren_812_base()},
    )
    assert text.startswith("💡 쉽게 말하면")
    assert "$44" in text or "44" in text
    assert "넓어" in text or "확장" in text


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
