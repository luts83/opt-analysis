"""실험형 데일리 리포트 조립.

목표: 예측이 아니라 어제 옵션 → 오늘 주가 → 실제 반응 → 해석 → 다음 검증.
형식: 짧은 불릿 + 화살표 흐름. 숫자 나열보다 '무슨 일이 있었는지'.
"""
from __future__ import annotations

from typing import Any


def _fmt_px(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _strike_range(strikes: list[float]) -> str:
    if not strikes:
        return ""
    ss = sorted(set(strikes))
    if len(ss) == 1:
        return _fmt_px(ss[0])
    if len(ss) == 2:
        return f"{_fmt_px(ss[0])}~{_fmt_px(ss[1])}"
    return f"{_fmt_px(ss[0])}~{_fmt_px(ss[-1])}"


def _collect_focus_strikes(dod: dict) -> list[float]:
    """어제 옵션 집중 행사가."""
    out: list[float] = []
    for rows in (dod.get("prev_top_calls") or [], dod.get("prev_top_puts") or []):
        for r in rows[:3]:
            try:
                out.append(float(r["strike"]))
            except (TypeError, ValueError, KeyError):
                continue
    prev_lv = dod.get("prev_levels") or {}
    for key in ("near_resistance", "strong_resistance", "near_support", "strong_support"):
        for it in prev_lv.get(key) or []:
            try:
                out.append(float(it["strike"]))
            except (TypeError, ValueError, KeyError):
                continue
    # 중복 제거, 가까운 순(전일 종가 기준)
    prev_spot = dod.get("prev_spot")
    seen: set[float] = set()
    uniq: list[float] = []
    for s in sorted(out, key=lambda x: abs(x - float(prev_spot or x))):
        rs = round(s, 2)
        if rs in seen:
            continue
        seen.add(rs)
        uniq.append(s)
    return uniq[:5]


def _price_reaction(
    strike: float,
    *,
    high: float | None,
    low: float | None,
    close: float | None,
    spot: float | None,
) -> tuple[str, str]:
    """(코드, 한글 라벨)."""
    ref = close if close is not None else spot
    if ref is None:
        return "unknown", "판단 불가"
    dist = abs(strike - ref) / ref * 100
    if dist > 12:
        return "far", "아직 먼 가격 · 단순 관심"
    tol = 0.004
    hi = float(high) if high is not None else ref
    lo = float(low) if low is not None else ref
    cl = float(close) if close is not None else ref

    tested_up = hi >= strike * (1 - tol)
    tested_down = lo <= strike * (1 + tol)
    held_above = cl >= strike * (1 - tol)
    rejected = tested_up and cl < strike * (1 - tol)

    if strike >= ref * 0.98:
        if rejected:
            return "test_reject", "실제 테스트 · 돌파 후 되돌림"
        if held_above and tested_up:
            return "break_hold", "돌파 확인 · 안착"
        if tested_up:
            return "tested", "실제 테스트 · 미안착"
        if dist <= 5:
            return "untested", "아직 테스트 없음"
        return "far", "아직 먼 가격 · 단순 관심"
    # 아래쪽 관심
    if tested_down and cl > strike * (1 + tol):
        return "support_hold", "아래쪽 테스트 · 지지 유지"
    if tested_down and cl < strike * (1 - tol):
        return "support_fail", "아래쪽 이탈"
    if dist <= 5:
        return "untested", "아직 테스트 없음"
    return "far", "아직 먼 가격 · 단순 관심"


def _build_case_analysis(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None,
) -> dict[str, Any]:
    """섹션 공통 분석."""
    dod = day_over_day or {}
    fb = feedback or {}
    act = fb.get("actual") or {}

    spot = data.get("spot")
    prev_spot = dod.get("prev_spot")
    high = act.get("high")
    low = act.get("low")
    close = act.get("close") if act.get("close") is not None else spot
    chg = dod.get("spot_change_pct")

    focus = _collect_focus_strikes(dod) if dod.get("available") else []
    focus_calls: list[float] = []
    seen_fc: set[float] = set()
    for r in (dod.get("prev_top_calls") or [])[:5]:
        try:
            s = float(r["strike"])
            rs = round(s, 2)
            if rs not in seen_fc:
                seen_fc.add(rs)
                focus_calls.append(s)
        except (TypeError, ValueError, KeyError):
            pass

    reactions: list[dict] = []
    # 어제 집중 + 오늘 levels 관심 가격
    level_strikes: list[float] = list(focus)
    for it in (base.get("levels") or {}).get("interest_all") or []:
        try:
            s = float(it["strike"])
            if abs(s - float(spot or s)) / float(spot or s) <= 0.12:
                level_strikes.append(s)
        except (TypeError, ValueError, KeyError):
            pass
    seen: set[float] = set()
    for s in sorted(set(level_strikes), key=lambda x: -x):
        rs = round(s, 2)
        if rs in seen:
            continue
        seen.add(rs)
        code, label = _price_reaction(s, high=high, low=low, close=close, spot=spot)
        reactions.append({"strike": s, "code": code, "label": label})

    # 핵심 테스트 가격 — 고가에 가장 가까운 실제 반응
    tested_codes = ("test_reject", "break_hold", "tested", "support_hold", "support_fail")
    tested = [r for r in reactions if r["code"] in tested_codes]
    primary = None
    if tested and high is not None:
        primary = min(tested, key=lambda r: abs(r["strike"] - float(high)))
    elif tested:
        primary = tested[0]

    # 일치 여부
    match = "unknown"
    if primary:
        if primary["code"] in ("break_hold", "tested", "test_reject"):
            match = "partial"
        elif primary["code"] in ("support_fail",):
            match = "mismatch"
        else:
            match = "weak"
    elif focus and chg is not None:
        if chg > 0.5 and any(s > float(prev_spot or 0) for s in focus_calls):
            match = "partial"
        elif chg < -0.5:
            match = "mismatch" if focus_calls else "weak"

    match_label = {
        "partial": "부분 일치 — 관심 가격에 실제 반응 있음",
        "aligned": "일치 — 신호와 주가 방향이 맞음",
        "mismatch": "불일치 — 옵션 신호만으로는 설명 어려움",
        "weak": "약한 연결 — 추가 관찰 필요",
        "unknown": "판단 보류 — 데이터 부족",
    }[match]

    pattern = "단발성 — 규칙 반영 전 추가 관찰 필요"

    return {
        "available": dod.get("available") or fb.get("available"),
        "focus": focus,
        "focus_calls": focus_calls,
        "focus_range": _strike_range(focus_calls or focus[:3]),
        "prev_spot": prev_spot,
        "high": high,
        "low": low,
        "close": close,
        "chg": chg,
        "primary": primary,
        "reactions": reactions[:6],
        "match": match,
        "match_label": match_label,
        "pattern": pattern,
        "volume_mult": dod.get("volume_mult"),
        "cpr_prev": dod.get("cpr_prev"),
        "cpr_today": dod.get("cpr_today"),
    }


def _analogy_glance_lines(a: dict) -> list[str]:
    """비유 섞은 한눈에 보기 본문 (3~4줄)."""
    lines: list[str] = []
    fr = a.get("focus_range")
    primary = a.get("primary")
    hi, cl = a.get("high"), a.get("close")
    chg = a.get("chg")

    if primary and hi is not None and cl is not None:
        ps = _fmt_px(primary["strike"])
        code = primary["code"]
        if code == "test_reject":
            if fr:
                lines.append(
                    f"· 어제 옵션 시장은 {fr} 쪽, 특히 {ps} '문' 앞에 거래가 몰려 있었습니다."
                )
            else:
                lines.append(f"· 어제 옵션 시장은 {ps} '문' 앞에 거래가 몰려 있었습니다.")
            lines.append(
                f"· 오늘 주가는 실제로 {ps}까지 갔지만, 문을 넘어서지 못하고 "
                f"{_fmt_px(cl)}에 마감했습니다."
            )
            lines.append(
                f"→ {ps} = 중요한 가격 맞음 · '이제 쭉 오른다' = 아직 확정 아님"
            )
            return lines
        if code == "break_hold":
            lines.append(
                f"· 어제 {fr or ps} 쪽에 관심이 있었고, 오늘 {ps} '문'을 넘어 "
                f"{_fmt_px(cl)}에 마감했습니다."
            )
            lines.append("→ 관심 가격 반응은 확인 · 하루만으로 추세 확정은 아님")
            return lines
        if code == "tested":
            lines.append(
                f"· {ps} '문' 앞까지 갔다가 {_fmt_px(cl)}에 마감 — "
                "손은 댔지만 아직 넘지 못한 하루입니다."
            )
            lines.append(f"→ {ps} = 반응 있음 · 돌파 확정 = 아직 아님")
            return lines
        if code == "support_hold":
            lines.append(
                f"· {ps} '바닥' 근처에서 버텼고 {_fmt_px(cl)}에 마감했습니다."
            )
            lines.append("→ 아래쪽 관심 가격은 일부 확인 · 방향 단정은 아직 이르다")
            return lines

    if fr:
        lines.append(f"· 어제 {fr} 쪽에 옵션 관심(사람)이 몰려 있었습니다.")
    if chg is not None and chg < -0.5 and fr:
        lines.append(
            f"· 그런데 오늘은 오히려 내려와 {_fmt_px(cl)}에 마감했습니다."
        )
        lines.append(
            "→ '사람 많은 쪽 = 그 방향으로 간다'만으로는 설명되지 않는 하루입니다."
        )
        return lines
    if hi is not None and cl is not None:
        lines.append(f"· 오늘 장중 {_fmt_px(hi)}까지 갔다가 {_fmt_px(cl)}에 마감했습니다.")
    if len(lines) <= 1:
        lines.append("· 뚜렷한 '문' 테스트는 없었고, 오늘은 기록 축적이 우선입니다.")
    return lines


def _analogy_one_liner(a: dict) -> str:
    """화살표 섹션 맨 아래 한 줄 비유."""
    primary = a.get("primary")
    cl = a.get("close")
    if primary and cl is not None:
        ps = _fmt_px(primary["strike"])
        if primary["code"] == "test_reject":
            return f"쉽게 말하면: {ps} 문까지 갔다가 다시 내려온 하루"
        if primary["code"] == "break_hold":
            return f"쉽게 말하면: {ps} 문을 넘어서 마감한 하루 (확정은 아직)"
        if primary["code"] == "tested":
            return f"쉽게 말하면: {ps} 문 앞에서 맴돌다 {_fmt_px(cl)} 마감"
    if a.get("chg") is not None and a["chg"] < -0.5 and a.get("focus_range"):
        return "쉽게 말하면: 관심은 위쪽이었는데 주가는 내려온 하루"
    return "쉽게 말하면: 오늘은 신호를 기록해 두고 내일 이어서 확인"


def at_a_glance_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
) -> str:
    """💡 오늘 한눈에 보기 — 비유 섞은 3~4줄."""
    L = ["💡 오늘 한눈에 보기"]
    a = _build_case_analysis(data, base, day_over_day, feedback)

    if not a["available"]:
        L.append("· 어제 옵션 기록이 없어 오늘은 주가 결과만 쌓입니다.")
        close = a["close"]
        if close is not None:
            L.append(f"· 오늘 종가 {_fmt_px(close)}.")
        return "\n".join(L)

    for line in _analogy_glance_lines(a):
        L.append(line)
    if len(L) == 1:
        L.append("· 오늘은 뚜렷한 검증 결과보다 기록 축적이 우선입니다.")
    return "\n".join(L)


