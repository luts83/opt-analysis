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


def _obs_result(o: dict) -> str:
    """관측 결과: hit | fail | unverified. 구형 hit bool 호환."""
    r = o.get("result")
    if r in ("hit", "fail", "unverified"):
        return r
    if "hit" in o:
        return "hit" if o.get("hit") else "fail"
    return "unverified"


def _evaluated(obs: list[dict]) -> list[dict]:
    """미검증 제외 — 학습 승률 분모에서 빼기."""
    return [o for o in obs if _obs_result(o) in ("hit", "fail")]


def _capped_hit_rate(obs: list[dict]) -> tuple[float | None, int, int, int]:
    """최근 표본 가중 상한 적중률. (rate, hits, fails, n_obs).

    미검증은 승률 분모에서 제외한다 (학습 오염 방지).
    """
    n_obs = len(obs)
    ev = _evaluated(obs)
    n = len(ev)
    if n == 0:
        return None, 0, 0, n_obs
    n_recent = max(1, int(round(n * RECENT_FRACTION)))
    n_recent = min(n_recent, n)
    recent = ev[-n_recent:]
    older = ev[:-n_recent] if n > n_recent else []
    w_recent_total = min(RECENT_WEIGHT_CAP, n_recent / n)
    w_older_total = 1.0 - w_recent_total if older else 0.0
    if not older:
        w_recent_total = 1.0

    def _rate(xs: list[dict]) -> float:
        if not xs:
            return 0.0
        return sum(1.0 for o in xs if _obs_result(o) == "hit") / len(xs)

    rate = _rate(recent) * w_recent_total + _rate(older) * w_older_total
    hits = sum(1 for o in ev if _obs_result(o) == "hit")
    fails = sum(1 for o in ev if _obs_result(o) == "fail")
    return round(rate, 4), hits, fails, n_obs


def pattern_state(pattern_id: str, path: Path | None = None) -> dict:
    data = load_patterns(path)
    rec = (data.get("patterns") or {}).get(pattern_id) or {}
    obs = rec.get("observations") or []
    rate, hits, fails, n_obs = _capped_hit_rate(obs)
    n_eval = hits + fails
    unverified = sum(1 for o in obs if _obs_result(o) == "unverified")
    enough = n_eval >= MIN_SAMPLES
    repeatable = enough and rate is not None and rate >= MIN_HIT_RATE
    status = "active" if repeatable else "candidate"
    return {
        "id": pattern_id,
        "label": rec.get("label") or _LABELS.get(pattern_id, pattern_id),
        "n": n_obs,
        "n_evaluated": n_eval,
        "hits": hits,
        "fails": fails,
        "unverified": unverified,
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
    hit: bool | None = None,
    result: str | None = None,
    setup: dict | None = None,
    path: Path | None = None,
) -> dict:
    """관찰 1건 추가. result=hit|fail|unverified. 미검증은 승률 분모 제외."""
    if result not in ("hit", "fail", "unverified"):
        if hit is None:
            result = "unverified"
        else:
            result = "hit" if hit else "fail"
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
            "result": result,
            # 하위 호환
            "hit": result == "hit",
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
        nr = levels.get("near_resistance") or []
        if nr:
            break_lv = nr[0].get("strike")
    # expansion/near_res 없어도 현재가±5% 콜 집중이면 셋업으로 본다
    spot = prev_snap.get("spot")
    calls = m.get("top_call_volume") or []
    if break_lv is None and spot is not None:
        for r in calls[:5]:
            try:
                s = float(r["strike"])
                if abs(s - float(spot)) / float(spot) <= 0.05:
                    break_lv = s
                    break
            except (TypeError, ValueError, KeyError):
                continue
    if break_lv is None:
        return None
    vol_anom = prev_snap.get("volume_anomaly") or {}
    spike = bool(vol_anom.get("is_anomaly"))
    near_vol = 0
    for r in calls[:5]:
        try:
            if abs(float(r["strike"]) - float(break_lv)) <= 1.01:
                near_vol = max(near_vol, int(r.get("volume") or 0))
        except (TypeError, ValueError, KeyError):
            continue
    # 거래 집중·확장맵·스파이크 중 하나만 있어도 관측 후보
    if not spike and near_vol < 500 and not exp:
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
        bl = float(setup["break_level"])
        hi = float(high) if high is not None else None
    except (TypeError, ValueError):
        return None
    if hi is None:
        result = "unverified"
    elif hi < bl * 0.97:
        # 관심가 미도달 → 실패가 아니라 미검증
        result = "unverified"
    elif upper is not None and hi > float(upper):
        result = "hit"
    elif hi >= bl * 0.998:
        # 돌파는 됐지만 상단 확장 실패
        result = "fail"
    else:
        result = "unverified"
    return record_observation(
        PATTERN_BREAKOUT_EXPAND,
        ticker=ticker,
        date=str(fb.get("date")),
        prediction_date=fb.get("prediction_date"),
        result=result,
        setup={
            "break_level": setup.get("break_level"),
            "volume_spike": setup.get("volume_spike"),
            "near_call_volume": setup.get("near_call_volume"),
            "band_upper": upper,
            "actual_high": high,
            "result": result,
        },
        path=path,
    )


