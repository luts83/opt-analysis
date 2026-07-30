"""리포트 본문(자연어) 생성 오케스트레이션.

- 1순위: ChatGPT(OpenAI) 로 일반인용 친근한 리포트 생성.
- 폴백: API 키 없음/실패 시 규칙 기반 리포트.
- 핵심 섹션(제목/온도/레벨/밴드/시나리오/학습/체크포인트)은
  시스템이 근거 문장으로 강제 교체해 LLM이 근거를 빼도 복구한다.
"""
from __future__ import annotations

import events
import report_evidence as ev


def _one_liner(data, base, eventinfo) -> str:
    return ev.one_liner(data, base, eventinfo)


def build_friendly_fallback(
    data, base, anomalies, volume_anomaly, prev, eventinfo=None, day_over_day=None,
    feedback=None, learning_context=None,
) -> str:
    """LLM 없이도 읽히는 근거 포함 리포트(규칙 기반)."""
    import learning
    import market_clock

    earn = (eventinfo or {}).get("earnings") or {}
    in_earnings = earn.get("phase") in ("임박", "직후")
    nxt = (eventinfo or {}).get("next_session") or {}
    fb = feedback or data.get("prediction_feedback")
    ctx = learning_context or data.get("learning_context")
    spot = data.get("spot")

    L: list[str] = []
    fb_text = learning.format_feedback_section(fb)
    if fb_text:
        L.append(fb_text.rstrip())
        L.append("")

    L.append(f"📊 오늘의 {data['ticker']} 옵션 시장 이야기 - {data['date']}")
    L.append("")
    L.append(f"🎯 {ev.one_liner(data, base, eventinfo)}")
    L.append("")
    L.append(market_clock.format_price_line(data))
    L.append("")

    if in_earnings and earn.get("message"):
        L.append("🚨 이벤트 경고")
        L.append(earn["message"])
        L.append("")
    price = (eventinfo or {}).get("price") or {}
    if price.get("abnormal") and price.get("note"):
        L.append("🚨 이벤트 경고")
        L.append(price["note"])
        L.append("")

    temp = ev.sentiment_block(base, in_earnings=in_earnings)
    if temp:
        L.append(temp)
        L.append("")

    if base.get("oi_source") and "전일" in str(base.get("oi_source")):
        L.append("※ 강한 지지/저항 OI는 전일 기준.")
        L.append("")

    L.append(ev.levels_block(base.get("levels"), spot))
    L.append("")

    band = ev.band_block(base)
    if band:
        L.append(band)
        L.append("")

    scen = ev.format_scenarios(nxt)
    if scen:
        L.append(scen)
        L.append("")

    unusual: list[str] = []
    if day_over_day and day_over_day.get("highlights"):
        unusual.extend(day_over_day["highlights"])
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        unusual.append(f"거래량 이상: 평소 대비 {volume_anomaly['mult']}배")
    for a in (anomalies or [])[:4]:
        unusual.append(a["message"])
    if unusual:
        L.append("⚠️ 오늘 특이한 일")
        for u in unusual:
            L.append(f"- {u}")
        L.append("")

    L.append(ev.learning_section(data.get("ticker", ""), fb, ctx))
    L.append("")
    L.append(ev.format_checkpoints(nxt))
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
    nxt = (eventinfo or {}).get("next_session") or {}
    earn = (eventinfo or {}).get("earnings") or {}
    in_earnings = earn.get("phase") in ("임박", "직후")
    spot = data.get("spot")

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
    text = market_clock.apply_session_to_narrative(text, data, eventinfo)

    # 근거 블록으로 핵심 섹션 강제 교체 (LLM이 근거를 빼도 복구)
    text = ev.enforce_all(
        text,
        title=ev.one_liner(data, base, eventinfo),
        temp=ev.sentiment_block(base, in_earnings=in_earnings),
        levels=ev.levels_block(base.get("levels"), spot),
        band=ev.band_block(base),
        scenarios=ev.format_scenarios(nxt),
        checkpoints=ev.format_checkpoints(nxt),
        learning=ev.learning_section(data.get("ticker", ""), fb, ctx),
    )
    text = events.with_linked_news(text, eventinfo)

    fb_block = learning.format_feedback_section(fb)
    if fb_block:
        text = re.sub(
            r"(?m)^📊\s*(직전 리포트 채점|어제 예측 vs 오늘 실제).*?(?=^📊 오늘의|\Z)",
            "",
            text,
            count=1,
            flags=re.S,
        )
        text = fb_block + "\n" + text.lstrip()

    return text, src
