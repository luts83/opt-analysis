"""실험형 데일리 리포트 조립.

목표: 매일 그럴듯한 예측이 아니라
  어제 옵션 → 오늘 옵션 변화 → 오늘 주가 반응 → 패턴 기록 → 과거 비교.
"""
from __future__ import annotations

from typing import Any


def _fmt_px(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_n(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        try:
            return f"{float(v):g}"
        except (TypeError, ValueError):
            return str(v)


def option_change_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    anomalies: list | None = None,
    volume_anomaly: dict | None = None,
) -> str:
    """🔄 어제 → 오늘 옵션 변화."""
    L = ["🔄 어제 → 오늘 옵션 변화"]
    dod = day_over_day or {}
    if not dod.get("available"):
        L.append("- 어제 스냅샷이 없어 변화를 비교할 수 없어요. 내일부터 쌓입니다.")
        return "\n".join(L)

    pdate = dod.get("prev_date") or "어제"
    L.append(f"(비교: {pdate} → {data.get('date')})")

    oi_t, oi_p = dod.get("oi_today"), dod.get("oi_prev")
    if oi_t is not None or oi_p is not None:
        if (oi_t or 0) <= 0 and (oi_p or 0) <= 0:
            L.append("- OI: 데이터 없음 (저신뢰)")
        else:
            bits = f"OI {_fmt_n(oi_p)} → {_fmt_n(oi_t)}"
            if oi_p and oi_t is not None and oi_p > 0:
                bits += f" ({(oi_t - oi_p) / oi_p * 100:+.0f}%)"
            L.append(f"- {bits}")

    vol_p, vol_t = dod.get("volume_prev"), dod.get("volume_today")
    vm = dod.get("volume_mult")
    if vol_t is not None:
        line = f"거래량 {_fmt_n(vol_p)} → {_fmt_n(vol_t)}"
        if vm is not None:
            line += f" ({vm}배)"
        L.append(f"- {line}")

    cpr_p, cpr_t = dod.get("cpr_prev"), dod.get("cpr_today")
    if cpr_t is not None:
        if cpr_p is not None:
            L.append(f"- C/P(구성비) {cpr_p} → {cpr_t} (방향 신호 아님)")
        else:
            L.append(f"- C/P(구성비) {cpr_t} (방향 신호 아님)")

    senti_p, senti_t = dod.get("sentiment_prev"), dod.get("sentiment_today")
    if senti_t and senti_p and senti_t != senti_p:
        L.append(f"- 옵션 온도 라벨: {senti_p} → {senti_t}")

    # 주요 행사가 관심 변화
    def _strikes(rows: list) -> list[str]:
        out = []
        for r in (rows or [])[:3]:
            try:
                out.append(f"${float(r['strike']):g}(vol {_fmt_n(r.get('volume'))})")
            except (TypeError, ValueError, KeyError):
                continue
        return out

    pc, tc = _strikes(dod.get("prev_top_calls")), _strikes(dod.get("today_top_calls"))
    pp, tp = _strikes(dod.get("prev_top_puts")), _strikes(dod.get("today_top_puts"))
    if pc or tc:
        L.append(f"- 콜 관심 행사가: {', '.join(pc) or '-'} → {', '.join(tc) or '-'}")
    if pp or tp:
        L.append(f"- 풋 관심 행사가: {', '.join(pp) or '-'} → {', '.join(tp) or '-'}")

    for a in (anomalies or [])[:4]:
        msg = a.get("message") if isinstance(a, dict) else str(a)
        if msg:
            L.append(f"- OI 급변: {msg}")

    va = volume_anomaly or {}
    if va.get("is_anomaly") and va.get("message"):
        L.append(f"- 거래량 특이: {va['message']}")

    if len(L) <= 2:
        L.append("- 눈에 띄는 옵션 변화는 크지 않았어요.")
    return "\n".join(L)


def option_vs_price_block(
    data: dict,
    base: dict,
    day_over_day: dict | None,
    feedback: dict | None = None,
) -> str:
    """📈 옵션 변화와 주가 움직임 — 어제 신호 → 오늘 반응."""
    L = ["📈 옵션 변화와 주가 움직임"]
    dod = day_over_day or {}
    spot = data.get("spot")
    prev_spot = dod.get("prev_spot")
    chg = dod.get("spot_change_pct")
    fb = feedback or {}
    act = fb.get("actual") or {}

    if not dod.get("available") and not fb.get("available"):
        L.append("- 어제 데이터가 없어 '신호→반응'을 아직 적을 수 없어요.")
        return "\n".join(L)

    # 어제 관심 가격
    prev_lv = dod.get("prev_levels") or {}
    interest: list[tuple[float, str]] = []
    for key, label in (
        ("near_resistance", "위 관심"),
        ("strong_resistance", "위 관심"),
        ("near_support", "아래 관심"),
        ("strong_support", "아래 관심"),
    ):
        for it in prev_lv.get(key) or []:
            try:
                s = float(it["strike"])
            except (TypeError, ValueError, KeyError):
                continue
            interest.append((s, label))
    # 중복 제거, 가까운 순
    seen = set()
    uniq = []
    for s, lab in sorted(interest, key=lambda x: abs(x[0] - float(prev_spot or spot or 0))):
        if round(s, 2) in seen:
            continue
        seen.add(round(s, 2))
        uniq.append((s, lab))
    top_calls = dod.get("prev_top_calls") or []

    L.append("어제 옵션에서 보였던 것:")
    if uniq:
        for s, lab in uniq[:3]:
            L.append(f"- {_fmt_px(s)} ({lab} 가격)")
    elif top_calls:
        for r in top_calls[:2]:
            L.append(
                f"- 콜 거래 집중 {_fmt_px(r.get('strike'))} "
                f"(vol {_fmt_n(r.get('volume'))})"
            )
    else:
        L.append("- 뚜렷한 관심 가격 기록이 약했어요.")

    L.append("오늘 주가 반응:")
    high = act.get("high")
    low = act.get("low")
    close = act.get("close") if act.get("close") is not None else spot
    if prev_spot is not None and close is not None:
        c = chg if chg is not None else None
        line = f"- {_fmt_px(prev_spot)} → {_fmt_px(close)}"
        if c is not None:
            line += f" ({c:+.2f}%)"
        L.append(line)
    if high is not None or low is not None:
        L.append(f"- 당일 고가 {_fmt_px(high)} / 저가 {_fmt_px(low)}")

    # 설명력 평가 (단정 금지)
    L.append("해석 (단정 아님):")
    explained = False
    if uniq and high is not None and close is not None:
        above = [s for s, lab in uniq if "위" in lab]
        if above:
            nearest = min(above, key=lambda s: abs(s - float(prev_spot or close)))
            if float(close) < float(nearest) * 0.99 and float(high) < float(nearest):
                L.append(
                    f"- {_fmt_px(nearest)} 위쪽 관심(예: 콜 OI/거래)이 있어도 "
                    f"종가는 그 아래로 마감 → 이 조건만으로 방향성을 잘 설명하지 못함."
                )
                explained = True
            elif float(high) >= float(nearest) * 0.998:
                L.append(
                    f"- {_fmt_px(nearest)} 근처까지 고가가 닿음 → "
                    "관심 가격 반응은 있었으나, 그 자체가 '간다/안 간다' 예측은 아님."
                )
                explained = True
    if dod.get("volume_mult") and dod["volume_mult"] >= 1.5 and chg is not None and abs(chg) < 1:
        L.append("- 옵션 거래량은 늘었지만 주가 변동은 작음 → 거래량≠방향.")
        explained = True
    if not explained:
        L.append(
            "- 오늘은 '어제 옵션 신호 → 오늘 주가' 연결이 약하거나 표본이 짧아요. "
            "기록만 남기고 규칙을 바꾸지 않습니다."
        )
    return "\n".join(L)


def interest_prices_block(data: dict, base: dict) -> str:
    """🎯 핵심 가격 — 관심 가격만."""
    import report_evidence as ev

    text = ev.price_map_block(data, base)
    if not text:
        return "🎯 핵심 가격 (관심 가격)\n- (데이터 부족)"
    soft = {
        "저항 후보": "위쪽 관심",
        "강한 저항": "위쪽 관심(강)",
        "지지 후보": "아래쪽 관심",
        "강한 지지": "아래쪽 관심(강)",
        "돌파 확인 / 지지 후보": "돌파 후 관심(관측)",
        "돌파 실패 가능성": "돌파 실패 관측 중",
    }
    for old, new in soft.items():
        text = text.replace(old, new)
    lines = text.split("\n")
    lines[0] = "🎯 핵심 가격 (지지·저항 단정 아님 · 관심 가격)"
    return "\n".join(lines)


def case_lesson_block(
    feedback: dict | None,
    day_over_day: dict | None,
    learning_context: dict | None = None,
) -> str:
    """🧠 이번 사례에서 얻은 교훈."""
    L = ["🧠 이번 사례에서 얻은 교훈"]
    fb = feedback or {}
    dod = day_over_day or {}
    lesson = fb.get("lesson") or ""
    missed = fb.get("missed_signals") or []

    bullets: list[str] = []
    if lesson:
        for part in str(lesson).split(" / "):
            p = part.strip()
            if p:
                bullets.append(p)
    for m in missed[:2]:
        if "CALL" in str(m).upper() and ("급락" in str(m) or "하락" in str(m)):
            bullets.append("콜 몰림 ≠ 상승. 급락일과 겹치면 콜 매도/헤지 가능")
        elif "PUT" in str(m).upper() and "상승" in str(m):
            bullets.append("풋 몰림 ≠ 하락. 헤지·풋 매도일 수 있음")

    chg = dod.get("spot_change_pct")
    prev_calls = dod.get("prev_top_calls") or []
    if prev_calls and chg is not None and chg < -0.5:
        try:
            s = float(prev_calls[0]["strike"])
            bullets.append(
                f"어제 콜 관심 {_fmt_px(s)}가 있어도 오늘 주가는 하락 "
                f"→ 해당 OI/거래만으로 상방 단정 불가 (이번 관측)"
            )
        except (TypeError, ValueError, KeyError):
            pass

    # 중복 제거
    seen = set()
    out = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            out.append(b)
    if not out:
        L.append("- 오늘은 새 교훈보다 관측 기록이 우선입니다. 표본이 쌓이면 여기가 채워집니다.")
    else:
        for b in out[:4]:
            L.append(f"- {b}")
        L.append("- ※ 단일 사례는 학습 후보일 뿐, 예측 규칙으로 바로 쓰지 않습니다.")
    return "\n".join(L)


def past_compare_block(ticker: str, learning_context: dict | None = None) -> str:
    """📚 과거 사례와 누적 비교."""
    import learning as learn
    import pattern_store as ps

    L = ["📚 과거 사례와 누적 비교"]
    ctx = learning_context or {}
    s7 = ctx.get("최근7일") or learn.cumulative_stats(ticker, limit=7)
    s30 = ctx.get("최근30일") or learn.cumulative_stats(ticker, limit=30)
    stats = s30 if s30.get("available") else s7
    if not stats.get("available"):
        L.append("- 아직 누적 채점·패턴 표본이 부족해요.")
    else:
        n = stats.get("n") or 0
        L.append(
            f"- 최근 {n}일: 밴드 {stats.get('band_accuracy_pct')}% · "
            f"관심가(지지측) {stats.get('support_accuracy_pct')}% · "
            f"방향 라벨 {stats.get('direction_accuracy_pct')}%"
        )
        top = stats.get("top_missed_signals") or []
        if top:
            L.append("- 자주 반복된 놓침:")
            for t in top[:3]:
                sig = (t.get("signal") or "")[:70]
                L.append(f"  * {sig} (×{t.get('count')})")

    st = ps.pattern_state(ps.PATTERN_BREAKOUT_EXPAND)
    if st.get("n", 0) > 0:
        rate = st.get("hit_rate")
        rate_s = f"{rate*100:.0f}%" if rate is not None else "-"
        tag = "활성" if st["status"] == "active" else "학습 후보"
        L.append(
            f"- 패턴 '{st['label'][:40]}…': "
            f"[{tag}] 표본 {st['n']}/{st['min_samples']} · 적중률 {rate_s}"
        )
        if st["status"] != "active":
            L.append("  → 반복성 부족 · 예측 가중치 미반영")
    else:
        L.append("- 돌파+확장 패턴 관찰은 아직 없음 (생기면 여기 누적).")
    return "\n".join(L)


def next_observe_block(data: dict, base: dict, eventinfo: dict | None = None) -> str:
    """🔮 다음 관찰 포인트 — 예측이 아니라 확인 항목."""
    import report_evidence as ev

    L = ["🔮 다음 관찰 포인트"]
    L.append("다음에 같은 신호가 다시 나타나면 결과를 확인합니다 (단정 금지).")
    spot = data.get("spot")
    levels = base.get("levels") or {}
    res = ev._nearest(levels, float(spot), "res") if spot is not None else None
    sup = ev._nearest(levels, float(spot), "sup") if spot is not None else None
    exp = levels.get("expansion_up") or ((eventinfo or {}).get("next_session") or {}).get(
        "expansion_up"
    )

    if res:
        L.append(f"- {_fmt_px(res)} 돌파가 거래량 증가와 함께 유지되는지")
    if exp and exp.get("zone"):
        z0, z1 = exp["zone"]
        L.append(f"- 유지 시 ${z0}~${z1} 구간에 실제로 반응이 나오는지 (목표가 아님)")
    if sup:
        L.append(f"- {_fmt_px(sup)} 이탈 시 주가·옵션 관심이 어떻게 옮겨가는지")
    L.append("- 먼 행사가 콜 OI가 커도, 다음날 주가가 반대로 가면 '설명 실패'로 기록")
    if not res and not sup:
        L.append("- 가까운 관심 가격이 생기면 그때부터 관찰 체크리스트를 채웁니다.")
    return "\n".join(L)


def limits_block(base: dict | None = None) -> str:
    """⚠️ 데이터 한계."""
    L = [
        "⚠️ 데이터 한계",
        "- OI만으로 매수/매도 방향을 확정할 수 없습니다.",
        "- 거래량만으로 방향을 확정할 수 없습니다.",
        "- 표본이 부족한 패턴은 학습 규칙으로 채택하지 않습니다.",
        "- 옵션 밴드는 예상 변동 범위일 뿐 천장/바닥이 아닙니다.",
    ]
    if base and (base.get("low_confidence") or (base.get("levels") or {}).get("low_confidence")):
        L.append("- 오늘은 OI 저신뢰 모드라 위 한계가 더 큽니다.")
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
    import pattern_store

    ticker = data.get("ticker", "")
    date = data.get("date", "")
    L: list[str] = []
    L.append(f"📊 {ticker} 옵션 데일리 리포트 - {date}")
    L.append("")
    L.append(
        "이 리포트는 매일 하나의 실험 결과를 쌓아, "
        "우리 종목에서 실제로 통하는 옵션 신호를 찾는 기록입니다."
    )
    L.append("")

    banner = __import__("report_evidence").low_confidence_banner(base)
    if banner:
        L.append(banner)
        L.append("")

    L.append("① 오늘 결과")
    L.append(market_clock.format_price_line(data))
    L.append("")

    earn = (eventinfo or {}).get("earnings") or {}
    if earn.get("phase") in ("임박", "직후") and earn.get("message"):
        L.append("🚨 이벤트")
        L.append(earn["message"])
        L.append("")

    L.append(
        option_change_block(data, base, day_over_day, anomalies, volume_anomaly)
    )
    L.append("")
    L.append(option_vs_price_block(data, base, day_over_day, feedback))
    L.append("")
    L.append(interest_prices_block(data, base))
    L.append("")
    L.append(case_lesson_block(feedback, day_over_day, learning_context))
    L.append("")
    L.append(past_compare_block(ticker, learning_context))
    L.append("")
    L.append(pattern_store.format_candidates_block())
    L.append("")
    L.append(next_observe_block(data, base, eventinfo))
    L.append("")
    L.append(limits_block(base))
    L.append("")
    L.append("⚠️ 이 리포트는 투자 조언이 아니라 관측·학습 기록입니다.")
    return "\n".join(L)
