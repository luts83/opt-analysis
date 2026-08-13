"""과적합 방지용 학습 후보 저장소.

단일 종목·단일 날짜 결과는 예측 규칙이 되지 않는다.
표본이 충분하고 적중률이 반복될 때만 시나리오 확률/신뢰도를 소폭 조정한다.
"""
from __future__ import annotations

import json
from pathlib import Path

# 엔진 가중치에 넣기 전 최소 관찰 수 (티커 합산)
MIN_SAMPLES = 8
# 적중률 하한
MIN_HIT_RATE = 0.55
# 최근 관측이 전체에서 차지할 수 있는 가중치 상한
RECENT_WEIGHT_CAP = 0.25
RECENT_FRACTION = 0.2
# 활성 패턴이 시나리오 순위에 더할 수 있는 최대치 (뒤집지 않음)
MAX_RANK_DELTA = 1.0

PATTERN_BREAKOUT_EXPAND = "breakout_volume_band_expand"
_LABELS = {
    PATTERN_BREAKOUT_EXPAND: (
        "핵심 관심가 돌파 + 거래량 급증 시 예상 범위 상단 확장 가능성"
    ),
}


def _root() -> Path:
    import config
    from pathlib import Path as P

    here = P(__file__).resolve().parent
    base = P(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else here / base
    d = base / "_learning"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return _root() / "patterns.json"


def load_patterns(path: Path | None = None) -> dict:
    p = path or _path()
    if not p.exists():
        return {"patterns": {}}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"patterns": {}}
        data.setdefault("patterns", {})
        return data
    except Exception:
        return {"patterns": {}}


def save_patterns(data: dict, path: Path | None = None) -> Path:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def _capped_hit_rate(obs: list[dict]) -> tuple[float | None, int, int]:
    """최근 표본 가중 상한을 적용한 적중률. (rate, hits, n)"""
    n = len(obs)
    if n == 0:
        return None, 0, 0
    n_recent = max(1, int(round(n * RECENT_FRACTION)))
    n_recent = min(n_recent, n)
    # 최신이 뒤에 쌓이도록 저장한다고 가정. 없으면 리스트 순서를 그대로.
    recent = obs[-n_recent:]
    older = obs[:-n_recent] if n > n_recent else []
    w_recent_total = min(RECENT_WEIGHT_CAP, n_recent / n)
    w_older_total = 1.0 - w_recent_total if older else 0.0
    if not older:
        w_recent_total = 1.0

    def _hit(xs: list[dict]) -> float:
        if not xs:
            return 0.0
        return sum(1.0 for o in xs if o.get("hit")) / len(xs)

    rate = _hit(recent) * w_recent_total + _hit(older) * w_older_total
    hits = sum(1 for o in obs if o.get("hit"))
    return round(rate, 4), hits, n


def pattern_state(pattern_id: str, path: Path | None = None) -> dict:
    data = load_patterns(path)
    rec = (data.get("patterns") or {}).get(pattern_id) or {}
    obs = rec.get("observations") or []
    rate, hits, n = _capped_hit_rate(obs)
    enough = n >= MIN_SAMPLES
    repeatable = enough and rate is not None and rate >= MIN_HIT_RATE
    status = "active" if repeatable else "candidate"
    return {
        "id": pattern_id,
        "label": rec.get("label") or _LABELS.get(pattern_id, pattern_id),
        "n": n,
        "hits": hits,
        "hit_rate": rate,
        "min_samples": MIN_SAMPLES,
        "status": status,
        "rank_delta": MAX_RANK_DELTA if status == "active" else 0.0,
        "observations": obs,
    }


def rank_delta(pattern_id: str, path: Path | None = None) -> float:
    """예측을 뒤집지 않는 소폭 가산. 후보면 0."""
    return float(pattern_state(pattern_id, path).get("rank_delta") or 0)


def record_observation(
    pattern_id: str,
    *,
    ticker: str,
    date: str,
    prediction_date: str | None,
    hit: bool,
    setup: dict | None = None,
    path: Path | None = None,
) -> dict:
    """관찰 1건 추가. 같은 티커+날짜는 덮어씀. 규칙은 바꾸지 않음."""
    data = load_patterns(path)
    pats = data.setdefault("patterns", {})
    rec = pats.setdefault(
        pattern_id,
        {
            "label": _LABELS.get(pattern_id, pattern_id),
            "observations": [],
        },
    )
    obs: list[dict] = rec.setdefault("observations", [])
    key = (str(ticker).upper(), str(date))
    obs[:] = [o for o in obs if (str(o.get("ticker", "")).upper(), str(o.get("date"))) != key]
    obs.append(
        {
            "ticker": str(ticker).upper(),
            "date": date,
            "prediction_date": prediction_date,
            "hit": bool(hit),
            "setup": setup or {},
        }
    )
    rec["observations"] = obs[-200:]
    rec["label"] = rec.get("label") or _LABELS.get(pattern_id, pattern_id)
    save_patterns(data, path)
    return pattern_state(pattern_id, path)


