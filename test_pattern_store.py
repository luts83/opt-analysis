"""과적합 가드: 단일 사례는 학습 후보, 가중치 미반영."""
from __future__ import annotations

from pathlib import Path

import pattern_store as ps


def test_single_iren_obs_stays_candidate(tmp_path: Path | None = None):
    path = (tmp_path or Path(".")) / "patterns.json"
    if tmp_path is None:
        import tempfile

        d = Path(tempfile.mkdtemp())
        path = d / "patterns.json"
    st = ps.record_observation(
        ps.PATTERN_BREAKOUT_EXPAND,
        ticker="IREN",
        date="2026-08-13",
        prediction_date="2026-08-12",
        hit=True,
        setup={"break_level": 44, "actual_high": 49.19, "band_upper": 46},
        path=path,
    )
    assert st["status"] == "candidate"
    assert st["n"] == 1
    assert st["rank_delta"] == 0.0
    assert ps.rank_delta(ps.PATTERN_BREAKOUT_EXPAND, path) == 0.0
    blob = ps.format_candidates_block(path)
    assert "학습 후보" in blob
    assert "가중치 미반영" in blob or "뒤집지" in blob
    assert "$50" not in blob  # 목표가를 배우면 안 됨


def test_min_samples_needed_for_active():
    import tempfile

    path = Path(tempfile.mkdtemp()) / "patterns.json"
    for i in range(ps.MIN_SAMPLES):
        ps.record_observation(
            ps.PATTERN_BREAKOUT_EXPAND,
            ticker="T" + str(i % 3),
            date=f"2026-07-{i+1:02d}",
            prediction_date=f"2026-07-{i+1:02d}",
            hit=True,
            path=path,
        )
    st = ps.pattern_state(ps.PATTERN_BREAKOUT_EXPAND, path)
    assert st["n"] == ps.MIN_SAMPLES
    assert st["status"] == "active"
    assert st["rank_delta"] == ps.MAX_RANK_DELTA


def test_recency_cap_prevents_two_hits_dominating():
    import tempfile

    path = Path(tempfile.mkdtemp()) / "patterns.json"
    # 과거 8건 전부 실패 + 최근 2건 성공 → 최근 가중 상한으로 active 되면 안 됨
    for i in range(8):
        ps.record_observation(
            ps.PATTERN_BREAKOUT_EXPAND,
            ticker="OLD",
            date=f"2026-06-{i+1:02d}",
            prediction_date=f"2026-06-{i+1:02d}",
            hit=False,
            path=path,
        )
    ps.record_observation(
        ps.PATTERN_BREAKOUT_EXPAND, ticker="IREN", date="2026-08-12",
        prediction_date="2026-08-11", hit=True, path=path,
    )
    ps.record_observation(
        ps.PATTERN_BREAKOUT_EXPAND, ticker="IREN", date="2026-08-13",
        prediction_date="2026-08-12", hit=True, path=path,
    )
    st = ps.pattern_state(ps.PATTERN_BREAKOUT_EXPAND, path)
    assert st["n"] == 10
    assert st["hit_rate"] is not None
    assert st["hit_rate"] <= ps.RECENT_WEIGHT_CAP + 0.05  # 최근만 맞은 경우 상한 근처
    assert st["status"] == "candidate"  # 적중률 < 0.55


def test_observe_from_grade_records_setup_not_target():
    import tempfile

    path = Path(tempfile.mkdtemp()) / "patterns.json"
    prev = {
        "metrics": {
            "levels": {
                "expansion_up": {"break_level": 44.0, "zone": [45, 47], "magnet": 50},
                "near_resistance": [{"strike": 44, "volume": 13298}],
            },
            "expiry_metrics": {"this_week": {"straddle": {"lower": 41, "upper": 46}}},
            "top_call_volume": [{"strike": 44, "volume": 13298}],
        },
        "volume_anomaly": {"is_anomaly": True},
    }
    fb = {
        "available": True,
        "date": "2026-08-13",
        "prediction_date": "2026-08-12",
        "predicted": {"band": [41, 46]},
        "actual": {"high": 49.19, "low": 43.5, "close": 48.0},
    }
    st = ps.observe_from_grade("IREN", prev, fb, path=path)
    assert st is not None
    assert st["n"] == 1
    assert st["hits"] == 1
    assert st["status"] == "candidate"
    setup = st["observations"][0]["setup"]
    assert "50" not in str(setup.get("break_level"))
    assert setup.get("actual_high") == 49.19


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