def yesterday_options_today_price_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
) -> str:
    """🔄 어제 옵션 → 오늘 주가 — 5단계 화살표."""
    L = ["🔄 어제 옵션 → 오늘 주가"]
    a = _build_case_analysis(data, base, day_over_day, feedback)

    if not a["available"]:
        L.append("· 어제 스냅샷 없음 → 내일부터 검증 시작")
        return "\n".join(L)

    # ① 어제 집중
    if a["focus_calls"]:
        strikes = " · ".join(_fmt_px(s) + " CALL" for s in a["focus_calls"][:3])
        L.append(f"① 어제 집중: {strikes}")
    elif a["focus"]:
        L.append(f"① 어제 집중: {_strike_range(a['focus'])} 관심")
    else:
        L.append("① 어제 집중: 뚜렷한 행사가 집중 없음")
    L.append("   ↓")

    # ② 오늘 테스트
    primary = a["primary"]
    hi = a["high"]
    if primary and hi is not None:
        ps = _fmt_px(primary["strike"])
        if primary["code"] in ("test_reject", "tested", "break_hold"):
            L.append(f"② 오늘 테스트: {ps} 돌파 시도 → 고가 {_fmt_px(hi)}")
        elif primary["code"] == "support_hold":
            L.append(f"② 오늘 테스트: {ps} 아래쪽 지지 테스트")
        else:
            L.append(f"② 오늘 테스트: {ps} 근접 — {primary['label']}")
    elif hi is not None:
        L.append(f"② 오늘 테스트: 주요 관심가 미접촉 · 고가 {_fmt_px(hi)}")
    else:
        L.append("② 오늘 테스트: intraday 데이터 없음")
    L.append("   ↓")

    # ③ 이후 움직임
    cl = a["close"]
    if primary and primary["code"] == "test_reject" and cl is not None:
        L.append(f"③ 이후: {_fmt_px(primary['strike'])} 위 안착 실패 → 종가 {_fmt_px(cl)}")
    elif primary and primary["code"] == "break_hold" and cl is not None:
        L.append(f"③ 이후: {_fmt_px(primary['strike'])} 위 유지 → 종가 {_fmt_px(cl)}")
    elif cl is not None and a["prev_spot"] is not None:
        L.append(f"③ 이후: {_fmt_px(a['prev_spot'])} → {_fmt_px(cl)}")
    else:
        L.append("③ 이후: 종가 데이터 확인 중")
    L.append("   ↓")

    # ④ 일치 여부
    L.append(f"④ 일치: {a['match_label']}")
    L.append("   ↓")

    # ⑤ 패턴
    L.append(f"⑤ 패턴: {a['pattern']}")
    L.append("")
    L.append(f"💬 {_analogy_one_liner(a)}")
    return "\n".join(L)


