"""옵션 이벤트 → 주가 반응(상관 실험) 단위.

목표: 매일 Top strike 나열이 아니라
  근거리(±focus%) 특별 움직임만 이벤트로 잡고
  t+1 OHLC 반응으로 연결/미연결을 누적한다.

인과 단정 금지. 거래량≠매수≠방향.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _cfg() -> dict:
    import config

    return {
        "focus_pct": float(getattr(config, "REPORT_FOCUS_PCT", 0.05)),
        "focus_pct_2": float(getattr(config, "REPORT_FOCUS_PCT_2", 0.08)),
        "event_min_score": int(getattr(config, "REPORT_EVENT_MIN_SCORE", 40)),
        "quiet_mode": str(getattr(config, "REPORT_QUIET_MODE", "one_line")),
        "volume_mult_hi": 1.5,
        "volume_mult_lo": 0.6,
        # 평이한 근거리 거래는 이벤트가 아님 — 강한 집중만
        "min_near_volume": 2000,
        "min_voi_extreme": 5.0,
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


def judge_strike_outcome(
    strike: float,
    side: str,
    ohlc: dict,
) -> dict[str, Any]:
    """단일 strike vs OHLC → hit|partial|reject|unverified."""
    hi = ohlc.get("high")
    lo = ohlc.get("low")
    cl = ohlc.get("close")
    if hi is None or lo is None or cl is None:
        return {"result": "unverified", "note": "OHLC 없음"}
    hi, lo, cl = float(hi), float(lo), float(cl)
    s = float(strike)
    side = side.upper()
    if side == "CALL":
        if hi < s * 0.97:
            return {
                "result": "unverified",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 미도달",
            }
        if hi >= s * 0.995 and cl >= s * 0.99:
            return {
                "result": "hit",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 도달·종가 유지",
            }
        if hi >= s * 0.995 and cl < s * 0.99:
            return {
                "result": "reject",
                "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 터치 후 되돌림(종 {_fmt_px(cl)})",
            }
        return {
            "result": "partial",
            "note": f"고 {_fmt_px(hi)} · {_fmt_px(s)} 근접",
        }
    # PUT
    if lo > s * 1.03:
        return {
            "result": "unverified",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 미도달",
        }
    if lo <= s * 1.005 and cl >= s * 0.995:
        return {
            "result": "hit",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 근처 반응",
        }
    if lo <= s * 1.005 and cl < s * 0.995:
        return {
            "result": "partial",
            "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 하방 통과",
        }
    return {
        "result": "partial",
        "note": f"저 {_fmt_px(lo)} · {_fmt_px(s)} 근접",
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
) -> list[dict]:
    """전일 이벤트에 t+1 결과 부여 후 저장."""
    if not prev_events or not today_ohlc:
        return []
    resolved: list[dict] = []
    for e in prev_events:
        out = judge_strike_outcome(e["strike"], e["side"], today_ohlc)
        rec = {
            **e,
            "ticker": ticker.upper(),
            "outcome_date": outcome_date,
            "result": out["result"],
            "outcome_note": out["note"],
            "actual_high": today_ohlc.get("high"),
            "actual_low": today_ohlc.get("low"),
            "actual_close": today_ohlc.get("close"),
            "horizon": "t+1",
        }
        resolved.append(rec)
    if save and resolved:
        # 중복 방지: 같은 ticker+asof+outcome_date+strike 있으면 스킵
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
            for r in resolved
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
    return resolved


def correlation_stats(
    ticker: str,
    *,
    limit: int = 80,
) -> dict[str, Any]:
    """근거리 이벤트 연결률. unverified는 평가 분모에서 제외."""
    recs = [
        r
        for r in load_event_records(ticker, limit=limit * 3)
        if r.get("result") and r.get("horizon") == "t+1"
    ][-limit:]
    if not recs:
        return {"available": False, "n": 0, "ticker": ticker}

    evaluated = [r for r in recs if r.get("result") != "unverified"]
    linked = [r for r in evaluated if r.get("result") in ("hit", "partial", "reject")]
    # reject도 '가격이 그 레벨에 반응'이므로 연결로 봄. hit|partial|reject
    connected = [r for r in evaluated if r.get("result") in ("hit", "partial", "reject")]
    hits = [r for r in evaluated if r.get("result") == "hit"]
    n_eval = len(evaluated)
    n_unv = sum(1 for r in recs if r.get("result") == "unverified")
    rate = (len(connected) / n_eval) if n_eval else None
    hit_rate = (len(hits) / n_eval) if n_eval else None

    by_type: dict[str, dict] = {}
    for r in recs:
        et = r.get("event_type") or "other"
        st = by_type.setdefault(et, {"n": 0, "eval": 0, "connected": 0})
        st["n"] += 1
        if r.get("result") != "unverified":
            st["eval"] += 1
            if r.get("result") in ("hit", "partial", "reject"):
                st["connected"] += 1

    return {
        "available": True,
        "ticker": ticker.upper(),
        "n": len(recs),
        "n_evaluated": n_eval,
        "n_unverified": n_unv,
        "n_connected": len(connected),
        "n_hit": len(hits),
        "connect_rate": round(rate, 3) if rate is not None else None,
        "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
        "by_type": by_type,
        "min_samples": 20,
        "status": (
            "candidate"
            if n_eval < 20
            else ("active" if (rate or 0) >= 0.55 else "weak")
        ),
    }


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
        return ""  # 호출측에서 발송 스킵
    return line


def format_correlation_block(stats: dict | None) -> str:
    if not stats or not stats.get("available"):
        return "📉 누적 상관\n· 아직 근거리 이벤트 결과 없음"
    L = ["📉 누적 상관 (옵션 이벤트 → t+1 주가 반응)"]
    n_eval = stats.get("n_evaluated") or 0
    rate = stats.get("connect_rate")
    rate_s = f"{rate * 100:.0f}%" if rate is not None else "-"
    L.append(
        f"· 평가 {n_eval}건 · 연결(hit/부분/되돌림) {stats.get('n_connected', 0)} "
        f"· 연결률 {rate_s} · 미검증 {stats.get('n_unverified', 0)}"
    )
    L.append(
        f"· 상태: "
        + {
            "candidate": "표본 부족(후보)",
            "active": "반복 관찰 중",
            "weak": "연결 약함",
        }.get(stats.get("status"), stats.get("status"))
    )
    L.append("· ※ 연결≠인과 · 거래량≠매수·방향")
    return "\n".join(L)


def format_event_story(
    *,
    ticker: str,
    date: str,
    prev_events: list[dict],
    resolved: list[dict],
    today_events: list[dict],
    score_info: dict,
    ohlc: dict | None,
    corr: dict | None,
) -> str:
    """이벤트 데이 본문."""
    L: list[str] = []
    L.append(f"📊 {ticker} 옵션↔주가")
    L.append(date)
    L.append("")
    L.append("💡 한눈에")
    L.append("📌 어제 옵션 이벤트 → 오늘 주가")

    if not prev_events:
        L.append("· 전일 근거리 이벤트 없음 — 오늘은 새 이벤트만 기록")
    else:
        for e in prev_events[:4]:
            side = "콜" if e.get("side") == "CALL" else "풋"
            L.append(
                f"· [{e.get('event_type')}] {_fmt_px(e['strike'])} {side} "
                f"(거리 {e.get('dist_pct')}%) — {e.get('detail') or ''}"
            )

    if ohlc and ohlc.get("close") is not None:
        chg = ohlc.get("return_pct")
        chg_s = f" ({chg:+.1f}%)" if chg is not None else ""
        L.append(
            f"· 오늘 주가: 고 {_fmt_px(ohlc.get('high'))} / "
            f"저 {_fmt_px(ohlc.get('low'))} / 종 {_fmt_px(ohlc.get('close'))}{chg_s}"
        )

    if resolved:
        L.append("")
        L.append("📋 연결 검증 (t+1)")
        L.append("이벤트 | 결과 | 메모")
        ko = {
            "hit": "✅ 연결(적중)",
            "partial": "⚠️ 연결(부분)",
            "reject": "↩️ 연결(되돌림)",
            "unverified": "⬜ 미연결(미검증)",
        }
        for r in resolved:
            side = "콜" if r.get("side") == "CALL" else "풋"
            L.append(
                f"· {_fmt_px(r['strike'])} {side} | "
                f"{ko.get(r.get('result'), r.get('result'))} | {r.get('outcome_note')}"
            )
        linked = sum(
            1 for r in resolved if r.get("result") in ("hit", "partial", "reject")
        )
        unv = sum(1 for r in resolved if r.get("result") == "unverified")
        L.append(
            f"· 요약: 연결 {linked}/{len(resolved)}"
            + (f" · 미검증 {unv}" if unv else "")
            + " (연결≠옵션이 주가를 밀었다는 뜻 아님)"
        )

    if today_events:
        L.append("")
        L.append("🔭 오늘 새 근거리 이벤트 (내일 검증)")
        for e in today_events[:3]:
            side = "콜" if e.get("side") == "CALL" else "풋"
            L.append(
                f"· {_fmt_px(e['strike'])} {side} · {e.get('event_type')} · "
                f"{e.get('detail') or ''}"
            )
    else:
        L.append("")
        L.append("🔭 오늘 새 근거리 이벤트 없음")

    L.append("")
    L.append(format_correlation_block(corr))
    L.append("")
    L.append("⚠️ 관측·상관 실험이며 투자 조언이 아닙니다.")
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
    """메인 엔트리: quiet | event 리포트 문자열과 메타."""
    prev_events = detect_events_from_snap(prev_snap)
    today_events = detect_events_from_snap(today_snap)
    score_info = day_event_score(
        prev_events=prev_events,
        today_events=today_events,
        dod=dod,
        vol_anom=vol_anom,
    )
    resolved: list[dict] = []
    if score_info["is_event_day"]:
        # 검증은 의미 있는 전일 이벤트 우선, 없으면 전일 후보 전체
        to_resolve = [e for e in prev_events if _is_meaningful(e)] or prev_events
        resolved = resolve_prev_events(
            ticker,
            to_resolve,
            today_ohlc,
            outcome_date=date,
            save=save,
        )

    corr = correlation_stats(ticker)
    spot = None
    if today_ohlc and today_ohlc.get("close") is not None:
        spot = today_ohlc.get("close")
    elif today_snap.get("spot") is not None:
        spot = today_snap.get("spot")

    if score_info["is_event_day"]:
        body = format_event_story(
            ticker=ticker,
            date=date,
            prev_events=prev_events,
            resolved=resolved,
            today_events=today_events,
            score_info=score_info,
            ohlc=today_ohlc,
            corr=corr,
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
        "correlation": corr,
    }