def format_candidates_block(path: Path | None = None) -> str:
    """리포트용. 후보=표시만, 활성=확률 조정 안내. 관측 0이면 빈 문자열."""
    st = pattern_state(PATTERN_BREAKOUT_EXPAND, path)
    if st["n"] <= 0:
        return ""  # 빈 '아직 관찰 없어요' 정크 생략
    L = ["🧪 학습 후보 (예측 규칙을 바로 바꾸지 않음)"]
    tag = "활성(확률 소폭 가산)" if st["status"] == "active" else "학습 후보"
    rate = st.get("hit_rate")
    rate_s = f"{rate*100:.0f}%" if rate is not None else "-"
    L.append(
        f"- [{tag}] {st['label']}"
    )
    L.append(
        f"  관측 {st['n']} · 평가 {st.get('n_evaluated', st['n'])}/{st['min_samples']} "
        f"· 적중 {st['hits']} · 실패 {st.get('fails', 0)} · 미검증 {st.get('unverified', 0)} "
        f"(가중 적중률 {rate_s})"
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
        res = _obs_result(last)
        res_ko = {"hit": "적중", "fail": "실패", "unverified": "미검증"}.get(res, res)
        L.append(
            f"  최근 관찰: {last.get('ticker')} {last.get('prediction_date')}→{last.get('date')} "
            f"({res_ko})"
        )
    return "\n".join(L)


def backfill_from_snapshots(
    tickers: list[str] | None = None,
    *,
    limit_per_ticker: int = 40,
    path: Path | None = None,
) -> int:
    """기존 스냅샷에서 패턴 관측을 한 번 채운다. 이미 있으면 스킵.

    Returns: 새로 기록한 관측 수.
    """
    import snapshot_store

    st = pattern_state(PATTERN_BREAKOUT_EXPAND, path)
    if st.get("n", 0) > 0:
        return 0
    tickers = tickers or ["IREN", "TSLA", "SPCX"]
    added = 0
    seen: set[tuple[str, str]] = set()
    for ticker in tickers:
        try:
            dates = snapshot_store.list_dates(ticker) or []
        except Exception:
            # list_dates 없으면 디렉터리 스캔
            from pathlib import Path as P
            import config

            base = P(config.SNAPSHOTS_DIR)
            base = base if base.is_absolute() else P(__file__).resolve().parent / base
            d = base / ticker
            dates = sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
        dates = dates[-limit_per_ticker:]
        for i in range(1, len(dates)):
            prev = snapshot_store.load_snapshot(ticker, dates[i - 1])
            today = snapshot_store.load_snapshot(ticker, dates[i])
            if not prev or not today:
                continue
            fb = today.get("prediction_feedback")
            if not fb or not fb.get("available"):
                continue
            key = (ticker, str(fb.get("date") or dates[i]))
            if key in seen:
                continue
            seen.add(key)
            out = observe_from_grade(ticker, prev, fb, path=path)
            if out:
                added += 1
    return added
