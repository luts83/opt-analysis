"""옵션 지표 계산.

지표:
1. V/OI 비율 + 분류 (<1 조용함 / 1~2 활발 / 2~5 뜨거움 / 5+ 극단)
2. OI 변화율 (어제 대비)
3. Volume 이상 감지 (최근 평균 대비 N배)
4. 콜/풋 OI 밀집 지점 (만기별 top N strike)
5. Straddle 예상 밴드 (ATM 콜 lastPrice + ATM 풋 lastPrice)
6. 콜/풋 볼륨 비율

OI 데이터 지연 대응:
- yfinance OI 는 미국 장중/장마감 후에만 채워진다. 장 시작 전에는 전 계약 0.
- 전 계약의 90% 이상이 0이면 stale 로 보고, 전일 스냅샷의 계약별 OI 를
  '보간(carry-forward)' 해 마지막으로 알려진 값으로 채운다(명확히 라벨링).
"""
from __future__ import annotations

import config

# ------------------------------------------------------------------ #
# 1. V/OI
# ------------------------------------------------------------------ #

def voi_ratio(volume: float, open_interest: float) -> float | None:
    if not open_interest or open_interest <= 0:
        return None
    return volume / open_interest


def classify_voi(voi: float | None) -> str:
    if voi is None:
        return "N/A"
    if voi < 1:
        return "조용함"
    if voi < 2:
        return "활발"
    if voi < 5:
        return "뜨거움"
    return "극단"


# ------------------------------------------------------------------ #
# 2. OI 변화율
# ------------------------------------------------------------------ #

def oi_change_rate(today_oi: float, yesterday_oi: float | None) -> float | None:
    if yesterday_oi is None or yesterday_oi <= 0:
        return None
    return (today_oi - yesterday_oi) / yesterday_oi


# ------------------------------------------------------------------ #
# 3. Volume 이상 감지
# ------------------------------------------------------------------ #

def volume_anomaly(today_volume: float, history_volumes: list[float]) -> bool:
    vols = [v for v in history_volumes if v and v > 0]
    if not vols:
        return False
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return False
    return today_volume >= avg * config.VOLUME_ANOMALY_MULT


# ------------------------------------------------------------------ #
# 4. OI 밀집 지점
# ------------------------------------------------------------------ #

def oi_clusters(rows: list[dict], top_n: int | None = None) -> list[dict]:
    top_n = top_n or config.OI_CLUSTER_TOP_N
    ranked = sorted(rows, key=lambda r: r.get("openInterest", 0), reverse=True)
    return [
        {"strike": r["strike"], "oi": int(r.get("openInterest", 0))}
        for r in ranked[:top_n]
        if r.get("openInterest", 0) > 0
    ]


# ------------------------------------------------------------------ #
# 5. Straddle 예상 밴드
# ------------------------------------------------------------------ #

def _nearest_atm(rows: list[dict], spot: float) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - spot))


def straddle_band(spot: float, calls: list[dict], puts: list[dict]) -> dict | None:
    atm_call = _nearest_atm(calls, spot)
    atm_put = _nearest_atm(puts, spot)
    if not atm_call or not atm_put:
        return None
    call_px = atm_call.get("lastPrice") or 0.0
    put_px = atm_put.get("lastPrice") or 0.0
    straddle = call_px + put_px
    return {
        "atm_strike": atm_call["strike"],
        "call_last": round(call_px, 3),
        "put_last": round(put_px, 3),
        "straddle": round(straddle, 3),
        "lower": round(spot - straddle, 2),
        "upper": round(spot + straddle, 2),
        "band_pct": round((straddle / spot * 100) if spot else 0.0, 2),
    }


# ------------------------------------------------------------------ #
# 6. 콜/풋 볼륨 비율
# ------------------------------------------------------------------ #

def call_put_volume_ratio(data: dict) -> tuple[float | None, int, int]:
    call_vol = put_vol = 0
    for leg in data["expiries"].values():
        call_vol += sum(r.get("volume", 0) for r in leg["calls"])
        put_vol += sum(r.get("volume", 0) for r in leg["puts"])
    ratio = (call_vol / put_vol) if put_vol > 0 else None
    return ratio, call_vol, put_vol