def reacted_prices_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
) -> str:
    """🎯 실제 반응한 가격 — 관심 vs 반응 구분."""
    L = ["🎯 실제 반응한 가격"]
    a = _build_case_analysis(data, base, day_over_day, feedback)
    reactions = a["reactions"]

    if not reactions:
        L.append("· (데이터 부족)")
        return "\n".join(L)

    # 반응 있는 것 먼저, 미테스트는 아래
    order = {
        "test_reject": 0,
        "break_hold": 1,
        "tested": 2,
        "support_hold": 3,
        "support_fail": 4,
        "untested": 5,
        "far": 6,
        "unknown": 7,
    }
    sorted_r = sorted(reactions, key=lambda r: (order.get(r["code"], 9), -r["strike"]))
    for r in sorted_r[:6]:
        L.append(f"· {_fmt_px(r['strike'])} → {r['label']}")
    return "\n".join(L)


def option_market_change_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    anomalies: list | None = None,
    volume_anomaly: dict | None = None,
) -> str:
    """📊 오늘 옵션 시장에서 무엇이 변했나? — 변화 중심."""
    L = ["📊 오늘 옵션 시장에서 무엇이 변했나?"]
    dod = day_over_day or {}
    if not dod.get("available"):
        L.append("· 어제 대비 비교 데이터 없음")
        return "\n".join(L)

    vm = dod.get("volume_mult")
    if vm is not None:
        if vm >= 1.3:
            L.append(f"· 거래량 {vm}배 증가")
        elif vm <= 0.7:
            L.append(f"· 거래량 {vm}배 감소")
        else:
            L.append("· 거래량 큰 변화 없음")

    cpr_p, cpr_t = dod.get("cpr_prev"), dod.get("cpr_today")
    if cpr_p is not None and cpr_t is not None:
        if cpr_t > cpr_p * 1.1:
            L.append("· 콜 거래 비중 확대 (방향 신호 아님)")
        elif cpr_t < cpr_p * 0.9:
            L.append("· 풋 거래 비중 확대 (방향 신호 아님)")
        else:
            L.append("· 콜/풋 구성비 비슷")

    def _top_range(rows: list) -> str:
        ss = []
        for r in (rows or [])[:3]:
            try:
                ss.append(float(r["strike"]))
            except (TypeError, ValueError, KeyError):
                continue
        return _strike_range(ss)

    prev_r = _top_range(dod.get("prev_top_calls"))
    today_r = _top_range(dod.get("today_top_calls"))
    if prev_r and today_r and prev_r != today_r:
        L.append(f"· 콜 관심 행사가: {prev_r} → {today_r}")
    elif today_r:
        L.append(f"· 콜 관심 행사가: {today_r}")

    oi_p, oi_t = dod.get("oi_prev"), dod.get("oi_today")
    if oi_p and oi_t and oi_p > 0:
        pct = (oi_t - oi_p) / oi_p * 100
        if abs(pct) >= 5:
            L.append(f"· OI {'증가' if pct > 0 else '감소'} ({pct:+.0f}%)")

    senti_p, senti_t = dod.get("sentiment_prev"), dod.get("sentiment_today")
    if senti_t and senti_p and senti_t != senti_p:
        L.append(f"· 옵션 온도: {senti_p} → {senti_t}")

    for an in (anomalies or [])[:2]:
        msg = an.get("message") if isinstance(an, dict) else str(an)
        if msg:
            L.append(f"· OI 급변: {msg[:60]}")

    va = volume_anomaly or {}
    if va.get("is_anomaly") and va.get("message"):
        L.append(f"· {va['message'][:60]}")

    if len(L) == 1:
        L.append("· 눈에 띄는 변화 없음")
    return "\n".join(L)


