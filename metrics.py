"""옵션 지표 계산.

지표:
1. V/OI 비율 + 분류 (<1 조용함 / 1~2 활발 / 2~5 뜨거움 / 5+ 극단)
2. OI 변화율 (어제 대비)
3. Volume 이상 감지 (최근 평균 대비 N배)
4. 콜/풋 OI 밀집 지점 (만기별 top N strike)
5. Straddle 예상 밴드 (ATM 콜 lastPrice + ATM 풋 lastPrice)
6. 콜/풋 볼륨 비율

추가(리뷰 피드백 반영):
- 개별 옵션 행에 voi / openInterest_change_pct / volume_vs_avg_ratio 필드 삽입 (enrich_contracts)
- top_voi 는 최소 볼륨 필터 적용, top_volume(절대 볼륨) 리스트 별도 제공
- anomalies: OI 급변을 구조화 배열 + message 로 생성 (build_anomalies)
"""
from __future__ import annotations

import config

# ------------------------------------------------------------------ #
# 1. V/OI
# ------------------------------------------------------------------ #

def voi_ratio(volume: float, open_interest: float) -> float | None:
    """volume / open_interest. OI 가 0 이면 None."""
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
    """(오늘 OI - 어제 OI) / 어제 OI. 어제 값이 없거나 0이면 None."""
    if yesterday_oi is None or yesterday_oi <= 0:
        return None
    return (today_oi - yesterday_oi) / yesterday_oi


# ------------------------------------------------------------------ #
# 3. Volume 이상 감지
# ------------------------------------------------------------------ #

def volume_anomaly(today_volume: float, history_volumes: list[float]) -> bool:
    """오늘 거래량이 최근 평균 대비 배수 이상인지."""
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
    """콜/풋 볼륨 비율 기반 심리 판단.

    ratio = 콜 볼륨 합 / 풋 볼륨 합
    - ratio >= 1.2  → 강세 (콜 매수 우위)
    - ratio <= 0.83 → 약세 (풋 매수 우위)
    - 그 사이        → 중립
    """
    if ratio is None:
        return "중립"
    if ratio >= 1.2:
        return "강세"
    if ratio <= 0.83:
        return "약세"
    return "중립"


# ------------------------------------------------------------------ #
# 개별 옵션 행 보강 (P1, P5)
# ------------------------------------------------------------------ #

def _iter_rows(data: dict):
    """(role, opt_type, leg_date, row) 를 순회."""
    for role, leg in data["expiries"].items():
        for opt_type, rows in (("CALL", leg["calls"]), ("PUT", leg["puts"])):
            for r in rows:
                yield role, opt_type, leg["date"], r


def _index_contracts(data: dict) -> dict[tuple, dict]:
    """(role, opt_type, strike) -> row 인덱스."""
    return {
        (role, opt_type, r["strike"]): r
        for role, opt_type, _date, r in _iter_rows(data)
    }


def _history_volume_index(history: list[dict]) -> dict[tuple, list[float]]:
    """이력 스냅샷들에서 (role, opt_type, strike) -> 볼륨 리스트."""
    idx: dict[tuple, list[float]] = {}
    for h in history:
        for role, opt_type, _date, r in _iter_rows(h):
            idx.setdefault((role, opt_type, r["strike"]), []).append(
                r.get("volume", 0)
            )
    return idx


def enrich_contracts(
    data: dict, prev: dict | None, history: list[dict], oi_stale: bool = False
) -> None:
    """각 옵션 행에 voi / openInterest_change_pct / volume_vs_avg_ratio 를 채운다.

    (data 를 제자리에서 수정 → 스냅샷에 그대로 저장됨)
    oi_stale 이면 OI 파생 필드(voi, openInterest_change_pct)는 신뢰 불가라 None.
    """
    prev_idx = _index_contracts(prev) if prev else {}
    hist_vol = _history_volume_index(history)

    for role, opt_type, _date, r in _iter_rows(data):
        oi = r.get("openInterest", 0)
        vol = r.get("volume", 0)
        key = (role, opt_type, r["strike"])

        if oi_stale:
            r["voi"] = None
            r["openInterest_change_pct"] = None
        else:
            v = voi_ratio(vol, oi)
            r["voi"] = round(v, 3) if v is not None else None
            pr = prev_idx.get(key)
            rate = oi_change_rate(oi, pr.get("openInterest") if pr else None)
            r["openInterest_change_pct"] = (
                round(rate * 100, 1) if rate is not None else None
            )

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
    total = 0
    for leg in data["expiries"].values():
        total += sum(r.get("volume", 0) for r in leg["calls"])
        total += sum(r.get("volume", 0) for r in leg["puts"])
    return total