def sentiment_from_ratio(
    ratio: float | None,
    change_pct: float | None = None,
    *,
    both_side_extreme: bool = False,
) -> str:
    """C/P 는 방향 신호가 아니라 거래 구성. 극단이 겹치면 변동성 우선.

    급락(-5%↓) 중 C/P 상승은 '강세'가 아니라 반등/헤지/콜매도 혼재로 본다.
    """
    if both_side_extreme:
        return "변동성 확대 가능성"
    if change_pct is not None and change_pct <= -5 and ratio is not None and ratio >= 1.2:
        return "방향 불확실 (콜 우세·주가 급락)"
    if change_pct is not None and change_pct >= 5 and ratio is not None and ratio <= 0.83:
        return "차익실현/헤지 혼재"
    if ratio is None:
        return "중립"
    if ratio >= 1.2:
        return "콜 거래 우세"
    if ratio <= 0.83:
        return "풋 거래 우세"
    return "콜·풋 균형"


def detect_both_side_oi_surge(anomalies: list[dict] | None) -> bool:
    """콜·풋 OI 대량 유입이 동시에 있으면 True."""
    if not anomalies:
        return False
    call_up = any(
        a.get("type") == "OI_SURGE" and str(a.get("option_type", "")).upper() == "CALL"
        for a in anomalies
    )
    put_up = any(
        a.get("type") == "OI_SURGE" and str(a.get("option_type", "")).upper() == "PUT"
        for a in anomalies
    )
    return call_up and put_up


# ------------------------------------------------------------------ #
# 반복 순회 / 인덱스
# ------------------------------------------------------------------ #

def _iter_rows(data: dict):
    for role, leg in data["expiries"].items():
        for opt_type, rows in (("CALL", leg["calls"]), ("PUT", leg["puts"])):
            for r in rows:
                yield role, opt_type, leg["date"], r


def _index_contracts(data: dict) -> dict[tuple, dict]:
    return {
        (role, opt_type, r["strike"]): r
        for role, opt_type, _date, r in _iter_rows(data)
    }


def total_open_interest(data: dict) -> int:
    return sum(r.get("openInterest", 0) for _r, _t, _d, r in _iter_rows(data))


def is_oi_stale(data: dict) -> bool:
    """전체 계약 중 OI==0 비율이 임계(기본 90%) 이상이면 stale."""
    total = zeros = 0
    for _r, _t, _d, r in _iter_rows(data):
        total += 1
        if r.get("openInterest", 0) == 0:
            zeros += 1
    if total == 0:
        return False
    return (zeros / total) >= config.OI_STALE_ZERO_FRACTION


def apply_oi_fallback(data: dict, prev: dict | None, oi_stale: bool) -> bool:
    """stale 이면 전일 스냅샷의 계약별 OI 로 보간한다.

    Returns: 보간이 실제로 이루어졌는지(carried).
    """
    if not oi_stale or not prev:
        return False
    prev_idx = _index_contracts(prev)
    carried = False
    for role, opt_type, _d, r in _iter_rows(data):
        if r.get("openInterest", 0) == 0:
            pr = prev_idx.get((role, opt_type, r["strike"]))
            if pr and pr.get("openInterest", 0) > 0:
                r["openInterest"] = int(pr["openInterest"])
                r["oi_carried_forward"] = True
                carried = True
    return carried


# ------------------------------------------------------------------ #
# 개별 옵션 행 보강
# ------------------------------------------------------------------ #

def _history_volume_index(history: list[dict]) -> dict[tuple, list[float]]:
    idx: dict[tuple, list[float]] = {}
    for h in history:
        for role, opt_type, _date, r in _iter_rows(h):
            idx.setdefault((role, opt_type, r["strike"]), []).append(
                r.get("volume", 0)
            )
    return idx


def enrich_contracts(
    data: dict, prev: dict | None, history: list[dict], oi_real: bool = True
) -> None:
    """각 옵션 행에 voi / openInterest_change_pct / volume_vs_avg_ratio 를 채운다.

    - voi: OI(보간 포함) > 0 이면 계산.
    - openInterest_change_pct: 오늘 OI 가 '실제 당일 값'일 때만(oi_real).
    """
    prev_idx = _index_contracts(prev) if prev else {}
    hist_vol = _history_volume_index(history)

    for role, opt_type, _date, r in _iter_rows(data):
        oi = r.get("openInterest", 0)
        vol = r.get("volume", 0)
        key = (role, opt_type, r["strike"])

        v = voi_ratio(vol, oi)
        r["voi"] = round(v, 3) if v is not None else None

        if oi_real:
            pr = prev_idx.get(key)
            rate = oi_change_rate(oi, pr.get("openInterest") if pr else None)
            r["openInterest_change_pct"] = (
                round(rate * 100, 1) if rate is not None else None
            )
        else:
            r["openInterest_change_pct"] = None

        vols = [x for x in hist_vol.get(key, []) if x and x > 0]
        if vols:
            avg = sum(vols) / len(vols)
            r["volume_vs_avg_ratio"] = round(vol / avg, 2) if avg > 0 else None
        else:
            r["volume_vs_avg_ratio"] = None


