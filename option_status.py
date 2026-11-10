"""옵션 체인 → 보험 비유 상태 요약 (텔레그램 3~6줄).

상세 수치·판단 근거는 스냅샷 JSON(option_status)에만 저장.
텔레그램은 고정 템플릿 + 조건 분기. LLM 미사용.
"""
from __future__ import annotations

from typing import Any

import config

_DISCLAIMER = "이 해석은 방향 예측이 아닙니다. 옵션 시장의 가격·거래 구성만 요약한 것입니다."


def _cfg() -> dict[str, Any]:
    raw = getattr(config, "OPTION_STATUS", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except (TypeError, ValueError):
        return None


def _pick_role(data: dict) -> tuple[str, dict]:
    expiries = data.get("expiries") or {}
    for role in ("this_week", "next_week", "monthly"):
        leg = expiries.get(role)
        if isinstance(leg, dict) and (leg.get("calls") or leg.get("puts")):
            return role, leg
    if expiries:
        role, leg = next(iter(expiries.items()))
        return str(role), leg if isinstance(leg, dict) else {}
    return "this_week", {}


def _atm_rows(rows: list[dict], spot: float, band_pct: float) -> list[dict]:
    out: list[dict] = []
    for r in rows or []:
        s = _f(r.get("strike"))
        if s is None or spot <= 0:
            continue
        if abs(s - spot) / spot <= band_pct:
            out.append(r)
    return out


def _sum_field(rows: list[dict], key: str) -> int:
    total = 0
    for r in rows:
        v = _f(r.get(key))
        if v is not None:
            total += int(v)
    return total


def _mean_iv(rows: list[dict]) -> float | None:
    vals = [_f(r.get("impliedVolatility")) for r in rows]
    vals = [v for v in vals if v is not None and v > 0]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0


def _spread_stats(rows: list[dict]) -> dict[str, Any]:
    spreads: list[float] = []
    near_mid = 0
    priced = 0
    for r in rows:
        bid = _f(r.get("bid"))
        ask = _f(r.get("ask"))
        last = _f(r.get("lastPrice"))
        if bid is None or ask is None or ask <= 0 or bid < 0 or ask < bid:
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        spreads.append((ask - bid) / mid)
        priced += 1
        if last is not None and abs(last - mid) / mid <= 0.15:
            near_mid += 1
    return {
        "median_spread_pct": round((_median(spreads) or 0) * 100, 2) if spreads else None,
        "near_mid_ratio": round(near_mid / priced, 3) if priced else None,
        "n_quoted": priced,
    }


def _band_pct_fallback(data: dict, role: str) -> float | None:
    m = data.get("metrics") or {}
    bt = m.get("band_trend") or {}
    for row in bt.get("rows") or []:
        if row.get("role") == role:
            return _f(row.get("band_pct"))
    em = (m.get("expiry_metrics") or {}).get(role) or {}
    sb = em.get("straddle_band") or {}
    return _f(sb.get("band_pct"))


def _premium_level(
    atm_iv: float | None, band_pct: float | None, cfg: dict
) -> tuple[str, str]:
    """(level, source). level: high|mid|low."""
    iv_high = float(cfg.get("iv_high", 0.60))
    iv_low = float(cfg.get("iv_low", 0.35))
    band_high = float(cfg.get("band_pct_high", 15.0))
    band_low = float(cfg.get("band_pct_low", 8.0))

    if atm_iv is not None:
        if atm_iv >= iv_high:
            return "high", "atm_iv"
        if atm_iv < iv_low:
            return "low", "atm_iv"
        return "mid", "atm_iv"

    if band_pct is not None:
        if band_pct >= band_high:
            return "high", "band_pct"
        if band_pct < band_low:
            return "low", "band_pct"
        return "mid", "band_pct"

    return "mid", "default"


def _balance_ratio(a: float, b: float) -> float | None:
    """min/max — 1에 가까울수록 균형."""
    if a <= 0 and b <= 0:
        return None
    mx = max(a, b)
    mn = min(a, b)
    if mx <= 0:
        return None
    return mn / mx


def _seller_signals(
    *,
    premium: str,
    atm_balance: float | None,
    cpr: float | None,
    spread: dict[str, Any],
    aggressive_buy_weak: bool,
    cfg: dict,
) -> list[dict[str, Any]]:
    """팔려는 쪽 우세 후보 신호들 (True인 것만 반환하지 않고 전부 기록)."""
    bal_min = float(cfg.get("balance_min", 0.45))
    cpr_lo = float(cfg.get("cpr_extreme_low", 0.40))
    cpr_hi = float(cfg.get("cpr_extreme_high", 2.50))
    spread_max = float(cfg.get("spread_median_max", 0.25))  # 25% of mid
    near_mid_min = float(cfg.get("near_mid_min", 0.40))

    signals: list[dict[str, Any]] = []

    # 1) ATM 근처 IV 높음 + vol/OI 콜·풋 고르게
    even = atm_balance is not None and atm_balance >= bal_min
    high_iv = premium == "high"
    hit1 = high_iv and even
    signals.append(
        {
            "id": "atm_high_iv_even",
            "hit": hit1,
            "detail": {
                "premium": premium,
                "atm_balance": round(atm_balance, 3) if atm_balance is not None else None,
            },
        }
    )

    # 2) 스프레드 좁거나 last≈mid
    med = spread.get("median_spread_pct")
    near = spread.get("near_mid_ratio")
    tight = (med is not None and med / 100.0 <= spread_max) or (
        near is not None and near >= near_mid_min
    )
    signals.append(
        {
            "id": "tight_spread_or_near_mid",
            "hit": bool(tight and spread.get("n_quoted")),
            "detail": {"median_spread_pct": med, "near_mid_ratio": near},
        }
    )

    # 3) 단기 공격적 매수 흔적 약함
    signals.append(
        {
            "id": "weak_aggressive_buy",
            "hit": bool(aggressive_buy_weak),
            "detail": {"weak": aggressive_buy_weak},
        }
    )

    # 4) 콜/풋 volume 비율이 극단적이지 않음
    cpr_ok = cpr is not None and cpr_lo <= cpr <= cpr_hi
    signals.append(
        {
            "id": "cpr_not_extreme",
            "hit": bool(cpr_ok),
            "detail": {"cpr": round(cpr, 3) if cpr is not None else None},
        }
    )
    return signals


def _aggressive_buy_weak(
    atm_calls: list[dict],
    atm_puts: list[dict],
    prev_leg: dict | None,
    cfg: dict,
) -> bool:
    """전일 대비 ATM volume 급증이 약하면 True (매수 공세 약함)."""
    surge_mult = float(cfg.get("aggressive_vol_mult", 2.5))
    min_vol = int(cfg.get("aggressive_min_vol", 200))

    def _vol_map(leg: dict | None) -> dict[tuple[str, float], int]:
        out: dict[tuple[str, float], int] = {}
        if not leg:
            return out
        for side, key in (("CALL", "calls"), ("PUT", "puts")):
            for r in leg.get(key) or []:
                s = _f(r.get("strike"))
                if s is None:
                    continue
                out[(side, float(s))] = int(_f(r.get("volume")) or 0)
        return out

    prev_map = _vol_map(prev_leg)
    today_rows = [("CALL", r) for r in atm_calls] + [("PUT", r) for r in atm_puts]
    if not today_rows:
        return True

    surges = 0
    checked = 0
    for side, r in today_rows:
        s = _f(r.get("strike"))
        vol = int(_f(r.get("volume")) or 0)
        if s is None or vol < min_vol:
            continue
        checked += 1
        prev_v = prev_map.get((side, float(s)), 0)
        if prev_v <= 0:
            # 전일 없음 + 오늘만 크면 급증으로 봄
            if vol >= min_vol * 2:
                surges += 1
            continue
        if vol / max(prev_v, 1) >= surge_mult:
            surges += 1

    if checked == 0:
        # 거래 자체가 적음 → 공격적 매수 약함
        return True
    # 급증 비율이 낮으면 약함
    return (surges / checked) < 0.25


def _iv_at_distance(
    calls: list[dict], puts: list[dict], spot: float, dist_pct: float
) -> tuple[float | None, float | None]:
    """현가에서 dist_pct 위 콜 / 아래 풋의 IV."""
    target_c = spot * (1 + dist_pct)
    target_p = spot * (1 - dist_pct)

    def nearest(rows: list[dict], target: float) -> float | None:
        best = None
        best_d = None
        for r in rows or []:
            s = _f(r.get("strike"))
            iv = _f(r.get("impliedVolatility"))
            if s is None or iv is None or iv <= 0:
                continue
            d = abs(s - target)
            if best_d is None or d < best_d:
                best_d = d
                best = iv
        return best

    return nearest(calls, target_c), nearest(puts, target_p)


def _direction_signals(
    *,
    call_vol: int,
    put_vol: int,
    call_oi: int,
    put_oi: int,
    call_iv: float | None,
    put_iv: float | None,
    call_act: int,
    put_act: int,
    cfg: dict,
) -> list[dict[str, Any]]:
    vol_ratio = float(cfg.get("dir_vol_ratio", 1.35))
    act_ratio = float(cfg.get("dir_activity_ratio", 1.25))
    iv_eps = float(cfg.get("dir_iv_eps", 0.02))

    signals: list[dict[str, Any]] = []

    # Volume / OI
    direction = None
    if call_vol > 0 or put_vol > 0:
        if put_vol > 0 and call_vol / max(put_vol, 1) >= vol_ratio:
            direction = "up"
        elif call_vol > 0 and put_vol / max(call_vol, 1) >= vol_ratio:
            direction = "down"
    if direction is None and (call_oi > 0 or put_oi > 0):
        if put_oi > 0 and call_oi / max(put_oi, 1) >= vol_ratio:
            direction = "up"
        elif call_oi > 0 and put_oi / max(call_oi, 1) >= vol_ratio:
            direction = "down"
    signals.append(
        {
            "id": "volume_oi",
            "direction": direction,
            "detail": {
                "call_vol": call_vol,
                "put_vol": put_vol,
                "call_oi": call_oi,
                "put_oi": put_oi,
            },
        }
    )

    # IV skew (same distance): call IV >= put IV → up tilt; put > call → down
    skew_dir = None
    if call_iv is not None and put_iv is not None:
        if call_iv >= put_iv + iv_eps:
            skew_dir = "up"
        elif put_iv > call_iv + iv_eps:
            skew_dir = "down"
    signals.append(
        {
            "id": "iv_skew",
            "direction": skew_dir,
            "detail": {
                "call_iv": round(call_iv, 4) if call_iv is not None else None,
                "put_iv": round(put_iv, 4) if put_iv is not None else None,
            },
        }
    )

    # Activity concentration (ATM± band volume)
    act_dir = None
    if call_act > 0 or put_act > 0:
        if put_act > 0 and call_act / max(put_act, 1) >= act_ratio:
            act_dir = "up"
        elif call_act > 0 and put_act / max(call_act, 1) >= act_ratio:
            act_dir = "down"
    signals.append(
        {
            "id": "activity_focus",
            "direction": act_dir,
            "detail": {"call_act": call_act, "put_act": put_act},
        }
    )
    return signals


def analyze(data: dict, prev: dict | None = None) -> dict[str, Any]:
    """상태 분석 → verdict dict (텔레그램용 + detail용 공통)."""
    cfg = _cfg()
    spot = _f(data.get("spot")) or 0.0
    role, leg = _pick_role(data)
    band = float(cfg.get("atm_band_pct", 0.05))

    calls = list(leg.get("calls") or [])
    puts = list(leg.get("puts") or [])
    atm_c = _atm_rows(calls, spot, band)
    atm_p = _atm_rows(puts, spot, band)

    call_vol = _sum_field(calls, "volume")
    put_vol = _sum_field(puts, "volume")
    call_oi = _sum_field(calls, "openInterest")
    put_oi = _sum_field(puts, "openInterest")
    atm_call_vol = _sum_field(atm_c, "volume")
    atm_put_vol = _sum_field(atm_p, "volume")
    atm_call_oi = _sum_field(atm_c, "openInterest")
    atm_put_oi = _sum_field(atm_p, "openInterest")

    cpr = (call_vol / put_vol) if put_vol > 0 else None
    bal_vol = _balance_ratio(float(atm_call_vol), float(atm_put_vol))
    bal_oi = _balance_ratio(float(atm_call_oi), float(atm_put_oi))
    atm_balance = None
    if bal_vol is not None and bal_oi is not None:
        atm_balance = (bal_vol + bal_oi) / 2.0
    elif bal_vol is not None:
        atm_balance = bal_vol
    elif bal_oi is not None:
        atm_balance = bal_oi

    atm_iv_c = _mean_iv(atm_c)
    atm_iv_p = _mean_iv(atm_p)
    atm_iv = None
    if atm_iv_c is not None and atm_iv_p is not None:
        atm_iv = (atm_iv_c + atm_iv_p) / 2.0
    elif atm_iv_c is not None:
        atm_iv = atm_iv_c
    elif atm_iv_p is not None:
        atm_iv = atm_iv_p

    band_pct = _band_pct_fallback(data, role)
    premium, premium_src = _premium_level(atm_iv, band_pct, cfg)

    spread = _spread_stats(atm_c + atm_p)

    prev_leg = None
    if prev and isinstance(prev.get("expiries"), dict):
        prev_leg = (prev.get("expiries") or {}).get(role)

    weak_buy = _aggressive_buy_weak(atm_c, atm_p, prev_leg, cfg)

    seller_sigs = _seller_signals(
        premium=premium,
        atm_balance=atm_balance,
        cpr=cpr,
        spread=spread,
        aggressive_buy_weak=weak_buy,
        cfg=cfg,
    )
    seller_hits = [s for s in seller_sigs if s.get("hit")]
    seller_stance = "sellers" if len(seller_hits) >= 2 else "balanced"

    dist = float(cfg.get("skew_distance_pct", 0.05))
    call_iv_d, put_iv_d = _iv_at_distance(calls, puts, spot, dist)
    # IV 없으면 ATM IV로 스큐 대체
    if call_iv_d is None:
        call_iv_d = atm_iv_c
    if put_iv_d is None:
        put_iv_d = atm_iv_p

    dir_sigs = _direction_signals(
        call_vol=call_vol,
        put_vol=put_vol,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=call_iv_d,
        put_iv=put_iv_d,
        call_act=atm_call_vol,
        put_act=atm_put_vol,
        cfg=cfg,
    )
    up_n = sum(1 for s in dir_sigs if s.get("direction") == "up")
    down_n = sum(1 for s in dir_sigs if s.get("direction") == "down")
    if up_n >= 2 and up_n > down_n:
        tilt = "up"
    elif down_n >= 2 and down_n > up_n:
        tilt = "down"
    else:
        tilt = "none"

    # 만기별 요약 (상세 저장용)
    per_expiry: dict[str, Any] = {}
    for rname, rleg in (data.get("expiries") or {}).items():
        if not isinstance(rleg, dict):
            continue
        rc = rleg.get("calls") or []
        rp = rleg.get("puts") or []
        per_expiry[str(rname)] = {
            "date": rleg.get("date"),
            "call_volume": _sum_field(rc, "volume"),
            "put_volume": _sum_field(rp, "volume"),
            "call_oi": _sum_field(rc, "openInterest"),
            "put_oi": _sum_field(rp, "openInterest"),
            "atm_iv": _mean_iv(_atm_rows(rc, spot, band) + _atm_rows(rp, spot, band)),
            "band_pct": _band_pct_fallback(data, str(rname)),
        }

    return {
        "available": bool(calls or puts) and spot > 0,
        "ticker": data.get("ticker"),
        "date": data.get("date"),
        "spot": spot,
        "role": role,
        "expiry": leg.get("date"),
        "premium": premium,
        "premium_source": premium_src,
        "seller_stance": seller_stance,
        "tilt": tilt,
        "features": {
            "atm_iv": round(atm_iv, 4) if atm_iv is not None else None,
            "band_pct": band_pct,
            "cpr": round(cpr, 3) if cpr is not None else None,
            "call_vol": call_vol,
            "put_vol": put_vol,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "atm_call_vol": atm_call_vol,
            "atm_put_vol": atm_put_vol,
            "atm_balance": round(atm_balance, 3) if atm_balance is not None else None,
            "spread": spread,
            "weak_aggressive_buy": weak_buy,
        },
        "seller_signals": seller_sigs,
        "seller_hit_count": len(seller_hits),
        "direction_signals": dir_sigs,
        "direction_counts": {"up": up_n, "down": down_n},
        "per_expiry": per_expiry,
        "disclaimer": _DISCLAIMER,
    }


def format_telegram(ticker: str, verdict: dict[str, Any]) -> str:
    """지침서 예시 형식의 짧은 한국어 (3~6줄)."""
    t = (ticker or verdict.get("ticker") or "?").upper()
    lines: list[str] = [f"[{t}] 옵션으로 본 현재 상태", ""]

    if not verdict.get("available"):
        lines.append("오늘은 옵션 데이터를 충분히 읽지 못했어요.")
        return "\n".join(lines)

    premium = verdict.get("premium") or "mid"
    stance = verdict.get("seller_stance") or "balanced"
    tilt = verdict.get("tilt") or "none"

    # IV 낮음 → 짧은 예시 3
    if premium == "low":
        lines.append("보험료가 비교적 저렴한 편입니다.")
        lines.append("시장이 큰 움직임을 크게 예상하지 않고 있어요.")
        return "\n".join(lines)

    # 보험료 한 줄
    if premium == "high":
        lines.append("보험료가 비싸게 형성되어 있습니다.")
    else:
        lines.append("보험료는 비싼 편입니다.")

    # 사/팔 한 줄
    if stance == "sellers":
        lines.append("그런데 사려는 사람보다 팔려는 쪽이 더 많아 보여요.")
    else:
        lines.append("사려는 쪽과 팔려는 쪽이 비슷해 보여요.")

    lines.append("")

    # 변동성 해석
    if stance == "sellers" and premium == "high":
        lines.append("크게 움직일 위험은 높게 보고 있지만,")
        lines.append("실제로는 생각보다 안 움직일 가능성이 높아 보입니다.")
    elif stance == "balanced" and premium == "high":
        lines.append("시장이 위험을 크게 보고는 있지만,")
        lines.append("뚜렷한 한쪽 우위는 없는 상태입니다.")
    elif stance == "sellers":
        lines.append("보험을 팔려는 쪽이 조금 더 많아 보여요.")
    else:
        lines.append("뚜렷한 한쪽 우위는 없는 상태입니다.")

    # 조건부 방향
    if tilt == "down":
        lines.append("")
        lines.append(
            "(참고) 풋 쪽 활동이 상대적으로 많아 하락 쪽에 관심이 조금 더 실려 있습니다."
        )
    elif tilt == "up":
        lines.append("")
        lines.append(
            "(참고) 콜 쪽 활동이 상대적으로 많아 상승 쪽에 관심이 조금 더 실려 있습니다."
        )

    return "\n".join(lines)


def to_detail(verdict: dict[str, Any]) -> dict[str, Any]:
    """스냅샷 저장용 (텔레그램에 안 나가는 근거 포함)."""
    return dict(verdict)


def build_for_report(
    data: dict, prev: dict | None = None
) -> tuple[str, dict[str, Any]]:
    """(텔레그램 본문, detail dict)."""
    verdict = analyze(data, prev)
    text = format_telegram(str(data.get("ticker") or ""), verdict)
    return text, to_detail(verdict)