def case_interpretation_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
) -> str:
    """🧠 이번 사례의 해석 — 🟢🟡⚪."""
    L = ["🧠 이번 사례의 해석"]
    a = _build_case_analysis(data, base, day_over_day, feedback)
    dod = day_over_day or {}

    facts: list[str] = []
    interp: list[str] = []
    unknown: list[str] = []

    if a["focus_range"]:
        facts.append(f"어제 {a['focus_range']} 콜 거래 집중")
    if a["high"] is not None and a["close"] is not None:
        facts.append(f"오늘 고가 {_fmt_px(a['high'])} · 종가 {_fmt_px(a['close'])}")
    if a["volume_mult"] and a["volume_mult"] >= 1.3:
        facts.append(f"옵션 거래량 {a['volume_mult']}배 증가")

    primary = a["primary"]
    if primary:
        if primary["code"] == "test_reject":
            interp.append(f"{_fmt_px(primary['strike'])}에서 매물·되돌림 가능성")
        elif primary["code"] == "break_hold":
            interp.append(f"{_fmt_px(primary['strike'])} 돌파 후 관심 유지 가능")
        elif primary["code"] == "tested":
            interp.append(f"{_fmt_px(primary['strike'])} 테스트 반응 — 지속 여부 미확인")

    if a["match"] == "mismatch":
        interp.append("어제 옵션 신호와 오늘 주가 방향이 어긋남")
    elif a["match"] == "partial":
        interp.append("관심 가격에 실제 반응 — 방향 확정은 아직 이르다")

    unknown.append("콜/풋 매수·매도 중 무엇이 원인인지는 확인 불가")
    unknown.append("OI만으로 방향 단정 불가")

    chg = a["chg"]
    prev_calls = dod.get("prev_top_calls") or []
    if prev_calls and chg is not None and chg < -0.5:
        facts.append("어제 콜 집중 + 오늘 하락 마감")
        interp.append("콜 몰림 = 상승 베팅만은 아님 (매도·헤지 가능)")

    for line in facts[:3]:
        L.append(f"🟢 {line}")
    for line in interp[:3]:
        L.append(f"🟡 {line}")
    for line in unknown[:2]:
        L.append(f"⚪ {line}")
    return "\n".join(L)