# ------------------------------------------------------------------ #
# 기본 지표 집계
# ------------------------------------------------------------------ #

def _total_volume(data: dict) -> int:
    return sum(r.get("volume", 0) for _r, _t, _d, r in _iter_rows(data))


def _volume_rank(rows_with_meta: list[dict], n: int) -> list[dict]:
    ranked = sorted(rows_with_meta, key=lambda x: x["volume"], reverse=True)
    return ranked[:n]


def build_base_metrics(
    data: dict, prev: dict | None = None, oi_available: bool = True
) -> dict:
    """오늘 데이터로 지표 계산. oi_available=False 면 OI 지표 폴백/생략."""
    spot = data["spot"]
    ratio, call_vol, put_vol = call_put_volume_ratio(data)
    prev_close = data.get("previous_close")
    change_pct = None
    if prev_close and spot is not None and float(prev_close) > 0:
        change_pct = round((float(spot) - float(prev_close)) / float(prev_close) * 100, 2)

    prev_em = (prev or {}).get("metrics", {}).get("expiry_metrics", {})

    expiry_metrics: dict[str, dict] = {}
    for role, leg in data["expiries"].items():
        if not oi_available and prev_em.get(role):
            call_clusters = prev_em[role].get("call_oi_clusters", [])
            put_clusters = prev_em[role].get("put_oi_clusters", [])
        else:
            call_clusters = oi_clusters(leg["calls"])
            put_clusters = oi_clusters(leg["puts"])
        expiry_metrics[role] = {
            "date": leg["date"],
            "straddle": straddle_band(spot, leg["calls"], leg["puts"]),
            "call_oi_clusters": call_clusters,
            "put_oi_clusters": put_clusters,
        }

    # V/OI 상위 + 콜/풋 거래량 상위 (분리)
    voi_candidates: list[dict] = []
    call_vol_rows: list[dict] = []
    put_vol_rows: list[dict] = []
    for role, opt_type, date, r in _iter_rows(data):
        oi = r.get("openInterest", 0)
        vol = r.get("volume", 0)
        entry = {
            "expiry": date,
            "role": role,
            "type": opt_type,
            "strike": r["strike"],
            "volume": vol,
            "oi": oi,
            "voi": r.get("voi"),
            "class": classify_voi(r.get("voi")),
            "oi_carried_forward": bool(r.get("oi_carried_forward")),
        }
        if opt_type == "CALL":
            call_vol_rows.append(entry)
        else:
            put_vol_rows.append(entry)
        if r.get("voi") is not None and oi >= config.VOI_MIN_OI and vol >= config.VOI_MIN_VOLUME:
            voi_candidates.append(entry)

    voi_candidates.sort(key=lambda x: x["voi"], reverse=True)
    if not oi_available:
        voi_candidates = []  # OI 없으면 V/OI 계산·표시하지 않음

    zd = None
    if "zero_dte" in data.get("expiries", {}):
        zd = data["expiries"]["zero_dte"].get("date")

    senti = sentiment_from_ratio(ratio, change_pct)
    return {
        "call_put_volume_ratio": round(ratio, 3) if ratio is not None else None,
        "sentiment": senti,
        "sentiment_raw": sentiment_from_ratio(ratio),  # C/P만 본 구성 라벨
        "price_change_pct": change_pct,
        "sentiment_tags": [],
        "sentiment_rule": (
            "C/P는 방향이 아니라 콜/풋 거래 구성비. "
            "급락+콜우세는 방향 불확실, 콜·풋 극단 동시 → 변동성 확대 우선"
        ),
        "total_call_volume": call_vol,
        "total_put_volume": put_vol,
        "total_volume": _total_volume(data),
        "total_open_interest": total_open_interest(data),
        "oi_available": oi_available,
        "low_confidence": (not oi_available) or total_open_interest(data) <= 0,
        "zero_dte_date": zd,
        "expiry_metrics": expiry_metrics,
        "top_voi": voi_candidates[: config.VOI_TOP_N],
        "top_call_volume": _volume_rank(call_vol_rows, config.TOP_VOLUME_N),
        "top_put_volume": _volume_rank(put_vol_rows, config.TOP_VOLUME_N),
    }


