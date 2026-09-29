"""옵션 ↔ 주가 학습 파이프라인.

흐름 (가격 우선):
  1) 주가 변화 분석
  2) 실제 가격 반응 구간 추출
  3) 전일 옵션 이벤트와 매칭
  4) 옵션↔가격 관계 분류
  5) 과거 동일 셋업 패턴 검색
  6) 오늘의 학습 결과 생성

인과 단정 금지. 거래량≠매수≠방향. 연결률 KPI 사용 안 함.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# 결과 코드 → 한글 (리포트·저장 공통)
RESULT_KO: dict[str, str] = {
    "breakout_success": "돌파 성공",
    "breakout_fail": "돌파 실패",
    "support_success": "지지 성공",
    "support_fail": "지지 실패",
    "approach_reverse": "접근 후 반전",
    "level_pass": "가격대 통과",
    "not_reached": "미도달",
    "mismatch": "옵션-가격 불일치",
    "weak_option": "옵션 근거 부족",
}

# 구형 결과 → 신형 (jsonl 호환)
_LEGACY_RESULT = {
    "hit": "breakout_success",  # side로 재해석 필요할 수 있음
    "partial": "approach_reverse",
    "reject": "breakout_fail",
    "unverified": "not_reached",
}

MIN_PATTERN_SAMPLES = 8


def _cfg() -> dict:
    import config

    return {
        "focus_pct": float(getattr(config, "REPORT_FOCUS_PCT", 0.05)),
        "focus_pct_2": float(getattr(config, "REPORT_FOCUS_PCT_2", 0.08)),
        "event_min_score": int(getattr(config, "REPORT_EVENT_MIN_SCORE", 40)),
        "quiet_mode": str(getattr(config, "REPORT_QUIET_MODE", "one_line")),
        "volume_mult_hi": 1.5,
        "volume_mult_lo": 0.6,
        "min_near_volume": 2000,
        "min_voi_extreme": 5.0,
        "match_pct": 0.015,  # 반응구간↔strike 매칭 허용
        "significant_move_pct": 2.5,
    }


def _root() -> Path:
    import config

    here = Path(__file__).resolve().parent
    base = Path(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else here / base
    d = base / "_learning"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _events_path() -> Path:
    return _root() / "option_events.jsonl"


def _fmt_px(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _dist_pct(strike: float, spot: float) -> float | None:
    if not spot:
        return None
    return abs(float(strike) - float(spot)) / float(spot) * 100


def _norm_result(r: str | None, side: str | None = None) -> str:
    if not r:
        return "not_reached"
    if r in RESULT_KO:
        return r
    mapped = _LEGACY_RESULT.get(r, r)
    if mapped == "breakout_success" and (side or "").upper() == "PUT":
        return "support_success"
    if mapped == "breakout_fail" and (side or "").upper() == "PUT":
        return "support_fail"
    return mapped if mapped in RESULT_KO else "not_reached"


def detect_events_from_snap(
    snap: dict | None,
    *,
    focus_pct: float | None = None,
) -> list[dict[str, Any]]:
    """스냅샷(그날 종가 기준)에서 근거리 옵션 이벤트 후보."""
    if not snap:
        return []
    cfg = _cfg()
    fp = focus_pct if focus_pct is not None else cfg["focus_pct"]
    spot = snap.get("spot") or snap.get("regular_close")
    if spot is None:
        return []
    spot = float(spot)
    m = snap.get("metrics") or {}
    date = str(snap.get("date") or "")
    ticker = str(snap.get("ticker") or "")
    vol_anom = snap.get("volume_anomaly") or {}
    dod = snap.get("day_over_day") or {}
    vm = dod.get("volume_mult")
    if vm is None and vol_anom.get("mult") is not None:
        vm = vol_anom.get("mult")

    events: list[dict[str, Any]] = []

    def _add(
        *,
        strike: float,
        side: str,
        event_type: str,
        volume: int | None = None,
        oi: int | None = None,
        voi: float | None = None,
        expiry: str | None = None,
        score_add: int = 0,
        detail: str = "",
    ) -> None:
        dist = _dist_pct(strike, spot)
        if dist is None or dist > fp * 100:
            return
        key = (round(strike, 2), side, event_type)
        if any(
            round(e["strike"], 2) == key[0]
            and e["side"] == side
            and e["event_type"] == event_type
            for e in events
        ):
            return
        events.append(
            {
                "ticker": ticker,
                "asof": date,
                "strike": float(strike),
                "side": side,  # CALL | PUT
                "dist_pct": round(dist, 2),
                "event_type": event_type,
                "volume": volume,
                "oi": oi,
                "voi": voi,
                "expiry": expiry,
                "volume_mult": float(vm) if vm is not None else None,
                "score_add": score_add,
                "detail": detail,
            }
        )

    # 1) 근거리 콜/풋 거래 집중
    spike_day = bool(vol_anom.get("is_anomaly")) or (
        vm is not None and float(vm) >= cfg["volume_mult_hi"]
    )
    for row in (m.get("top_call_volume") or [])[:5]:
        try:
            s = float(row["strike"])
            vol = int(row.get("volume") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        # 평일: 2000+ / 스파이크일: 1000+ 도 허용
        min_vol = 1000 if spike_day else cfg["min_near_volume"]
        if vol < min_vol:
            continue
        _add(
            strike=s,
            side="CALL",
            event_type="near_volume",
            volume=vol,
            oi=int(row.get("oi") or row.get("openInterest") or 0) or None,
            expiry=row.get("expiry"),
            score_add=25 if vol >= 2000 else 15,
            detail=f"콜 거래 {vol:,}",
        )
    for row in (m.get("top_put_volume") or [])[:5]:
        try:
            s = float(row["strike"])
            vol = int(row.get("volume") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        min_vol = 1000 if spike_day else cfg["min_near_volume"]
        if vol < min_vol:
            continue
        _add(
            strike=s,
            side="PUT",
            event_type="near_volume",
            volume=vol,
            oi=int(row.get("oi") or row.get("openInterest") or 0) or None,
            expiry=row.get("expiry"),
            score_add=25 if vol >= 2000 else 15,
            detail=f"풋 거래 {vol:,}",
        )

    # 2) 근거리 V/OI 극단
    for row in (m.get("top_voi") or [])[:5]:
        try:
            s = float(row["strike"])
            voi = float(row.get("voi") or row.get("voi_ratio") or 0)
            side = str(row.get("type") or row.get("opt_type") or "CALL").upper()
            if side not in ("CALL", "PUT"):
                side = "CALL"
        except (TypeError, ValueError, KeyError):
            continue
        if voi < cfg["min_voi_extreme"]:
            continue
        _add(
            strike=s,
            side=side,
            event_type="voi_extreme",
            volume=int(row.get("volume") or 0) or None,
            oi=int(row.get("oi") or 0) or None,
            voi=round(voi, 2),
            expiry=row.get("expiry"),
            score_add=30 if voi >= 5 else 20,
            detail=f"V/OI {voi:.1f}",
        )

    # 3) 총거래량 스파이크 → 근거리 대표 strike에 volume_spike 태그
    spike = spike_day
    if spike and events:
        for e in events:
            e["score_add"] = int(e.get("score_add") or 0) + 15
            if e["event_type"] == "near_volume":
                e["event_type"] = "volume_spike_near"
                e["detail"] = (e.get("detail") or "") + (
                    f" · 총거래 {float(vm):.1f}배" if vm else " · 거래 급증"
                )
    elif spike:
        # 스파이크만 있고 근거리 top이 약하면 가장 가까운 콜 1개
        calls = m.get("top_call_volume") or []
        best = None
        best_d = 99.0
        for row in calls[:8]:
            try:
                s = float(row["strike"])
                d = _dist_pct(s, spot)
                if d is not None and d < best_d and d <= fp * 100:
                    best_d = d
                    best = row
            except (TypeError, ValueError, KeyError):
                continue
        if best:
            _add(
                strike=float(best["strike"]),
                side="CALL",
                event_type="volume_spike_near",
                volume=int(best.get("volume") or 0) or None,
                expiry=best.get("expiry"),
                score_add=35,
                detail=f"총거래 {float(vm):.1f}배" if vm else "거래 급증",
            )

    # 점수 필드 + strike·side 중복 병합(높은 점수 유지)
    for e in events:
        e["score"] = min(100, int(e.get("score_add") or 0) + max(0, 10 - int(e.get("dist_pct") or 10)))
    merged: dict[tuple, dict] = {}
    for e in events:
        key = (round(float(e["strike"]), 2), e["side"])
        prev = merged.get(key)
        if prev is None or (e.get("score") or 0) > (prev.get("score") or 0):
            merged[key] = e
    events = sorted(merged.values(), key=lambda x: (-x.get("score", 0), x.get("dist_pct") or 99))
    return events[:4]


def _is_meaningful(e: dict) -> bool:
    """장문 리포트를 열 만큼 강한 이벤트인가."""
    et = e.get("event_type") or ""
    vol = int(e.get("volume") or 0)
    voi = float(e.get("voi") or 0)
    if et == "volume_spike_near":
        return True
    if et == "near_volume" and vol >= 8000:
        return True
    if et == "voi_extreme" and voi >= 20 and vol >= 1500:
        return True
    return False


def day_event_score(
    *,
    prev_events: list[dict],
    today_events: list[dict],
    dod: dict | None = None,
    vol_anom: dict | None = None,
) -> dict[str, Any]:
    """오늘 리포트가 '이벤트 데이'인지."""
    cfg = _cfg()
    dod = dod or {}
    vol_anom = vol_anom or {}
    vm = dod.get("volume_mult")
    if vm is None:
        vm = vol_anom.get("mult")
    reasons: list[str] = []
    score = 0

    m_prev = [e for e in prev_events if _is_meaningful(e)]
    m_today = [e for e in today_events if _is_meaningful(e)]

    if m_prev:
        top = max(e.get("score") or 0 for e in m_prev)
        score += min(50, top)
        reasons.append(f"전일 강한 이벤트 {len(m_prev)}건")
    if m_today:
        top = max(e.get("score") or 0 for e in m_today)
        score += min(40, int(top * 0.8))
        reasons.append(f"당일 강한 이벤트 {len(m_today)}건")
    if vm is not None and float(vm) >= cfg["volume_mult_hi"]:
        score += 25
        reasons.append(f"옵션거래 {float(vm):.1f}배")
    elif vm is not None and float(vm) <= cfg["volume_mult_lo"]:
        score = max(0, score - 10)
        reasons.append(f"옵션거래 저조 {float(vm):.1f}배")
    if vol_anom.get("is_anomaly"):
        score += 15
        reasons.append("거래량 이상")

    score = min(100, int(score))
    # 약한 V/OI만으로는 장문 안 씀 — 의미 있는 이벤트 또는 거래 급증+근거리
    vol_spike = vm is not None and float(vm) >= cfg["volume_mult_hi"]
    is_event = bool(m_prev or m_today) or (
        vol_spike and bool(prev_events or today_events) and score >= cfg["event_min_score"]
    )
    if not reasons:
        reasons.append("근거리 강한 이벤트 없음")
    return {
        "score": score,
        "is_event_day": is_event,
        "reasons": reasons,
        "volume_mult": float(vm) if vm is not None else None,
        "n_prev": len(prev_events),
        "n_today": len(today_events),
        "n_meaningful_prev": len(m_prev),
        "n_meaningful_today": len(m_today),
        "quiet_mode": cfg["quiet_mode"],
        "event_min_score": cfg["event_min_score"],
    }


def analyze_price_day(
    ohlc: dict | None,
    *,
    prev_close: float | None = None,
) -> dict[str, Any]:
    """[1] 주가 변화 분석."""
    if not ohlc or ohlc.get("close") is None:
        return {"available": False}
    try:
        o = float(ohlc.get("open") or prev_close or ohlc["close"])
        hi = float(ohlc.get("high") or ohlc["close"])
        lo = float(ohlc.get("low") or ohlc["close"])
        cl = float(ohlc["close"])
    except (TypeError, ValueError):
        return {"available": False}
    base = float(prev_close) if prev_close else o
    ret = ohlc.get("return_pct")
    if ret is None and base:
        ret = round((cl - base) / base * 100, 2)
    range_pct = round((hi - lo) / o * 100, 2) if o else 0.0
    direction = "up" if (ret or 0) > 0.3 else ("down" if (ret or 0) < -0.3 else "flat")
    return {
        "available": True,
        "open": o,
        "high": hi,
        "low": lo,
        "close": cl,
        "prev_close": base,
        "return_pct": ret,
        "range_pct": range_pct,
        "direction": direction,
        "significant": abs(ret or 0) >= _cfg()["significant_move_pct"]
        or range_pct >= 3.5,
    }


def extract_reaction_zones(price: dict) -> list[dict[str, Any]]:
    """[2] 실제 가격 반응 구간 (고·저·의미 있는 종가)."""
    if not price.get("available"):
        return []
    hi, lo, cl = price["high"], price["low"], price["close"]
    zones = [
        {"kind": "high", "price": hi, "label": "오늘 고점"},
        {"kind": "low", "price": lo, "label": "오늘 저점"},
    ]
    # 고저 폭이 있으면 종가도 앵커
    if price.get("range_pct", 0) >= 1.0:
        zones.append({"kind": "close", "price": cl, "label": "종가"})
    return zones


def classify_relation(
    strike: float,
    side: str,
    ohlc: dict,
    *,
    has_strong_option: bool = True,
) -> dict[str, Any]:
    """[4] 옵션↔가격 관계 분류 (신 라벨)."""
    if not has_strong_option:
        return {
            "result": "weak_option",
            "note": f"{_fmt_px(strike)} · 옵션 근거 약함",
        }
    hi = ohlc.get("high")
    lo = ohlc.get("low")
    cl = ohlc.get("close")
    op = ohlc.get("open")
    if hi is None or lo is None or cl is None:
        return {"result": "not_reached", "note": "OHLC 없음"}
    hi, lo, cl = float(hi), float(lo), float(cl)
    op = float(op) if op is not None else cl
    s = float(strike)
    side = side.upper()

    if side == "CALL":
        if hi < s * 0.97:
            return {
                "result": "not_reached",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 미도달",
            }
        if hi >= s * 0.995 and cl >= s * 0.99:
            return {
                "result": "breakout_success",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 돌파 후 종가 유지",
            }
        if hi >= s * 0.995 and cl < s * 0.99:
            return {
                "result": "breakout_fail",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 터치 후 되돌림(종 {_fmt_px(cl)})",
            }
        # 근접만
        return {
            "result": "approach_reverse",
            "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 접근 후 돌파 못함",
        }

    # PUT
    if lo > s * 1.03:
        return {
            "result": "not_reached",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 미도달",
        }
    if lo <= s * 1.005 and cl >= s * 0.995:
        return {
            "result": "support_success",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 지지 반응 후 종가 유지",
        }
    if lo <= s * 1.005 and cl < s * 0.995:
        # 하방 통과
        if op > s and cl < s * 0.99:
            return {
                "result": "level_pass",
                "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 하방 통과(종 {_fmt_px(cl)})",
            }
        return {
            "result": "support_fail",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 지지 실패(종 {_fmt_px(cl)})",
        }
    return {
        "result": "approach_reverse",
        "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 접근 후 이탈 없음",
    }


# 하위 호환 별칭
def judge_strike_outcome(strike: float, side: str, ohlc: dict) -> dict[str, Any]:
    out = classify_relation(strike, side, ohlc, has_strong_option=True)
    # 구 테스트 호환: breakout_success를 hit로도 인식하는 곳은 테스트 수정
    return out


def match_events_to_zones(
    events: list[dict],
    zones: list[dict],
    ohlc: dict,
    *,
    match_pct: float | None = None,
) -> list[dict[str, Any]]:
    """[3] 반응 구간 ↔ 전일 옵션 이벤트 매칭 후 분류."""
    cfg = _cfg()
    mp = match_pct if match_pct is not None else cfg["match_pct"]
    if not events:
        # 구간만 있으면 mismatch 후보
        return []

    matched: list[dict] = []
    used: set[tuple] = set()
    hi = float(ohlc.get("high") or 0)
    lo = float(ohlc.get("low") or 0)

    for e in events:
        try:
            s = float(e["strike"])
        except (TypeError, ValueError, KeyError):
            continue
        key = (round(s, 2), e.get("side"))
        if key in used:
            continue
        # 구간에 가깝거나 당일 레인지에 걸치면 매칭
        near_zone = False
        zone_hit = None
        for z in zones:
            zp = float(z["price"])
            if zp and abs(s - zp) / zp <= mp:
                near_zone = True
                zone_hit = z
                break
        in_range = lo > 0 and lo * 0.99 <= s <= hi * 1.01
        strong = _is_meaningful(e)
        if not near_zone and not in_range and not strong:
            continue
        used.add(key)
        cls = classify_relation(s, str(e.get("side") or "CALL"), ohlc, has_strong_option=strong)
        if not strong and (near_zone or in_range):
            cls = {
                "result": "weak_option",
                "note": f"{_fmt_px(s)} 반응 구간 근처 · 옵션 근거 약함",
            }
        matched.append(
            {
                **e,
                "result": cls["result"],
                "outcome_note": cls["note"],
                "matched_zone": zone_hit,
                "in_day_range": in_range,
            }
        )

    # 정렬: 의미 있는 결과 우선 (미도달 뒤로)
    prio = {
        "breakout_fail": 0,
        "breakout_success": 0,
        "support_fail": 0,
        "support_success": 0,
        "approach_reverse": 1,
        "level_pass": 1,
        "weak_option": 2,
        "not_reached": 3,
        "mismatch": 2,
    }
    matched.sort(key=lambda x: (prio.get(x.get("result"), 9), x.get("dist_pct") or 99))
    return matched


def find_setup_patterns(
    ticker: str,
    probe: dict,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """[5] 과거 동일 셋업(side + event 계열) 결과 분포."""
    side = str(probe.get("side") or "").upper()
    et = str(probe.get("event_type") or "")
    # volume_spike_near / near_volume → volume family; voi_extreme → voi
    if "voi" in et:
        family = "voi"
    else:
        family = "volume"

    recs = load_event_records(ticker, limit=limit)
    same: list[dict] = []
    for r in recs:
        if str(r.get("side") or "").upper() != side:
            continue
        if not r.get("result") and not r.get("outcome_date"):
            continue
        ret = str(r.get("event_type") or "")
        if family == "voi" and "voi" not in ret:
            continue
        if family == "volume" and "voi" in ret:
            continue
        # 근거리만 (당시 dist)
        d = r.get("dist_pct")
        if d is not None and float(d) > 8:
            continue
        same.append(r)

    counts: dict[str, int] = {}
    rets: list[float] = []
    for r in same:
        res = _norm_result(r.get("result"), r.get("side"))
        counts[res] = counts.get(res, 0) + 1
        # 다음날 수익률: 있으면
        ar = r.get("next_return_pct")
        if ar is None and r.get("actual_close") and r.get("prev_close"):
            try:
                ar = (
                    (float(r["actual_close"]) - float(r["prev_close"]))
                    / float(r["prev_close"])
                    * 100
                )
            except (TypeError, ValueError, ZeroDivisionError):
                ar = None
        if ar is not None:
            rets.append(float(ar))

    n = len(same)
    avg_ret = round(sum(rets) / len(rets), 2) if rets else None
    # 돌파/지지 성공·실패 요약
    if side == "CALL":
        ok = counts.get("breakout_success", 0)
        fail = counts.get("breakout_fail", 0) + counts.get("approach_reverse", 0)
        ok_label, fail_label = "돌파 성공", "돌파 실패·반전"
    else:
        ok = counts.get("support_success", 0)
        fail = counts.get("support_fail", 0) + counts.get("level_pass", 0)
        ok_label, fail_label = "지지 성공", "지지 실패·통과"

    status = "ok" if n >= MIN_PATTERN_SAMPLES else "insufficient"
    return {
        "available": n > 0,
        "n": n,
        "counts": counts,
        "avg_next_return_pct": avg_ret,
        "success_n": ok,
        "fail_n": fail,
        "success_label": ok_label,
        "fail_label": fail_label,
        "status": status,
        "family": family,
        "side": side,
        "probe_result": _norm_result(probe.get("result"), side),
    }


def build_learning_insight(
    *,
    price: dict,
    zones: list[dict],
    matched: list[dict],
    prev_events: list[dict],
) -> dict[str, Any]:
    """[6] 오늘 확인된 학습 한 줄 요약 구조."""
    if not price.get("available"):
        return {"headline": "주가 OHLC 부족 — 학습 보류", "bullets": []}

    bullets: list[str] = []
    ret = price.get("return_pct")
    ret_s = f" ({ret:+.1f}%)" if ret is not None else ""
    bullets.append(
        f"주가 {_fmt_px(price['open'])}→{_fmt_px(price['close'])}{ret_s}, "
        f"고 {_fmt_px(price['high'])} / 저 {_fmt_px(price['low'])}"
    )
    if zones:
        ztxt = ", ".join(f"{_fmt_px(z['price'])}({z['label']})" for z in zones[:3])
        bullets.append(f"실제 가격 반응 구간: {ztxt}")

    primary = matched[0] if matched else None
    if not prev_events and not matched:
        bullets.append("전일 근거리 옵션 이벤트 없음 → 옵션 근거 부족")
        return {
            "headline": "가격 움직임은 있었으나 전일 옵션 근거가 약함",
            "bullets": bullets,
            "primary": None,
            "result": "weak_option",
        }

    if not matched and prev_events:
        # 옵션은 있었는데 반응 구간과 안 겹침
        far = ", ".join(
            f"{_fmt_px(e['strike'])}({'콜' if e.get('side')=='CALL' else '풋'})"
            for e in prev_events[:3]
        )
        bullets.append(f"전일 옵션 관심: {far} — 오늘 반응 구간과 불일치/미도달")
        return {
            "headline": (
                f"옵션 관심({far})과 오늘 가격 반응 구간이 겹치지 않음 "
                f"(옵션-가격 불일치 또는 미도달)"
            ),
            "bullets": bullets,
            "primary": None,
            "result": "mismatch",
        }

    assert primary is not None
    side_ko = "콜" if primary.get("side") == "CALL" else "풋"
    res = primary.get("result")
    res_ko = RESULT_KO.get(res, res)
    bullets.append(
        f"전일 옵션: {_fmt_px(primary['strike'])} {side_ko} "
        f"({primary.get('detail') or primary.get('event_type')}) → {res_ko}"
        f" — {primary.get('outcome_note')}"
    )

    # 학습 문장
    if res == "breakout_fail":
        learned = (
            f"오늘 확인: '{side_ko} 거래가 많았다'가 아니라 "
            f"{_fmt_px(primary['strike'])} 옵션 집중대가 실제 저항 구간과 겹쳤다"
            f"(돌파 실패 → 종 {_fmt_px(price['close'])})."
        )
    elif res == "breakout_success":
        learned = (
            f"오늘 확인: {_fmt_px(primary['strike'])} 콜 집중대 돌파·안착이 "
            f"가격으로 확인됨 (돌파 성공)."
        )
    elif res == "support_success":
        learned = (
            f"오늘 확인: {_fmt_px(primary['strike'])} 풋 관심대가 "
            f"실제 지지 반응과 겹침 (지지 성공)."
        )
    elif res == "support_fail" or res == "level_pass":
        learned = (
            f"오늘 확인: {_fmt_px(primary['strike'])} 풋 관심대가 "
            f"지지로 작용하지 못함 ({res_ko})."
        )
    elif res == "approach_reverse":
        learned = (
            f"오늘 확인: {_fmt_px(primary['strike'])} 접근 후 돌파/이탈 확정 전 반전 "
            f"— 관심 가격 반응은 있었으나 방향 확정은 보류."
        )
    elif res == "not_reached":
        learned = (
            f"오늘 확인: {_fmt_px(primary['strike'])} 미도달 — "
            f"실패가 아니라 아직 그 가격대 반응을 검증하지 못함."
        )
    else:
        learned = f"오늘 확인: {res_ko} · {_fmt_px(primary['strike'])} {side_ko}"

    bullets.append(learned)
    return {
        "headline": learned,
        "bullets": bullets,
        "primary": primary,
        "result": res,
    }


def append_event_records(
    records: list[dict],
    path: Path | None = None,
) -> int:
    if not records:
        return 0
    p = path or _events_path()
    with p.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def load_event_records(
    ticker: str | None = None,
    *,
    limit: int = 500,
    path: Path | None = None,
) -> list[dict]:
    p = path or _events_path()
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ticker and str(rec.get("ticker", "")).upper() != ticker.upper():
                continue
            out.append(rec)
    return out[-limit:]


def resolve_prev_events(
    ticker: str,
    prev_events: list[dict],
    today_ohlc: dict | None,
    *,
    outcome_date: str,
    save: bool = True,
    prev_close: float | None = None,
    zones: list[dict] | None = None,
) -> list[dict]:
    """전일 이벤트에 t+1 분류 부여 후 저장."""
    if not today_ohlc:
        return []
    zones = zones or extract_reaction_zones(
        analyze_price_day(today_ohlc, prev_close=prev_close)
    )
    if prev_events:
        matched = match_events_to_zones(prev_events, zones, today_ohlc)
    else:
        matched = []

    # 매칭 안 된 강한 이벤트도 not_reached로 기록
    matched_keys = {(round(float(m["strike"]), 2), m.get("side")) for m in matched}
    resolved = list(matched)
    for e in prev_events:
        key = (round(float(e["strike"]), 2), e.get("side"))
        if key in matched_keys:
            continue
        cls = classify_relation(
            float(e["strike"]),
            str(e.get("side") or "CALL"),
            today_ohlc,
            has_strong_option=_is_meaningful(e),
        )
        resolved.append({**e, "result": cls["result"], "outcome_note": cls["note"]})

    out_recs: list[dict] = []
    for r in resolved:
        rec = {
            **{k: v for k, v in r.items() if k != "matched_zone"},
            "ticker": ticker.upper(),
            "outcome_date": outcome_date,
            "result": _norm_result(r.get("result"), r.get("side")),
            "outcome_note": r.get("outcome_note"),
            "actual_high": today_ohlc.get("high"),
            "actual_low": today_ohlc.get("low"),
            "actual_close": today_ohlc.get("close"),
            "prev_close": prev_close,
            "next_return_pct": today_ohlc.get("return_pct"),
            "horizon": "t+1",
        }
        if prev_close and today_ohlc.get("close") and rec.get("next_return_pct") is None:
            try:
                rec["next_return_pct"] = round(
                    (float(today_ohlc["close"]) - float(prev_close))
                    / float(prev_close)
                    * 100,
                    2,
                )
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        out_recs.append(rec)

    if save and out_recs:
        existing = {
            (
                str(r.get("ticker")),
                str(r.get("asof")),
                str(r.get("outcome_date")),
                round(float(r.get("strike") or 0), 2),
                str(r.get("event_type")),
            )
            for r in load_event_records(ticker, limit=2000)
            if r.get("outcome_date")
        }
        fresh = [
            r
            for r in out_recs
            if (
                r["ticker"],
                str(r.get("asof")),
                str(r.get("outcome_date")),
                round(float(r["strike"]), 2),
                str(r.get("event_type")),
            )
            not in existing
        ]
        append_event_records(fresh)
    return out_recs


def format_quiet_report(
    ticker: str,
    date: str,
    score_info: dict,
    *,
    spot: float | None = None,
) -> str:
    cfg = _cfg()
    mode = score_info.get("quiet_mode") or cfg["quiet_mode"]
    vm = score_info.get("volume_mult")
    vm_s = f"{float(vm):.1f}배" if vm is not None else "-"
    px = _fmt_px(spot) if spot is not None else "-"
    reasons = score_info.get("reasons") or []
    reason_s = " · ".join(reasons) if reasons else "근거리 이벤트 없음"
    line = (
        f"📊 {ticker} · {date} · 조용함\n"
        f"· 옵션 특별 움직임 없음 (점수 {score_info.get('score', 0)}"
        f"/{score_info.get('event_min_score', 40)}) · 거래 {vm_s}\n"
        f"· {reason_s}\n"
        f"· 종가 참고 {px} · 스냅만 저장 · 장문 리포트 생략"
    )
    if mode == "skip":
        return ""
    return line


def format_pattern_block(pat: dict | None, primary: dict | None) -> str:
    """과거 동일 패턴 — 연결률 대신 이걸 KPI로."""
    L = ["📚 과거 동일 패턴"]
    if not primary:
        L.append("· 오늘 대표 셋업 없음")
        return "\n".join(L)
    side_ko = "콜" if primary.get("side") == "CALL" else "풋"
    res_ko = RESULT_KO.get(primary.get("result"), primary.get("result"))
    L.append(
        f"· 셋업: {_fmt_px(primary['strike'])} {side_ko} 집중 → "
        f"다음날 반응 → {res_ko}"
    )
    if not pat or not pat.get("available"):
        L.append("· 과거 동일 셋업 기록 없음")
        L.append("· 표본 부족 — 참고용")
        return "\n".join(L)
    L.append(f"· 과거 동일 셋업: {pat['n']}회")
    L.append(
        f"· {pat['success_label']}: {pat['success_n']} · "
        f"{pat['fail_label']}: {pat['fail_n']}"
    )
    if pat.get("avg_next_return_pct") is not None:
        L.append(f"· 평균 다음날 수익률: {pat['avg_next_return_pct']:+.1f}%")
    # 세부 카운트
    bits = []
    for code, n in sorted((pat.get("counts") or {}).items(), key=lambda x: -x[1]):
        bits.append(f"{RESULT_KO.get(code, code)} {n}")
    if bits:
        L.append(f"· 상세: {' · '.join(bits[:5])}")
    if pat.get("status") == "insufficient":
        L.append(f"· 표본 부족 — 참고용 (최소 {MIN_PATTERN_SAMPLES}회 권장)")
    return "\n".join(L)


def format_learning_report(
    *,
    ticker: str,
    date: str,
    price: dict,
    insight: dict,
    matched: list[dict],
    today_events: list[dict],
    pattern: dict | None,
) -> str:
    """가격→매칭→학습 본문 (연결률 없음)."""
    L: list[str] = []
    L.append(f"📊 {ticker} 옵션↔주가 학습")
    L.append(date)
    L.append("")
    L.append("💡 오늘 확인된 것")
    for b in insight.get("bullets") or []:
        L.append(f"· {b}" if not b.startswith(" ") else f" {b}")

    # 관계 표 (로그가 아니라 분류)
    if matched:
        L.append("")
        L.append("📋 옵션↔가격 관계")
        for r in matched[:5]:
            side = "콜" if r.get("side") == "CALL" else "풋"
            L.append(
                f"· {_fmt_px(r['strike'])} {side} → "
                f"{RESULT_KO.get(r.get('result'), r.get('result'))}"
            )
            if r.get("outcome_note"):
                L.append(f"  {r['outcome_note']}")

    L.append("")
    L.append(format_pattern_block(pattern, insight.get("primary") or (matched[0] if matched else None)))

    if today_events:
        L.append("")
        L.append("🔭 오늘 새 근거리 이벤트 (내일 검증)")
        for e in [x for x in today_events if _is_meaningful(x)][:3] or today_events[:2]:
            side = "콜" if e.get("side") == "CALL" else "풋"
            L.append(
                f"· {_fmt_px(e['strike'])} {side} · {e.get('event_type')} · "
                f"{e.get('detail') or ''}"
            )

    L.append("")
    L.append("⚠️ 관측·학습 기록이며 투자 조언이 아닙니다. 연결≠인과.")
    return "\n".join(L)


def build_report_mode(
    *,
    ticker: str,
    date: str,
    prev_snap: dict | None,
    today_snap: dict,
    today_ohlc: dict | None,
    dod: dict | None,
    vol_anom: dict | None,
    save: bool = True,
) -> dict[str, Any]:
    """메인: quiet | learning 리포트."""
    prev_events = detect_events_from_snap(prev_snap)
    today_events = detect_events_from_snap(today_snap)
    score_info = day_event_score(
        prev_events=prev_events,
        today_events=today_events,
        dod=dod,
        vol_anom=vol_anom,
    )

    prev_close = None
    if prev_snap and prev_snap.get("spot") is not None:
        prev_close = float(prev_snap["spot"])
    elif today_snap.get("previous_close") is not None:
        prev_close = float(today_snap["previous_close"])

    price = analyze_price_day(today_ohlc, prev_close=prev_close)
    zones = extract_reaction_zones(price)

    # 가격 우선: 유의미한 주가 움직임만 있어도 학습 리포트
    is_learning_day = bool(score_info["is_event_day"]) or bool(
        price.get("significant") and prev_events
    )

    resolved: list[dict] = []
    matched: list[dict] = []
    insight: dict = {}
    pattern: dict | None = None

    spot = price.get("close") if price.get("available") else today_snap.get("spot")

    if is_learning_day:
        to_resolve = [e for e in prev_events if _is_meaningful(e)] or prev_events
        resolved = resolve_prev_events(
            ticker,
            to_resolve,
            today_ohlc,
            outcome_date=date,
            save=save,
            prev_close=prev_close,
            zones=zones,
        )
        matched = match_events_to_zones(to_resolve, zones, today_ohlc or {})
        if not matched and resolved:
            # 분류만 있고 구간 매칭 약하면 resolved 상위 사용
            matched = [
                r
                for r in resolved
                if r.get("result") not in ("not_reached",)
            ][:4] or resolved[:3]
        insight = build_learning_insight(
            price=price,
            zones=zones,
            matched=matched,
            prev_events=to_resolve,
        )
        primary = insight.get("primary") or (matched[0] if matched else None)
        if primary:
            pattern = find_setup_patterns(ticker, primary)
        body = format_learning_report(
            ticker=ticker,
            date=date,
            price=price,
            insight=insight,
            matched=matched or resolved[:4],
            today_events=today_events,
            pattern=pattern,
        )
        mode = "event"
    else:
        body = format_quiet_report(
            ticker, date, score_info, spot=float(spot) if spot is not None else None
        )
        mode = "quiet"
        if score_info.get("quiet_mode") == "skip":
            mode = "skip"

    return {
        "mode": mode,
        "body": body,
        "score_info": score_info,
        "prev_events": prev_events,
        "today_events": today_events,
        "resolved": resolved,
        "matched": matched,
        "insight": insight,
        "pattern": pattern,
        "price": price,
        "correlation": None,  # 연결률 KPI 폐기
    }

