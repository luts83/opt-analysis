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


def sentiment_from_ratio(ratio: float | None) -> str:
    if ratio is None:
        return "중립"
    if ratio >= 1.2:
        return "강세"
    if ratio <= 0.83:
        return "약세"
    return "중립"


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

    return {
        "call_put_volume_ratio": round(ratio, 3) if ratio is not None else None,
        "sentiment": sentiment_from_ratio(ratio),
        "sentiment_rule": "콜/풋 볼륨비 >=1.2 강세 / <=0.83 약세 / 그 외 중립",
        "total_call_volume": call_vol,
        "total_put_volume": put_vol,
        "total_volume": _total_volume(data),
        "total_open_interest": total_open_interest(data),
        "oi_available": oi_available,
        "expiry_metrics": expiry_metrics,
        "top_voi": voi_candidates[: config.VOI_TOP_N],
        "top_call_volume": _volume_rank(call_vol_rows, config.TOP_VOLUME_N),
        "top_put_volume": _volume_rank(put_vol_rows, config.TOP_VOLUME_N),
    }


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