def detect_breakout_expand_setup(prev_snap: dict | None) -> dict | None:
    """어제 스냅샷에 '관심가 + 거래 집중' 셋업이 있었는지. 목표가($50)는 기록하지 않음."""
    if not prev_snap:
        return None
    m = prev_snap.get("metrics") or {}
    levels = m.get("levels") or {}
    exp = levels.get("expansion_up") or {}
    break_lv = exp.get("break_level")
    if break_lv is None:
        nr = (levels.get("near_resistance") or levels.get("near_resistance") or [])
        if nr:
            break_lv = nr[0].get("strike")
    if break_lv is None:
        return None
    vol_anom = prev_snap.get("volume_anomaly") or {}
    spike = bool(vol_anom.get("is_anomaly"))
    calls = m.get("top_call_volume") or []
    near_vol = 0
    for r in calls[:5]:
        try:
            if abs(float(r["strike"]) - float(break_lv)) <= 1.01:
                near_vol = max(near_vol, int(r.get("volume") or 0))
        except (TypeError, ValueError, KeyError):
            continue
    if not spike and near_vol < 1000 and not exp:
        return None
    band = None
    tw = (m.get("expiry_metrics") or {}).get("this_week") or {}
    st = tw.get("straddle") or {}
    if st.get("upper") is not None:
        band = [st.get("lower"), st.get("upper")]
    return {
        "break_level": float(break_lv),
        "volume_spike": spike,
        "near_call_volume": near_vol,
        "had_expansion_map": bool(exp),
        "band": band,
        # 목표가 숫자는 학습하지 않음 — 구조만
    }


def observe_from_grade(
    ticker: str,
    prev_snap: dict | None,
    fb: dict | None,
    path: Path | None = None,
) -> dict | None:
    """채점 결과로 확장 패턴 관찰만 기록. 엔진 규칙은 즉시 바꾸지 않음."""
    if not fb or not fb.get("available"):
        return None
    setup = detect_breakout_expand_setup(prev_snap)
    if not setup:
        return None
    act = fb.get("actual") or {}
    high = act.get("high")
    band = (fb.get("predicted") or {}).get("band") or setup.get("band")
    upper = band[1] if isinstance(band, (list, tuple)) and len(band) > 1 else None
    try:
        hit = (
            high is not None
            and float(high) >= float(setup["break_level"]) * 0.998
            and upper is not None
            and float(high) > float(upper)
        )
    except (TypeError, ValueError):
        return None
    return record_observation(
        PATTERN_BREAKOUT_EXPAND,
        ticker=ticker,
        date=str(fb.get("date")),
        prediction_date=fb.get("prediction_date"),
        hit=bool(hit),
        setup={
            "break_level": setup.get("break_level"),
            "volume_spike": setup.get("volume_spike"),
            "near_call_volume": setup.get("near_call_volume"),
            "band_upper": upper,
            "actual_high": high,
        },
        path=path,
    )


def format_candidates_block(path: Path | None = None) -> str:
    """리포트용. 후보=표시만, 활성=확률 조정 안내."""
    st = pattern_state(PATTERN_BREAKOUT_EXPAND, path)
    L = ["🧪 학습 후보 (예측 규칙을 바로 바꾸지 않음)"]
    if st["n"] <= 0:
        L.append(
            "- 아직 관찰이 없어요. 단일 사례는 기록만 하고 가중치에 넣지 않습니다."
        )
        return "\n".join(L)
    tag = "활성(확률 소폭 가산)" if st["status"] == "active" else "학습 후보"
    rate = st.get("hit_rate")
    rate_s = f"{rate*100:.0f}%" if rate is not None else "-"
    L.append(
        f"- [{tag}] {st['label']}"
    )
    L.append(
        f"  표본 {st['n']}/{st['min_samples']} · 적중 {st['hits']} "
        f"(최근 가중 상한 {int(RECENT_WEIGHT_CAP*100)}% 적용 적중률 {rate_s})"
    )
    if st["status"] != "active":
        L.append(
            "  → 반복성 확인 전이라 시나리오 순위를 뒤집지 않습니다. "
            "기존 상승/횡보/하락의 확률 가산만 후보로 둡니다."
        )
    else:
        L.append(
            "  → 목표가 숫자를 배우지 않고, 상승 시나리오의 신뢰도만 소폭 조정합니다."
        )
    last = (st.get("observations") or [])[-1]
    if last:
        L.append(
            f"  최근 관찰: {last.get('ticker')} {last.get('prediction_date')}→{last.get('date')} "
            f"({'적중' if last.get('hit') else '미적중'})"
        )
    return "\n".join(L)
