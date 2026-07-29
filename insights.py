"""리포트 본문(자연어) 생성 오케스트레이션.

- 1순위: ChatGPT(OpenAI) 로 일반인용 친근한 리포트 생성.
- 폴백: API 키 없음/실패 시 규칙 기반 친근한 리포트(간단 버전).
"""
from __future__ import annotations

import events


def _level_block(levels: dict | None) -> list[str]:
    """지지/저항을 대칭 구조로."""
    if not levels:
        return ["- 데이터 없음"]
    L: list[str] = []

    def _items(key: str) -> list[str]:
        out = []
        for item in levels.get(key) or []:
            strike = item["strike"]
            if item.get("oi"):
                out.append(f"${strike:g} (계약 {item['oi']:,}개 대기)")
            elif item.get("volume"):
                out.append(f"${strike:g} (거래 {item['volume']:,})")
            else:
                out.append(f"${strike:g}")
        return out

    L.append("🟢 지지선")
    strong_s = _items("strong_support")
    near_s = _items("near_support")
    if strong_s:
        L.append(f"- 강한: {', '.join(strong_s)}")
    if near_s:
        L.append(f"- 단기: {', '.join(near_s)}")
    if not strong_s and not near_s:
        L.append("- (없음)")

    L.append("🔴 저항선")
    near_r = _items("near_resistance")
    strong_r = _items("strong_resistance")
    if near_r:
        L.append(f"- 단기: {', '.join(near_r)}")
    if strong_r:
        L.append(f"- 강한: {', '.join(strong_r)}")
    if not near_r and not strong_r:
        L.append("- (없음)")
    return L


def _one_liner(data, base, eventinfo) -> str:
    ticker = data.get("ticker", "")
    levels = base.get("levels") or {}
    spot = data.get("spot")
    senti = base.get("sentiment")
    strong = (levels.get("strong_support") or [{}])[0].get("strike")
    near_s = (levels.get("near_support") or [{}])[0].get("strike")
    near_r = (levels.get("near_resistance") or [{}])[0].get("strike")
    earn = (eventinfo or {}).get("earnings") or {}
    if earn.get("phase") in ("임박", "직후"):
        return f"{ticker} 실적 {earn.get('phase')} — 변동성·레벨 반응 우선"
    chg = ((eventinfo or {}).get("price") or {}).get("change_pct")
    if chg is not None and chg <= -3 and strong and spot is not None:
        if spot <= strong:
            return f"{ticker} 급락 — ${strong:g} 지지 이미 이탈, 추가 하락 경계"
        else:
            return f"{ticker} 급락 ${strong:g} 지지선 테스트 임박"
    if chg is not None and chg >= 3 and near_r:
        if spot is not None and spot >= near_r:
            return f"{ticker} 급등 — ${near_r:g} 저항 돌파, 추가 상승 여부 주시"
        return f"{ticker} 급등 ${near_r:g} 저항 테스트"
    if senti == "약세" and near_s:
        if spot is not None and spot <= near_s:
            return f"{ticker} 약세 — ${near_s:g} 지지 이탈, 하방 확인 필요"
        return f"{ticker} 약세 — ${near_s:g} 지지 이탈 여부 주시"
    if senti == "강세" and near_r:
        return f"{ticker} 강세 — ${near_r:g} 돌파 여부 주시"
    if strong and spot:
        return f"{ticker} ${spot:g} — 핵심 레벨 ${strong:g} 주시"
    return f"{ticker} 옵션 시장 요약"