def next_verify_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
    eventinfo: dict | None = None,
) -> str:
    """🔍 다음 검증 포인트."""
    import report_evidence as ev

    L = ["🔍 다음 검증 포인트"]
    a = _build_case_analysis(data, base, day_over_day, feedback)
    spot = data.get("spot")
    levels = base.get("levels") or {}
    primary = a["primary"]

    if primary and primary["code"] in ("test_reject", "tested"):
        ps = _fmt_px(primary["strike"])
        L.append(f"· {ps} 재돌파 후 안착하는가?")
        L.append(f"· {ps}에서 다시 되돌림이 나오는가?")
    else:
        res = ev._nearest(levels, float(spot), "res") if spot is not None else None
        if res:
            L.append(f"· {_fmt_px(res)} 돌파 후 거래량과 함께 유지되는가?")

    sup = ev._nearest(levels, float(spot), "sup") if spot is not None else None
    if sup:
        L.append(f"· {_fmt_px(sup)} 이탈 시 옵션 관심이 아래로 옮겨가는가?")

    exp = levels.get("expansion_up")
    if exp and exp.get("zone"):
        z0, z1 = exp["zone"]
        if abs(float(z0) - float(z1)) < 0.01:
            L.append(f"· {_fmt_px(z0)} 구간 실제 반응 여부 (목표가 아님)")
        else:
            L.append(f"· ${_fmt_px(z0)}~${_fmt_px(z1)} 구간 실제 반응 여부 (목표가 아님)")

    L.append("· 먼 콜 OI가 커도 다음날 반대로 가면 '설명 실패'로 기록")
    if len(L) == 1:
        L.append("· 관심 가격이 생기면 그때 검증 항목 추가")
    return "\n".join(L)