def total_open_interest(data: dict) -> int:
    return sum(r.get("openInterest", 0) for _r, _t, _d, r in _iter_rows(data))


def is_oi_stale(data: dict) -> bool:
    """OI 데이터가 아직 갱신되지 않았는지(장 시작 전/거래소 지연) 판단.

    yfinance 의 openInterest 는 장 마감 후 갱신되므로, 장 시작 전에는
    거의 모든 계약이 0으로 내려온다. 이 상태를 그대로 쓰면 '전 계약 -100%
    청산' 같은 가짜 신호가 발생하므로 별도 처리한다.

    판정: 전체 계약 중 OI==0 비율이 임계(기본 90%) 이상이면 stale.
    (떠돌이 계약 1~2개가 0이 아니어도 흔들리지 않게 '전부 0'이 아닌 비율로 판단)
    """
    total = 0
    zeros = 0
    for _r, _t, _d, r in _iter_rows(data):
        total += 1
        if r.get("openInterest", 0) == 0:
            zeros += 1
    if total == 0:
        return False
    return (zeros / total) >= config.OI_STALE_ZERO_FRACTION


def build_base_metrics(
    data: dict, prev: dict | None = None, oi_stale: bool = False
) -> dict:
    """오늘 데이터만으로 계산 가능한 지표. (enrich_contracts 이후 호출)

    oi_stale 이면 OI 기반 지표(클러스터)는 전일(prev) 값으로 폴백한다.
    """
    spot = data["spot"]
    ratio, call_vol, put_vol = call_put_volume_ratio(data)

    prev_em = (prev or {}).get("metrics", {}).get("expiry_metrics", {})

    expiry_metrics: dict[str, dict] = {}
    for role, leg in data["expiries"].items():
        if oi_stale and prev_em.get(role):
            # OI 미갱신 → 전일 클러스터를 그대로 사용 (마지막으로 알려진 값)
            call_clusters = prev_em[role].get("call_oi_clusters", [])
            put_clusters = prev_em[role].get("put_oi_clusters", [])
        else:
            call_clusters = oi_clusters(leg["calls"])   # 저항선 후보
            put_clusters = oi_clusters(leg["puts"])      # 지지선 후보
        expiry_metrics[role] = {
            "date": leg["date"],
            "straddle": straddle_band(spot, leg["calls"], leg["puts"]),
            "call_oi_clusters": call_clusters,
            "put_oi_clusters": put_clusters,
        }

    # V/OI 상위 (P2: 최소 볼륨 필터로 노이즈 제거)
    voi_candidates = []
    volume_candidates = []
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
        }
        volume_candidates.append(entry)
        if r.get("voi") is not None and oi >= config.VOI_MIN_OI and vol >= config.VOI_MIN_VOLUME:
            voi_candidates.append(entry)

    voi_candidates.sort(key=lambda x: x["voi"], reverse=True)
    volume_candidates.sort(key=lambda x: x["volume"], reverse=True)

    return {
        "call_put_volume_ratio": round(ratio, 3) if ratio is not None else None,
        "sentiment": sentiment_from_ratio(ratio),
        "sentiment_rule": "콜/풋 볼륨비 >=1.2 강세 / <=0.83 약세 / 그 외 중립",
        "total_call_volume": call_vol,
        "total_put_volume": put_vol,
        "total_volume": _total_volume(data),
        "total_open_interest": total_open_interest(data),
        "oi_data_stale": oi_stale,
        "oi_source": "전일 스냅샷 기준 (오늘 OI 미갱신)" if oi_stale else "오늘",
        "expiry_metrics": expiry_metrics,
        "top_voi": voi_candidates[: config.VOI_TOP_N],
        "top_volume": volume_candidates[: config.TOP_VOLUME_N],
    }


# ------------------------------------------------------------------ #
# 이상 신호(anomalies) 구조화 (P3)
# ------------------------------------------------------------------ #

def build_anomalies(
    data: dict, prev: dict | None, oi_stale: bool = False
) -> list[dict]:
    """어제 대비 OI 급변을 구조화 배열로 생성한다.

    oi_stale(오늘 OI 미갱신) 이면 가짜 '-100% 청산' 신호를 막기 위해 건너뛴다.
    """
    anomalies: list[dict] = []
    if not prev or oi_stale:
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
    """전체 거래량이 최근 평균 대비 이상인지."""
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