def apply_sentiment_tags(base: dict, anomalies: list[dict] | None) -> dict:
    """OI/V/OI 양방향 극단이면 강세·약세를 고르지 않고 변동성 확대."""
    import price_levels

    change_pct = base.get("price_change_pct")
    ratio = base.get("call_put_volume_ratio")
    tags: list[str] = list(base.get("sentiment_tags") or [])
    both = detect_both_side_oi_surge(anomalies) or price_levels.detect_both_side_voi_extreme(base)
    if both:
        tags.append("변동성 확대 가능성")
        base["sentiment"] = sentiment_from_ratio(
            ratio, change_pct, both_side_extreme=True
        )
    if price_levels.is_low_confidence(base):
        tags.append("저신뢰(OI 없음)")
        base["low_confidence"] = True
    base["sentiment_tags"] = tags
    return base


# ------------------------------------------------------------------ #
# 이상 신호(anomalies)
# ------------------------------------------------------------------ #

def build_anomalies(data: dict, prev: dict | None, oi_real: bool = True) -> list[dict]:
    """어제 대비 OI 급변. 오늘 OI 가 실제 당일 값일 때만(oi_real) 계산."""
    anomalies: list[dict] = []
    if not prev or not oi_real:
        return anomalies

    prev_idx = _index_contracts(prev)
    for role, opt_type, date, r in _iter_rows(data):
        oi = r.get("openInterest", 0)
        pr = prev_idx.get((role, opt_type, r["strike"]))
        if not pr:
            continue
        p_oi = pr.get("openInterest", 0)
        if oi < config.OI_ALERT_MIN_OI and p_oi < config.OI_ALERT_MIN_OI:
            continue
        rate = oi_change_rate(oi, p_oi)
        if rate is None:
            continue
        pct = round(rate * 100, 1)
        strike = r["strike"]
        if rate >= config.OI_SURGE_UP:
            kind, label = "OI_SURGE", "대량 유입"
        elif rate <= config.OI_SURGE_DOWN:
            kind, label = "OI_DROP", "대량 청산"
        else:
            continue
        anomalies.append(
            {
                "type": kind,
                "expiry": date,
                "role": role,
                "strike": strike,
                "option_type": opt_type,
                "prev_oi": p_oi,
                "curr_oi": oi,
                "change_pct": pct,
                "message": f"{date} 만기 ${strike:g} {opt_type} OI {label} "
                f"({p_oi:,} → {oi:,}, {'+' if pct > 0 else ''}{pct}%)",
            }
        )

    anomalies.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return anomalies


def build_volume_anomaly(data: dict, history: list[dict]) -> dict | None:
    hist_vols = [
        h.get("metrics", {}).get("total_volume")
        for h in history
        if h.get("metrics", {}).get("total_volume")
    ]
    if not hist_vols:
        return None
    today_total = data.get("metrics", {}).get("total_volume") or _total_volume(data)
    avg = sum(hist_vols) / len(hist_vols)
    return {
        "is_anomaly": volume_anomaly(today_total, hist_vols),
        "today": today_total,
        "recent_avg": round(avg, 1),
        "mult": round(today_total / avg, 2) if avg else None,
    }


def build_trend(history: list[dict], today: dict) -> list[dict]:
    """최근 며칠 핵심 지표 추이 (AI 해설이 트렌드를 읽도록)."""
    def _row(snap):
        m = snap.get("metrics", {})
        return {
            "date": snap.get("date"),
            "spot": snap.get("spot"),
            "sentiment": m.get("sentiment"),
            "call_put_volume_ratio": m.get("call_put_volume_ratio"),
            "total_volume": m.get("total_volume"),
        }

    rows = [_row(h) for h in sorted(history, key=lambda s: s.get("date", ""))]
    rows.append(_row(today))
    return rows[-6:]  # 최근 6일


# ------------------------------------------------------------------ #
# 지지/저항 레벨 (강한 OI vs 단기 거래량)
# ------------------------------------------------------------------ #

def build_levels(
    base: dict,
    spot: float,
    data: dict | None = None,
    prev: dict | None = None,
    today_ohlc: dict | None = None,
) -> dict:
    """관심 가격 맵 (OI/거래량 ≠ 지지·저항 단정)."""
    import price_levels

    return price_levels.build_levels(
        base, spot, data=data, prev=prev, today_ohlc=today_ohlc
    )


# ------------------------------------------------------------------ #
# 만기별 밴드 트렌드
# ------------------------------------------------------------------ #