def cumulative_learning_block(
    ticker: str,
    learning_context: dict | None = None,
) -> str:
    """📚 누적 학습 — 간소화."""
    import learning as learn
    import pattern_store as ps

    L = ["📚 누적 학습"]
    ctx = learning_context or {}
    stats = ctx.get("최근30일") or learn.cumulative_stats(ticker, limit=30)
    if not stats.get("available"):
        stats = ctx.get("최근7일") or learn.cumulative_stats(ticker, limit=7)

    if stats.get("available"):
        n = stats.get("n") or 0
        L.append(f"최근 {n}회")
        band = stats.get("band_accuracy_pct")
        sup = stats.get("support_accuracy_pct")
        direc = stats.get("direction_accuracy_pct")
        parts = []
        if band is not None:
            parts.append(f"밴드 {band}%")
        if sup is not None:
            parts.append(f"주요 가격 반응 {sup}%")
        if direc is not None:
            parts.append(f"방향 {direc}%")
        if parts:
            L.append(" · ".join(parts))
    else:
        L.append("· 아직 누적 기록 부족")

    st = ps.pattern_state(ps.PATTERN_BREAKOUT_EXPAND)
    n_obs = st.get("n", 0)
    if n_obs <= 0:
        L.append("· 학습 중인 패턴 없음")
    elif st["status"] == "active":
        L.append("· 학습 완료: 관심가+거래량 돌파 시 확장 가능성 — 소폭 반영 중")
    else:
        L.append(
            f"· 학습 중: 관심 가격을 거래량과 함께 돌파하면 추가 상승하는지 관찰 ({n_obs}회)"
        )
        L.append("  → 아직 사례 부족 · 예측 규칙 미반영")
    return "\n".join(L)


def limits_block(base: dict | None = None) -> str:
    """⚠️ 데이터 한계."""
    L = [
        "⚠️ 데이터 한계",
        "· OI/거래량만으로 매수·매도 방향 확정 불가",
        "· 표본 부족 패턴은 학습 후보로만 기록",
        "· 단일 날짜 결과로 예측 규칙 변경하지 않음",
    ]
    if base and (base.get("low_confidence") or (base.get("levels") or {}).get("low_confidence")):
        L.append("· 오늘 OI 저신뢰 — 위 한계 더 큼")
    return "\n".join(L)


def assemble_experiment_report(
    data: dict,
    base: dict,
    *,
    anomalies: list | None = None,
    volume_anomaly: dict | None = None,
    day_over_day: dict | None = None,
    eventinfo: dict | None = None,
    feedback: dict | None = None,
    learning_context: dict | None = None,
) -> str:
    """실험형 본문 전체."""
    import market_clock

    ticker = data.get("ticker", "")
    date = data.get("date", "")
    fb = feedback
    L: list[str] = []

    L.append(f"📊 {ticker} 옵션 데일리")
    L.append(f"{date}")
    L.append("")

    banner = __import__("report_evidence").low_confidence_banner(base)
    if banner:
        L.append(banner)
        L.append("")

    earn = (eventinfo or {}).get("earnings") or {}
    if earn.get("phase") in ("임박", "직후") and earn.get("message"):
        L.append(f"🚨 {earn['message']}")
        L.append("")

    L.append(market_clock.format_price_line(data))
    L.append("")
    L.append(at_a_glance_block(data, base, day_over_day, fb))
    L.append("")
    L.append(yesterday_options_today_price_block(data, base, day_over_day, fb))
    L.append("")
    L.append(reacted_prices_block(data, base, day_over_day, fb))
    L.append("")
    L.append(
        option_market_change_block(
            data, base, day_over_day, anomalies, volume_anomaly
        )
    )
    L.append("")
    L.append(case_interpretation_block(data, base, day_over_day, fb))
    L.append("")
    L.append(next_verify_block(data, base, day_over_day, fb, eventinfo))
    L.append("")
    L.append(cumulative_learning_block(ticker, learning_context))
    L.append("")
    L.append(limits_block(base))
    L.append("")
    L.append("⚠️ 관측·학습 기록이며 투자 조언이 아닙니다.")
    return "\n".join(L)
