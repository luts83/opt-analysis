"""옵션 이벤트 → t+1 주가 상관 단위 테스트."""
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
    assert 50.0 not in strikes  # 원거리 제외


def test_quiet_when_no_prev_events():
    today = {
        "ticker": "IREN",
        "date": "2026-09-03",
        "spot": 39.0,
        "metrics": {"top_call_volume": [], "top_put_volume": [], "top_voi": []},
        "day_over_day": {"volume_mult": 1.0},
        "volume_anomaly": {},
    }
    with tempfile.TemporaryDirectory() as d:
        # isolate store
        p = Path(d) / "option_events.jsonl"
        # monkeypatch path via resolve with empty prev
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


def test_outcome_hit_and_stats(tmp_path: Path | None = None):
    import tempfile as tf

    d = Path(tf.mkdtemp()) if tmp_path is None else tmp_path
    path = d / "option_events.jsonl"
    # temporarily write via append with path
    prev_events = [
        {
            "ticker": "IREN",
            "asof": "2026-09-02",
            "strike": 38.0,
            "side": "CALL",
            "dist_pct": 2.0,
            "event_type": "near_volume",
            "detail": "콜 거래 5000",
            "score": 40,
        }
    ]
    ohlc = {"high": 39.5, "low": 36.0, "close": 39.0, "return_pct": 5.0}
    resolved = oe.resolve_prev_events(
        "IREN", prev_events, ohlc, outcome_date="2026-09-03", save=False
    )
    assert resolved[0]["result"] == "hit"
    oe.append_event_records(resolved, path=path)
    # load from custom path
    recs = oe.load_event_records("IREN", path=path)
    assert len(recs) == 1
    # correlation_stats uses default path — test judge only here
    out = oe.judge_strike_outcome(50.0, "CALL", ohlc)
    assert out["result"] == "unverified"


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
    # 약한 V/OI만 → quiet
    weak = oe.day_event_score(
        prev_events=[{
            "score": 40, "strike": 38, "event_type": "voi_extreme", "voi": 6, "volume": 100,
        }],
        today_events=[],
        dod={"volume_mult": 1.0},
        vol_anom={},
    )
    assert weak["is_event_day"] is False
    mid = oe.day_event_score(
        prev_events=[{
            "score": 40, "strike": 38, "event_type": "near_volume", "volume": 4000,
        }],
        today_events=[],
        dod={"volume_mult": 1.0},
        vol_anom={},
    )
    assert mid["is_event_day"] is False


if __name__ == "__main__":
    test_near_volume_detected_far_ignored()
    print("  PASS  test_near_volume_detected_far_ignored")
    test_quiet_when_no_prev_events()
    print("  PASS  test_quiet_when_no_prev_events")
    test_outcome_hit_and_stats()
    print("  PASS  test_outcome_hit_and_stats")
    test_day_score_prev_events_force_event_day()
    print("  PASS  test_day_score_prev_events_force_event_day")
    print("\n4개 테스트 통과")
