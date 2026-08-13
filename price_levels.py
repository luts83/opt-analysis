"""관심 가격·역할 전환·상단 확장. OI/거래량을 지지·저항으로 단정하지 않는다.

중요도: 현재가 거리 > OI > 거래량 > V/OI > 가격 반응 > 잔여 만기.
"""
from __future__ import annotations

from typing import Any

# 거리 가중: 20% 밖은 거의 0
_DIST_CAP_PCT = 20.0
_NEAR_PCT = 0.5  # 현재가와 동일 취급
_FLIP_PCT = 0.5
_CLUSTER_GAP_PCT = 3.0
_CLUSTER_GAP_ABS = 1.5
_VOI_HOT = 3.0
_VOI_EXTREME = 5.0
_MIN_VOL_INTEREST = 200


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def distance_pct(spot: float, strike: float) -> float:
    if not spot:
        return 99.0
    return abs(float(strike) - float(spot)) / float(spot) * 100.0


def distance_score(spot: float, strike: float) -> float:
    """가까울수록 1, _DIST_CAP_PCT 이상이면 0."""
    d = distance_pct(spot, strike)
    if d >= _DIST_CAP_PCT:
        return 0.0
    return max(0.0, 1.0 - d / _DIST_CAP_PCT)


def importance_score(
    spot: float,
    strike: float,
    *,
    oi: float = 0,
    volume: float = 0,
    voi: float | None = None,
    reacted: bool = False,
    dte_days: int | None = None,
    max_oi: float = 1,
    max_vol: float = 1,
) -> float:
    dist = distance_score(spot, strike) * 0.40
    oi_s = (float(oi) / max(max_oi, 1)) * 0.20
    vol_s = (float(volume) / max(max_vol, 1)) * 0.15
    voi_s = 0.0
    if voi is not None:
        voi_s = min(float(voi) / 10.0, 1.0) * 0.15
    react_s = 0.05 if reacted else 0.0
    dte_s = 0.0
    if dte_days is not None:
        if dte_days <= 0:
            dte_s = 0.02  # 0DTE 는 중요하되 과대평가 금지
        elif dte_days <= 7:
            dte_s = 0.05
        elif dte_days <= 21:
            dte_s = 0.03
        else:
            dte_s = 0.01
    return round(dist + oi_s + vol_s + voi_s + react_s + dte_s, 4)


def is_low_confidence(base: dict, data: dict | None = None) -> bool:
    """OI 없거나 stale 이면 저신뢰. 거래량만으로 방향/지지저항 금지."""
    if base.get("oi_available") is False:
        return True
    src = str(base.get("oi_source") or "")
    if src in ("데이터 없음",):
        return True
    total_oi = base.get("total_open_interest") or 0
    if total_oi <= 0:
        return True
    return False


def detect_both_side_voi_extreme(base: dict) -> bool:
    rows = base.get("top_voi") or []
    hot_c = any(
        str(r.get("type", "")).upper() == "CALL" and (r.get("voi") or 0) >= _VOI_EXTREME
        for r in rows
    )
    hot_p = any(
        str(r.get("type", "")).upper() in ("PUT", "P")
        and (r.get("voi") or 0) >= _VOI_EXTREME
        for r in rows
    )
    return bool(hot_c and hot_p)


def _merge_interest_rows(base: dict) -> list[dict]:
    """콜/풋 거래량·OI 클러스터·V/OI 를 행사가별로 합친다."""
    by_k: dict[tuple, dict] = {}

    def _key(strike, opt_type: str) -> tuple:
        return (round(float(strike), 4), opt_type.upper())

    def _acc(strike, opt_type: str, **kw) -> None:
        k = _key(strike, opt_type)
        d = by_k.setdefault(
            k,
            {
                "strike": float(strike),
                "type": opt_type.upper(),
                "oi": 0,
                "volume": 0,
                "voi": None,
                "expiry": kw.get("expiry"),
                "role_hint": None,
            },
        )
        if kw.get("oi"):
            d["oi"] = max(int(d["oi"] or 0), int(kw["oi"]))
        if kw.get("volume"):
            d["volume"] = max(int(d["volume"] or 0), int(kw["volume"]))
        if kw.get("voi") is not None:
            prev = d.get("voi")
            d["voi"] = kw["voi"] if prev is None else max(float(prev), float(kw["voi"]))
        if kw.get("expiry") and not d.get("expiry"):
            d["expiry"] = kw["expiry"]

    em = (base.get("expiry_metrics") or {}).get("this_week") or {}
    for c in em.get("call_oi_clusters") or []:
        _acc(c["strike"], "CALL", oi=c.get("oi"), expiry=em.get("date"))
    for p in em.get("put_oi_clusters") or []:
        _acc(p["strike"], "PUT", oi=p.get("oi"), expiry=em.get("date"))
    for r in base.get("top_call_volume") or []:
        _acc(r["strike"], "CALL", volume=r.get("volume"), oi=r.get("oi"),
             voi=r.get("voi"), expiry=r.get("expiry"))
    for r in base.get("top_put_volume") or []:
        _acc(r["strike"], "PUT", volume=r.get("volume"), oi=r.get("oi"),
             voi=r.get("voi"), expiry=r.get("expiry"))
    for r in base.get("top_voi") or []:
        _acc(r["strike"], str(r.get("type") or "CALL"), volume=r.get("volume"),
             oi=r.get("oi"), voi=r.get("voi"), expiry=r.get("expiry"))
    return list(by_k.values())


