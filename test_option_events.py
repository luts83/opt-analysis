"""옵션↔주가 학습 파이프라인 테스트."""
from __future__ import annotations

import tempfile
from pathlib import Path

import option_events as oe


def test_near_volume_detected_far_ignored():
    snap = {
        "ticker": "IREN",
        "date": "2026-09-02",
        "spot": 36.8,
        "metrics": {
            "top_call_volume": [
                {"strike": 37.0, "volume": 5000, "expiry": "2026-09-04", "oi": 1000},
                {"strike": 50.0, "volume": 20000, "expiry": "2026-09-18", "oi": 5000},
            ],
            "top_put_volume": [],
            "top_voi": [],
        },
        "day_over_day": {"volume_mult": 1.8},
        "volume_anomaly": {"is_anomaly": False, "mult": 1.8},
    }
    ev = oe.detect_events_from_snap(snap, focus_pct=0.05)
    strikes = {round(e["strike"], 2) for e in ev}
    assert 37.0 in strikes
    assert 50.0 not in strikes


def test_quiet_when_no_prev_events():
    today = {
        "ticker": "IREN",
        "date": "2026-09-03",
        "spot": 39.0,
        "metrics": {"top_call_volume": [], "top_put_volume": [], "top_voi": []},
        "day_over_day": {"volume_mult": 1.0},
        "volume_anomaly": {},
    }
    out = oe.build_report_mode(
        ticker="IREN",
        date="2026-09-03",
        prev_snap=None,
        today_snap=today,
        today_ohlc={"high": 39.5, "low": 38.0, "close": 39.0, "return_pct": 0.5},
        dod={"volume_mult": 1.0},
        vol_anom={},
        save=False,
    )
    assert out["mode"] in ("quiet", "skip")
    assert "조용함" in out["body"] or out["mode"] == "skip"


def test_classify_breakout_fail_and_success():
    # 돌파 실패
    fail = oe.classify_relation(
        370.0, "CALL", {"high": 372.0, "low": 355.0, "close": 357.0, "open": 360.0}
    )
    assert fail["result"] == "breakout_fail"
    # 돌파 성공
    ok = oe.classify_relation(
        370.0, "CALL", {"high": 375.0, "low": 368.0, "close": 372.0, "open": 369.0}
    )
    assert ok["result"] == "breakout_success"
    # 미도달
    miss = oe.classify_relation(
        50.0, "CALL", {"high": 39.5, "low": 36.0, "close": 39.0}
    )
    assert miss["result"] == "not_reached"
    # 지지 성공
    sup = oe.classify_relation(
        35.0, "PUT", {"high": 39.0, "low": 34.9, "close": 36.5, "open": 37.0}
    )
    assert sup["result"] == "support_success"


def test_learning_report_has_insight_not_connect_rate():
    prev = {
        "ticker": "TSLA",
        "date": "2026-09-02",
        "spot": 360.0,
        "metrics": {
            "top_call_volume": [
                {"strike": 370.0, "volume": 12000, "expiry": "2026-09-05", "oi": 2000},
            ],
            "top_put_volume": [],
            "top_voi": [
                {
                    "strike": 370.0,
                    "type": "CALL",
                    "voi": 25.0,
                    "volume": 5000,
                    "oi": 200,
                    "expiry": "2026-09-05",
                }
            ],
        },
        "day_over_day": {"volume_mult": 2.0},
        "volume_anomaly": {"is_anomaly": True, "mult": 2.0},
    }
    today = {
        "ticker": "TSLA",
        "date": "2026-09-03",
        "spot": 357.0,
        "previous_close": 360.0,
        "metrics": {"top_call_volume": [], "top_put_volume": [], "top_voi": []},
        "day_over_day": {"volume_mult": 1.0},
        "volume_anomaly": {},
    }
    ohlc = {
        "open": 360.0,
        "high": 372.0,
        "low": 355.0,
        "close": 357.0,
        "return_pct": -0.8,
    }
    out = oe.build_report_mode(
        ticker="TSLA",
        date="2026-09-03",
        prev_snap=prev,
        today_snap=today,
        today_ohlc=ohlc,
        dod={"volume_mult": 1.0},
        vol_anom={},
        save=False,
    )
    assert out["mode"] == "event"
    body = out["body"]
    assert "연결률" not in body
    assert "오늘 확인된 것" in body
    assert "과거 동일 패턴" in body
    assert "돌파 실패" in body or "접근 후 반전" in body
    # 인사이트: 콜 많음 ≠ 학습 결론의 핵심
    assert "저항" in body or "돌파" in body


def test_day_score_prev_events_force_event_day():
    prev = [{
        "score": 40, "strike": 38, "event_type": "near_volume", "volume": 9000,
    }]
    info = oe.day_event_score(
        prev_events=prev,
        today_events=[],
        dod={"volume_mult": 1.0},
        vol_anom={},
    )
    assert info["is_event_day"] is True
    weak = oe.day_event_score(
        prev_events=[{
            "score": 40, "strike": 38, "event_type": "voi_extreme", "voi": 6, "volume": 100,
        }],
        today_events=[],
        dod={"volume_mult": 1.0},
        vol_anom={},
    )
    assert weak["is_event_day"] is False


def test_resolve_saves_new_labels(tmp_path: Path | None = None):
    import tempfile as tf

    d = Path(tf.mkdtemp()) if tmp_path is None else tmp_path
    path = d / "option_events.jsonl"
    prev_events = [
        {
            "ticker": "IREN",
            "asof": "2026-09-02",
            "strike": 38.0,
            "side": "CALL",
            "dist_pct": 2.0,
            "event_type": "near_volume",
            "volume": 9000,
            "detail": "콜 거래 9000",
            "score": 40,
        }
    ]
    ohlc = {"high": 39.5, "low": 36.0, "close": 39.0, "return_pct": 5.0}
    resolved = oe.resolve_prev_events(
        "IREN", prev_events, ohlc, outcome_date="2026-09-03", save=False
    )
    assert resolved[0]["result"] == "breakout_success"
    oe.append_event_records(resolved, path=path)
    recs = oe.load_event_records("IREN", path=path)
    assert len(recs) == 1


if __name__ == "__main__":
    tests = [
        test_near_volume_detected_far_ignored,
        test_quiet_when_no_prev_events,
        test_classify_breakout_fail_and_success,
        test_learning_report_has_insight_not_connect_rate,
        test_day_score_prev_events_force_event_day,
        test_resolve_saves_new_labels,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}개 테스트 통과")