def build_band_trend(base: dict) -> dict | None:
    """이번주/다음주/월간 상·하단 확장을 한 줄로 해석."""
    em = base.get("expiry_metrics") or {}
    rows = []
    zd = (base.get("zero_dte_date") or "").strip()
    labels = (("this_week", "이번주"), ("next_week", "2주내"), ("monthly", "1개월"))
    if zd:
        labels = (("this_week", "이번주(0DTE 제외)"), ("next_week", "2주내"), ("monthly", "1개월"))
    for role, label in labels:
        st = (em.get(role) or {}).get("straddle")
        if not st:
            continue
        lo, up = st.get("lower"), st.get("upper")
        # 표시용 반올림
        if lo is not None:
            lo = round(float(lo))
        if up is not None:
            up = round(float(up))
        bp = st.get("band_pct")
        if bp is not None:
            bp = round(float(bp))
        rows.append(
            {
                "role": role,
                "label": label,
                "date": (em.get(role) or {}).get("date"),
                "lower": lo,
                "upper": up,
                "band_pct": bp,
            }
        )
    if len(rows) < 2:
        return None

    first, last = rows[0], rows[-1]
    upper_expand = (
        last["upper"] is not None and first["upper"] is not None
        and last["upper"] > first["upper"]
    )
    lower_expand = (
        last["lower"] is not None and first["lower"] is not None
        and last["lower"] < first["lower"]
    )
    if upper_expand and lower_expand:
        interpretation = "→ 시간 갈수록 상·하방 모두 위험 구간이 넓어짐"
    elif upper_expand:
        interpretation = "→ 만기가 멀수록 상방 시나리오가 더 열림"
    elif lower_expand:
        interpretation = "→ 시간 갈수록 하방 위험 확대"
    else:
        interpretation = "→ 만기별 컨센서스가 비교적 좁음"
    return {"rows": rows, "interpretation": interpretation}


# ------------------------------------------------------------------ #
# 어제 대비 변화 요약
# ------------------------------------------------------------------ #

def build_day_over_day(data: dict, base: dict, prev: dict | None) -> dict | None:
    """어제 스냅샷과 비교(주가·거래량·심리·밴드). OI 급변은 별도 anomalies."""
    if not prev:
        return {
            "available": False,
            "note": "비교할 어제 스냅샷이 없어요. 내일치 쌓이면 변화 감지가 시작됩니다.",
        }
    prev_m = prev.get("metrics") or {}
    spot = data.get("spot")
    prev_spot = prev.get("spot")
    spot_chg = None
    if spot is not None and prev_spot:
        spot_chg = round((spot - prev_spot) / prev_spot * 100, 2)

    vol_t = base.get("total_volume") or 0
    vol_p = prev_m.get("total_volume") or 0
    vol_mult = round(vol_t / vol_p, 2) if vol_p else None

    st_t = ((base.get("expiry_metrics") or {}).get("this_week") or {}).get("straddle") or {}
    st_p = ((prev_m.get("expiry_metrics") or {}).get("this_week") or {}).get("straddle") or {}
    band_delta = None
    if st_t.get("band_pct") is not None and st_p.get("band_pct") is not None:
        band_delta = round(st_t["band_pct"] - st_p["band_pct"], 2)

    # 특이사항만 (비슷/유지는 노이즈 → 제외)
    unusual: list[str] = []
    if spot_chg is not None and abs(spot_chg) >= 3.0:
        unusual.append(f"주가 급변 {prev_spot} → {spot} ({spot_chg:+.1f}%)")
    if vol_mult is not None:
        if vol_mult >= 1.5:
            unusual.append(f"옵션 거래량 급증 (어제 대비 {vol_mult}배: {vol_p:,} → {vol_t:,})")
        elif vol_mult <= 0.7:
            unusual.append(f"옵션 거래량 급감 (어제 대비 {vol_mult}배: {vol_p:,} → {vol_t:,})")
    senti_t, senti_p = base.get("sentiment"), prev_m.get("sentiment")
    if senti_t and senti_p and senti_t != senti_p:
        unusual.append(f"심리 전환: {senti_p} → {senti_t}")
    elif senti_t and senti_t not in ("강세", "약세", "중립"):
        # 급락+고C/P 등 특수 라벨은 전환이 없어도 표시
        unusual.append(f"심리: {senti_t}")
    for tag in base.get("sentiment_tags") or []:
        if tag not in unusual:
            unusual.append(tag)
    if band_delta is not None and abs(band_delta) >= 1.0:
        unusual.append(
            f"예상 변동폭 {st_p.get('band_pct')}% → {st_t.get('band_pct')}% ({band_delta:+}%p)"
        )

    return {
        "available": True,
        "prev_date": prev.get("date"),
        "spot_change_pct": spot_chg,
        "volume_mult": vol_mult,
        "sentiment_today": senti_t,
        "sentiment_prev": senti_p,
        "band_delta_pp": band_delta,
        "highlights": unusual,  # 특이사항만
        "has_unusual": bool(unusual),
    }