def build_friendly_fallback(
    data, base, anomalies, volume_anomaly, prev, eventinfo=None, day_over_day=None,
    feedback=None, learning_context=None,
) -> str:
    """LLM 없이도 읽히는 짧은 리포트(규칙 기반)."""
    import learning
    import market_clock

    earn = (eventinfo or {}).get("earnings") or {}
    in_earnings = earn.get("phase") in ("임박", "직후")
    senti = base.get("sentiment")
    near = base["expiry_metrics"].get("this_week") or next(
        iter(base["expiry_metrics"].values())
    )
    nxt = (eventinfo or {}).get("next_session") or {}

    L: list[str] = []
    fb_text = learning.format_feedback_section(feedback or data.get("prediction_feedback"))
    if fb_text:
        L.append(fb_text.rstrip())
        L.append("")

    L.append(f"📊 오늘의 {data['ticker']} 옵션 시장 이야기 - {data['date']}")
    L.append("")
    L.append(f"🎯 {_one_liner(data, base, eventinfo)}")
    L.append("")
    L.append(market_clock.format_price_line(data))
    L.append("")

    # 이벤트 — 있을 때만
    if in_earnings and earn.get("message"):
        L.append("🚨 이벤트 경고")
        L.append(earn["message"])
        L.append("")
    price = (eventinfo or {}).get("price") or {}
    if price.get("abnormal") and price.get("note"):
        L.append("🚨 이벤트 경고")
        L.append(price["note"])
        L.append("")

    cpr = base.get("call_put_volume_ratio")
    if cpr:
        up = round(cpr / (1 + cpr) * 100)
        caveat = " (어닝 — 참고용)" if in_earnings else ""
        L.append("🌡️ 시장 온도")
        L.append(f"상승 베팅 ~{up}% / 하락 ~{100 - up}% → '{senti}'{caveat}")
        L.append("")

    if base.get("oi_source") and "전일" in str(base.get("oi_source")):
        L.append("※ 강한 지지/저항 OI는 전일 기준.")
        L.append("")
    L.extend(_level_block(base.get("levels")))
    L.append("")

    bt = base.get("band_trend") or {}
    rows = bt.get("rows") or []
    if not rows:
        import metrics as _m
        bt = _m.build_band_trend(base) or {}
        rows = bt.get("rows") or []
    st = near.get("straddle")
    if rows or st:
        L.append("📈 예상 범위")
        if rows:
            for r in rows:
                L.append(
                    f"{r['label']}: ${r['lower']}~${r['upper']} (±{r['band_pct']}%)"
                )
        elif st:
            L.append(
                f"이번주: ${round(float(st['lower']))}~${round(float(st['upper']))} "
                f"(±{round(float(st['band_pct']))}%)"
            )
        if bt.get("interpretation"):
            tip = bt["interpretation"]
            if not tip.startswith("→"):
                tip = f"→ {tip}"
            L.append(tip)
        L.append("")

    if nxt.get("scenarios"):
        L.append(nxt.get("section_title") or "🔮 시나리오 (가능성 순)")
        if nxt.get("gap_note"):
            L.append(nxt["gap_note"])
        for s in nxt["scenarios"]:
            L.append(f"- {s['name']}: {s['condition']}")
            if s.get("watch"):
                L.append(f"  → {s['watch']}")
        L.append("")

    # 특이사항 — 진짜 특이만
    unusual: list[str] = []
    if day_over_day and day_over_day.get("highlights"):
        unusual.extend(day_over_day["highlights"])
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        unusual.append(
            f"거래량 이상: 평소 대비 {volume_anomaly['mult']}배"
        )
    for a in (anomalies or [])[:4]:
        unusual.append(a["message"])
    if unusual:
        L.append("⚠️ 오늘 특이한 일")
        for u in unusual:
            L.append(f"- {u}")
        L.append("")

    L.append("🎯 오늘 체크포인트")
    checks = nxt.get("checkpoints") or []
    if checks:
        for c in checks:
            L.append(f"- {c}")
    elif nxt.get("action_hint"):
        for part in str(nxt["action_hint"]).split(" / "):
            L.append(f"- {part.strip()}")
    else:
        L.append("- 지지·저항 반응을 먼저 확인하세요.")
    L.append("")
    L.append("⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다.")
    return "\n".join(L)


def build_narrative(
    data, base, anomalies, volume_anomaly, prev, trend, eventinfo=None, day_over_day=None,
    feedback=None, learning_context=None,
) -> tuple[str, str]:
    """(본문, 출처). 출처: 'openai' | 'rule'."""
    import llm
    import market_clock
    import learning
    import re
    import report_polish

    fb = feedback if feedback is not None else data.get("prediction_feedback")
    ctx = learning_context if learning_context is not None else data.get("learning_context")

    text = llm.generate_report(
        data, base, anomalies, volume_anomaly, prev, trend, eventinfo, day_over_day,
        feedback=fb, learning_context=ctx,
    )
    if text:
        src = "openai"
    else:
        text = build_friendly_fallback(
            data, base, anomalies, volume_anomaly, prev, eventinfo, day_over_day,
            feedback=fb, learning_context=ctx,
        )
        src = "rule"

    text = report_polish.polish_narrative(text)
    text = events.with_linked_news(text, eventinfo)
    text = market_clock.apply_session_to_narrative(text, data, eventinfo)

    # 채점 블록은 항상 시스템 포맷으로 교체/삽입
    fb_block = learning.format_feedback_section(fb)
    if fb_block:
        # 기존 채점/어제예측 섹션 제거 후 상단 삽입
        text = re.sub(
            r"(?m)^📊\s*(직전 리포트 채점|어제 예측 vs 오늘 실제).*?(?=^📊 오늘의|\Z)",
            "",
            text,
            count=1,
            flags=re.S,
        )
        text = fb_block + "\n" + text.lstrip()

    return text, src