def _role_for_strike(
    strike: float,
    spot: float,
    opt_type: str,
    *,
    prev_spot: float | None = None,
    today_high: float | None = None,
    today_low: float | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """가격 위치 기준으로 역할. OI 방향 단정 없음."""
    hi = today_high if today_high is not None else spot
    lo = today_low if today_low is not None else spot
    above = strike > spot * (1 + _FLIP_PCT / 100)
    below = strike < spot * (1 - _FLIP_PCT / 100)

    # 돌파: 이전엔 위(저항 후보)였는데 지금 아래
    was_above = prev_spot is not None and strike > prev_spot * (1 + _FLIP_PCT / 100)
    broken_up = was_above and (hi >= strike * 0.998 or spot >= strike * 0.998)
    rebreak_down = broken_up and lo < strike * (1 - _FLIP_PCT / 100)

    if rebreak_down:
        return {
            "role": "failed_breakout",
            "label": "돌파 실패 가능성",
            "side": "resist",
        }
    if broken_up and below:
        return {
            "role": "broken_resist_now_support",
            "label": "돌파 확인 / 지지 후보",
            "side": "support",
        }
    if above:
        if confirmed:
            return {"role": "strong_resist", "label": "강한 저항", "side": "resist"}
        kind = "resist_candidate" if (opt_type == "CALL" or True) else "interest"
        return {
            "role": kind,
            "label": "저항 후보",
            "side": "resist",
        }
    if below:
        if confirmed:
            return {"role": "strong_support", "label": "강한 지지", "side": "support"}
        return {"role": "support_candidate", "label": "지지 후보", "side": "support"}
    return {"role": "testing", "label": "핵심 가격(테스트 중)", "side": "at"}


def detect_upside_expansion(
    spot: float,
    rows: list[dict],
    *,
    volume_spike: bool = False,
) -> dict | None:
    """현재가 위 연속 콜 관심 → 돌파 후 확장 구간 + 먼 자석 가격.

    IREN 8/12 사례: $44 돌파 → $45~47 확장 → $49~50 테스트.
    """
    calls = [
        r for r in rows
        if str(r.get("type", "")).upper() == "CALL"
        and float(r["strike"]) > spot * 1.002
        and (
            (r.get("volume") or 0) >= _MIN_VOL_INTEREST
            or (r.get("voi") or 0) >= _VOI_HOT
            or (r.get("oi") or 0) > 0
        )
    ]
    if not calls:
        return None
    calls.sort(key=lambda r: float(r["strike"]))
    gap_lim = max(_CLUSTER_GAP_ABS, spot * _CLUSTER_GAP_PCT / 100.0)

    cluster = [calls[0]]
    rest = []
    for r in calls[1:]:
        if float(r["strike"]) - float(cluster[-1]["strike"]) <= gap_lim:
            cluster.append(r)
        else:
            rest.append(r)
            rest.extend(calls[calls.index(r) + 1 :])
            break

    key = float(cluster[0]["strike"])
    magnet = float(rest[0]["strike"]) if rest else None
    if magnet is None and len(cluster) >= 3:
        magnet = float(cluster[-1]["strike"])

    if len(cluster) >= 2:
        zone_lo = float(cluster[1]["strike"])
        last_c = float(cluster[-1]["strike"])
        if magnet is not None:
            zone_hi = int(min(magnet - 3, last_c + 2))
            if zone_hi < zone_lo:
                zone_hi = int((zone_lo + magnet) / 2)
        else:
            zone_hi = round(last_c + 2.0)
        zone = [round(zone_lo), int(zone_hi)]
        if zone[1] < zone[0]:
            zone[1] = zone[0]
    elif magnet is not None:
        zone = [round(key + 1), round((key + magnet) / 2)]
        if zone[1] < zone[0]:
            zone[1] = zone[0] + 1
    else:
        return None

    hot = any((c.get("voi") or 0) >= _VOI_HOT or (c.get("volume") or 0) >= 1000 for c in cluster)
    if not hot and not volume_spike:
        # 연속 관심만으로도 확장 후보
        if len(cluster) < 2:
            return None

    return {
        "break_level": key,
        "zone": zone,
        "magnet": magnet,
        "cluster": [float(c["strike"]) for c in cluster],
        "volume_spike": volume_spike or hot,
        "note": (
            f"${key:g} 돌파 + 거래량 증가 + 돌파 유지 시 "
            f"${zone[0]}~${zone[1]} 상단 확장"
            + (f", 강세 지속 시 ${magnet:g} 테스트 가능" if magnet else "")
        ),
    }


def detect_downside_expansion(spot: float, rows: list[dict]) -> dict | None:
    puts = [
        r for r in rows
        if str(r.get("type", "")).upper() == "PUT"
        and float(r["strike"]) < spot * 0.998
        and ((r.get("volume") or 0) >= _MIN_VOL_INTEREST or (r.get("oi") or 0) > 0
             or (r.get("voi") or 0) >= _VOI_HOT)
    ]
    if len(puts) < 2:
        return None
    puts.sort(key=lambda r: -float(r["strike"]))
    gap_lim = max(_CLUSTER_GAP_ABS, spot * _CLUSTER_GAP_PCT / 100.0)
    cluster = [puts[0]]
    for r in puts[1:]:
        if float(cluster[-1]["strike"]) - float(r["strike"]) <= gap_lim:
            cluster.append(r)
        else:
            break
    if len(cluster) < 2:
        return None
    key = float(cluster[0]["strike"])
    zone_hi = float(cluster[1]["strike"])
    zone_lo = float(cluster[-1]["strike"])
    return {
        "break_level": key,
        "zone": [round(zone_lo), round(zone_hi)],
        "magnet": float(cluster[-1]["strike"]),
        "cluster": [float(c["strike"]) for c in cluster],
        "note": f"${key:g} 이탈 시 ${round(zone_lo)}~${round(zone_hi)} 하단 확장 가능",
    }


def _confirmed_from_prev(prev: dict | None, strike: float, side: str) -> bool:
    """전일에도 같은 가격이 후보였고, 가격이 그 근처에서 반응한 경우만 강한 레벨."""
    if not prev:
        return False
    lv = ((prev.get("metrics") or {}).get("levels") or {})
    keys = ("near_resistance", "strong_resistance") if side == "resist" else (
        "near_support", "strong_support"
    )
    found = False
    for k in keys:
        for it in lv.get(k) or []:
            if abs(float(it.get("strike") or 0) - strike) < 0.05:
                found = True
                break
    if not found:
        return False
    prev_spot = _f(prev.get("spot"))
    if prev_spot is None:
        return False
    # 전일 종가가 레벨 1% 안에서 막힘/지지
    return distance_pct(prev_spot, strike) <= 1.5


def build_levels(
    base: dict,
    spot: float,
    data: dict | None = None,
    prev: dict | None = None,
    today_ohlc: dict | None = None,
) -> dict:
    """관심 가격 맵. 키 이름은 기존 리포트 호환을 위해 유지하되 의미는 후보."""
    low_conf = is_low_confidence(base, data)
    rows = _merge_interest_rows(base)
    max_oi = max((r.get("oi") or 0) for r in rows) or 1
    max_vol = max((r.get("volume") or 0) for r in rows) or 1
    prev_spot = _f((prev or {}).get("spot"))
    hi = _f((today_ohlc or {}).get("high"))
    lo = _f((today_ohlc or {}).get("low"))
    vol_anom = (data or {}).get("volume_anomaly") or {}
    spike = bool(vol_anom.get("is_anomaly"))

    items: list[dict] = []
    for r in rows:
        strike = float(r["strike"])
        opt = str(r.get("type") or "CALL").upper()
        if low_conf:
            role = {
                "role": "interest",
                "label": "옵션 관심 가격",
                "side": "resist" if strike > spot else "support",
            }
            confirmed = False
        else:
            confirmed = _confirmed_from_prev(
                prev, strike, "resist" if strike >= spot else "support"
            )
            role = _role_for_strike(
                strike, spot, opt,
                prev_spot=prev_spot, today_high=hi, today_low=lo, confirmed=confirmed,
            )
        score = importance_score(
            spot, strike,
            oi=r.get("oi") or 0,
            volume=r.get("volume") or 0,
            voi=r.get("voi"),
            reacted=confirmed or role["role"].startswith("broken"),
            max_oi=max_oi,
            max_vol=max_vol,
        )
        meaning = _meaning(r, role, low_conf)
        items.append({
            "strike": strike,
            "oi": r.get("oi") or 0,
            "volume": r.get("volume") or 0,
            "voi": r.get("voi"),
            "type": opt,
            "kind": role["label"],
            "role": role["role"],
            "side": role["side"],
            "basis": "옵션 포지션/거래 집중",
            "meaning": meaning,
            "score": score,
            "confirmed": confirmed and not low_conf,
        })

    items.sort(key=lambda x: (-x["score"], distance_pct(spot, x["strike"])))

    expansion_up = None if low_conf else detect_upside_expansion(
        spot, rows, volume_spike=spike
    )
    expansion_down = None if low_conf else detect_downside_expansion(spot, rows)

    def _take(side: str, roles: tuple[str, ...], n: int = 3) -> list[dict]:
        out = [it for it in items if it["side"] == side and it["role"] in roles]
        return out[:n]

    # 호환 키: 강한* 는 확인된 경우만, 그 외는 후보
    strong_res = _take("resist", ("strong_resist",), 2)
    strong_sup = _take("support", ("strong_support", "broken_resist_now_support"), 2)
    near_res = _take("resist", ("resist_candidate", "testing", "failed_breakout", "interest"), 3)
    near_sup = _take("support", ("support_candidate", "interest", "testing"), 3)

    # 현재가 위/아래 필터
    near_res = [x for x in near_res if x["strike"] >= spot * 0.995]
    near_sup = [x for x in near_sup if x["strike"] <= spot * 1.005]
    strong_res = [x for x in strong_res if x["strike"] >= spot * 0.995]
    strong_sup = [x for x in strong_sup if x["strike"] <= spot * 1.005]

    flipped_to_support = [x for x in items if x["role"] == "broken_resist_now_support"]
    flipped_to_resist = [x for x in items if x["role"] == "failed_breakout"]

    ranked = [
        {
            "strike": it["strike"],
            "score": it["score"],
            "label": it["kind"],
            "dist_pct": round(distance_pct(spot, it["strike"]), 1),
            "oi": it["oi"],
            "volume": it["volume"],
        }
        for it in items[:8]
    ]

    return {
        "strong_support": strong_sup,
        "strong_resistance": strong_res,
        "near_support": near_sup,
        "near_resistance": near_res,
        "flipped_to_support": flipped_to_support,
        "flipped_to_resist": flipped_to_resist,
        "interest_all": items,
        "ranked": ranked,
        "expansion_up": expansion_up,
        "expansion_down": expansion_down,
        "has_oi_levels": not low_conf and bool(
            (base.get("expiry_metrics") or {}).get("this_week", {}).get("call_oi_clusters")
            or (base.get("expiry_metrics") or {}).get("this_week", {}).get("put_oi_clusters")
        ),
        "low_confidence": low_conf,
        "key_price": (near_res[0] if near_res else None) or (near_sup[0] if near_sup else None),
    }


def _meaning(r: dict, role: dict, low_conf: bool) -> str:
    s = float(r["strike"])
    oi = r.get("oi") or 0
    vol = r.get("volume") or 0
    if low_conf:
        bits = [f"${s:g}는 옵션 관심 가격"]
        if vol:
            bits.append(f"거래 {vol:,}계약")
        bits.append("OI 없어 지지/저항으로 읽지 않음")
        return " · ".join(bits)
    bits = [f"${s:g}에 옵션 포지션이 많이 쌓여 있음"]
    if oi:
        bits.append(f"OI {oi:,}")
    if vol:
        bits.append(f"거래 {vol:,}")
    bits.append(f"→ {role['label']} (단정 아님)")
    return " · ".join(bits)


def band_breakout_signal(base: dict, spot: float) -> dict | None:
    """밴드는 예상 변동 범위. 상단=저항으로 쓰지 않고, 돌파 시 확장 신호만."""
    st = ((base.get("expiry_metrics") or {}).get("this_week") or {}).get("straddle") or {}
    upper = _f(st.get("upper"))
    lower = _f(st.get("lower"))
    if upper is None and lower is None:
        return None
    if upper is not None and spot > upper:
        return {
            "side": "up",
            "level": upper,
            "text": (
                f"🚀 예상 밴드 상단(${upper:g}) 돌파 — "
                "변동성 확대/상단 확장 가능성"
            ),
        }
    if lower is not None and spot < lower:
        return {
            "side": "down",
            "level": lower,
            "text": (
                f"예상 밴드 하단(${lower:g}) 이탈 — "
                "변동성 확대/하단 확장 가능성"
            ),
        }
    return None
