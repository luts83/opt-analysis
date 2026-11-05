"""옵션판 ↔ 주가 상관 인사이트 본문.

원하는 톤:
  오늘 주가 -x% · 전일 옵션 관심 구간과 겹침 · 단정 불가·압력 후보
  + 현가 위·아래 옵션 분포로 시나리오 (목표가·숏배팅 단정 금지)
"""
from __future__ import annotations

from typing import Any

import option_events as oe

_LIMIT = (
    "한계: 매수/매도(숏) 주체·정확한 체결 시각은 이 데이터로 확인 불가. "
    "스냅은 전일 장 마감 옵션 요약 기준."
)


def _fmt(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return "-"


def _spot(snap: dict | None) -> float | None:
    if not snap:
        return None
    for k in ("regular_close", "spot", "previous_close"):
        try:
            f = float(snap.get(k))
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def build_distribution(snap: dict | None, spot: float | None) -> dict[str, Any]:
    """현가 위=콜 관심, 아래=풋 관심 후보 (OI/거래 밀집)."""
    if not snap or spot is None or spot <= 0:
        return {"available": False, "above": [], "below": []}
    m = snap.get("metrics") or {}
    above: list[dict] = []
    below: list[dict] = []
    oi_ok = not (
        snap.get("oi_stale_raw")
        or (m.get("oi_available") is False)
    )

    def _add(bucket: list, strike: float, side: str, how: str, score: float) -> None:
        if any(abs(x["strike"] - strike) / spot < 0.005 for x in bucket):
            return
        bucket.append(
            {
                "strike": strike,
                "side": side,
                "how": how,
                "score": score,
                "dist_pct": round(abs(strike - spot) / spot * 100, 2),
            }
        )

    # 위=상방 관심(콜 우선), 아래=하방·지지 관심(풋 우선)
    for row in (m.get("top_call_volume") or [])[:8]:
        try:
            s = float(row["strike"])
            vol = float(row.get("volume") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        if s > spot * 1.005:
            _add(above, s, "CALL", f"콜 거래 {int(vol):,}", vol)

    for row in (m.get("top_put_volume") or [])[:8]:
        try:
            s = float(row["strike"])
            vol = float(row.get("volume") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        if s < spot * 0.995:
            _add(below, s, "PUT", f"풋 거래 {int(vol):,}", vol)

    if oi_ok:
        for row in (m.get("top_voi") or [])[:6]:
            try:
                s = float(row["strike"])
                side = str(row.get("type") or row.get("opt_type") or "CALL").upper()
                voi = float(row.get("voi") or 0)
            except (TypeError, ValueError, KeyError):
                continue
            label = f"V/OI {voi:.1f}"
            if side == "CALL" and s > spot * 1.005:
                _add(above, s, "CALL", label, voi * 1000)
            elif side == "PUT" and s < spot * 0.995:
                _add(below, s, "PUT", label, voi * 1000)

        lv = m.get("levels") or {}
        for key, side_hint in (
            ("near_resistance", "CALL"),
            ("strong_resistance", "CALL"),
            ("near_support", "PUT"),
            ("strong_support", "PUT"),
        ):
            raw = lv.get(key)
            items = raw if isinstance(raw, list) else ([raw] if raw else [])
            for it in items[:3]:
                try:
                    if isinstance(it, dict):
                        s = float(it.get("strike") or it.get("price"))
                    else:
                        s = float(it)
                except (TypeError, ValueError):
                    continue
                oi = 0
                if isinstance(it, dict):
                    oi = float(it.get("oi") or it.get("openInterest") or 0)
                how = f"OI 밀집 {int(oi):,}" if oi else "OI 밀집"
                if s > spot * 1.002:
                    _add(above, s, "CALL", how, oi or 1)
                elif s < spot * 0.998:
                    _add(below, s, "PUT", how, oi or 1)

    above.sort(key=lambda x: x["strike"])
    below.sort(key=lambda x: -x["strike"])
    return {
        "available": bool(above or below),
        "above": above[:4],
        "below": below[:4],
        "oi_trusted": oi_ok,
        "spot": spot,
    }


def interpret_overlap(
    *,
    price: dict,
    primary: dict | None,
    prev_events: list[dict],
) -> str:
    """단정 금지 · 압력/헤지 후보 톤."""
    ret = price.get("return_pct")
    if primary is None and not prev_events:
        if ret is not None and abs(float(ret)) >= 2.5:
            return (
                "전일 근거리 옵션 관심이 약해, 오늘 변동을 옵션판만으로 설명하기 어렵다. "
                "뉴스·수급·시장 동조를 함께 볼 것."
            )
        return "전일·당일 옵션↔가격 겹침이 약하다. 방향 단정하지 않는다."

    e = primary or prev_events[0]
    side = str(e.get("side") or "").upper()
    strike = e.get("strike")
    res = oe._norm_result(e.get("result"), side)  # noqa: SLF001
    side_ko = "풋(하락쪽 관심)" if side == "PUT" else "콜(상방·헷지 혼재 가능)"
    head = f"전일 {_fmt(strike)} {side_ko}"

    if ret is not None and float(ret) <= -2.0:
        if side == "PUT" or res in (
            "support_fail",
            "level_pass",
            "approach_reverse",
        ):
            return (
                f"단정 불가. {head}과 오늘 하락 경로가 겹쳐, "
                f"하방 압력·헤지/청산 후보로 본다. (숏배팅으로 단정하지 않음)"
            )
        if side == "CALL":
            return (
                f"단정 불가. {head}이었으나 주가는 하락 — 콜≠매수. "
                f"콜 매도·변동성 헤지 혼재 가능."
            )
        return f"단정 불가. {head}과 오늘 하락이 같은 구간에 있다. 인과는 미확정."

    if ret is not None and float(ret) >= 2.0:
        if side == "CALL" or res in ("breakout_success",):
            return (
                f"단정 불가. {head}과 오늘 상승이 겹친다. "
                f"상방 관심·숏커버 후보이나 매수로 단정하지 않음."
            )
        if side == "PUT" and res in ("support_success", "approach_reverse"):
            return (
                f"단정 불가. {head} 구간에서 지지·반등 반응 후보. "
                f"풋 매수(약세)와 풋 매도(지지)를 구분할 수 없음."
            )
        return f"단정 불가. {head}과 오늘 상승 구간이 일부 겹친다."

    # 소폭 움직임
    if res in oe.STRONG_SEND_RESULTS:
        note = e.get("outcome_note") or oe.RESULT_KO.get(res, res)
        return f"단정 불가. {head} → 오늘 반응: {note}. 방향 확정은 보류."
    return f"단정 불가. {head}은 있었으나 오늘 가격과 강한 겹침은 제한적."


def _scenario_block(dist: dict, spot: float | None) -> list[str]:
    L: list[str] = []
    if not dist.get("available") or spot is None:
        L.append("· 분포 데이터 부족 — 시나리오 보류")
        return L
    ab = dist.get("above") or []
    bl = dist.get("below") or []
    up = ab[0] if ab else None
    dn = bl[0] if bl else None
    L.append("· 시나리오 (목표가 아님 · 관측 포인트)")
    if up:
        L.append(
            f"  - 반등·저항 테스트: {_fmt(up['strike'])} "
            f"({up['side']} · {up['how']}) 반응 여부"
        )
    else:
        L.append("  - 반등 시: 상방 옵션 밀집 약함 — 저항 후보 불명확")
    if dn:
        L.append(
            f"  - 추가 하락: {_fmt(dn['strike'])} "
            f"({dn['side']} · {dn['how']}) 이탈·지지 여부"
        )
    else:
        L.append("  - 하락 시: 하방 옵션 밀집 약함 — 지지 후보 불명확")
    L.append("  - 횡보: 현가 ±2% 안이면 방향 판단 보류 · 주간 리포트 참고")
    return L


def format_daily_insight(
    *,
    ticker: str,
    date: str,
    today_snap: dict,
    prev_snap: dict | None,
    oe_result: dict,
    regime: dict | None = None,
    spy_regime: dict | None = None,
) -> str:
    """매일 텔레그램 본문: ①어제→오늘 ②분포·시나리오 ③과거(있을 때)."""
    price = oe_result.get("price") or {}
    matched = oe_result.get("matched") or []
    prev_events = oe_result.get("prev_events") or []
    today_events = oe_result.get("today_events") or []
    pattern = oe_result.get("pattern")
    insight = oe_result.get("insight") or {}

    # 매칭이 약하면 전일 이벤트만이라도 표시용으로 분류
    primary = insight.get("primary")
    strong = [r for r in matched if r.get("result") in oe.STRONG_SEND_RESULTS]
    if not primary and matched:
        primary = matched[0]
    if not primary and prev_events:
        # 가격만이라도 붙여 해석
        top = prev_events[0]
        if price.get("available"):
            cls = oe.classify_relation(
                float(top["strike"]),
                str(top.get("side") or "CALL"),
                {
                    "high": price.get("high"),
                    "low": price.get("low"),
                    "close": price.get("close"),
                    "open": price.get("open"),
                },
                has_strong_option=oe._is_meaningful(top),
            )
            primary = {**top, "result": cls["result"], "outcome_note": cls["note"]}

    spot = price.get("close") or _spot(today_snap)
    dist = build_distribution(today_snap, float(spot) if spot else None)

    ret = price.get("return_pct")
    significant = bool(price.get("significant")) or (
        ret is not None and abs(float(ret)) >= 2.0
    )
    has_story = bool(strong or (prev_events and significant) or primary)

    L: list[str] = []
    # 헤드라인
    if ret is not None and float(ret) <= -1.0:
        L.append(f"📉 {ticker} · 오늘 주가 {float(ret):+.1f}%")
    elif ret is not None and float(ret) >= 1.0:
        L.append(f"📈 {ticker} · 오늘 주가 {float(ret):+.1f}%")
    else:
        L.append(f"📌 {ticker} · 옵션↔주가 · {date}")

    if price.get("available"):
        L.append(
            f"· 종 {_fmt(price.get('close'))} / 고 {_fmt(price.get('high'))} "
            f"/ 저 {_fmt(price.get('low'))}"
        )
    if regime and regime.get("available"):
        sync = ""
        try:
            import regime as regime_mod

            s = regime_mod.spy_sync_flag(regime, spy_regime)
            if s:
                sync = f" · {s}"
        except Exception:  # noqa: BLE001
            pass
        L.append(
            f"· 상태: {regime.get('regime_ko')} "
            f"(20일 {regime.get('slope_pct')}%{sync})"
        )

    L.append("")
    L.append("① 어제 옵션판 → 오늘 주가")
    L.append("· 기준: 전일 스냅(장 마감 옵션 요약) — 체결 시각 분 단위 없음")

    show = strong[:2] or ([primary] if primary else [])
    if show:
        for r in show:
            side = "풋" if str(r.get("side")).upper() == "PUT" else "콜"
            detail = r.get("detail") or r.get("event_type") or ""
            res_ko = oe.RESULT_KO.get(r.get("result"), r.get("result") or "-")
            L.append(
                f"· 관심: {_fmt(r.get('strike'))} {side} "
                f"({detail}) → 오늘: {res_ko}"
            )
            if r.get("outcome_note"):
                L.append(f"  {r['outcome_note']}")
    elif prev_events:
        e = prev_events[0]
        side = "풋" if str(e.get("side")).upper() == "PUT" else "콜"
        L.append(
            f"· 관심: {_fmt(e.get('strike'))} {side} "
            f"({e.get('detail') or e.get('event_type')}) — 오늘 강한 반응 매칭 약함"
        )
    else:
        L.append("· 전일 근거리 옵션 관심 약함")

    L.append(f"· 해석: {interpret_overlap(price=price, primary=primary, prev_events=prev_events)}")
    L.append(f"· {_LIMIT}")

    L.append("")
    L.append("② 지금 주가 기준 옵션 분포")
    if not dist.get("available"):
        L.append("· 분포를 그릴 옵션 관심 부족")
    else:
        if not dist.get("oi_trusted"):
            L.append("· OI 미신뢰 — 거래량 위주")
        ab = dist.get("above") or []
        bl = dist.get("below") or []
        if ab:
            bits = ", ".join(
                f"{_fmt(x['strike'])}({x['how']})" for x in ab[:3]
            )
            L.append(f"· 위(저항 후보): {bits}")
        else:
            L.append("· 위(저항 후보): 뚜렷한 밀집 약함")
        if bl:
            bits = ", ".join(
                f"{_fmt(x['strike'])}({x['how']})" for x in bl[:3]
            )
            L.append(f"· 아래(지지·하방 후보): {bits}")
        else:
            L.append("· 아래(지지·하방 후보): 뚜렷한 밀집 약함")
        L.extend(_scenario_block(dist, float(spot) if spot else None))

    # ③ 과거 — 있을 때만
    if has_story and pattern and pattern.get("available") and pattern.get("status") == "ok":
        L.append("")
        L.append("③ 과거 비슷한 셋업")
        L.append(
            f"· {pattern.get('n')}회 · "
            f"{pattern.get('success_label')} {pattern.get('success_n')} / "
            f"{pattern.get('fail_label')} {pattern.get('fail_n')}"
        )
        if pattern.get("avg_next_return_pct") is not None:
            L.append(
                f"· 평균 다음날 수익 {pattern['avg_next_return_pct']:+.1f}% "
                f"(미래 확률 아님)"
            )
    elif has_story and today_events:
        m_today = [e for e in today_events if oe._is_meaningful(e)][:2]
        if m_today:
            L.append("")
            L.append("③ 오늘 새 관심 (내일 검증)")
            for e in m_today:
                side = "풋" if str(e.get("side")).upper() == "PUT" else "콜"
                L.append(
                    f"· {_fmt(e.get('strike'))} {side} · "
                    f"{e.get('detail') or e.get('event_type')}"
                )

    if not has_story and not significant:
        L.append("")
        L.append("· 오늘 변동·옵션 겹침이 작아 장문 해석 최소화. 분포·시나리오만 참고.")

    L.append("")
    L.append("⚠️ 관측·가설 · 투자 조언 아님 · 연결≠인과 · 숏/롱 단정 금지")
    return "\n".join(L)
