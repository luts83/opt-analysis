"""리포트 본문(자연어) 생성 오케스트레이션.

- 1순위: ChatGPT(OpenAI) 로 일반인용 친근한 리포트 생성.
- 폴백: API 키 없음/실패 시 규칙 기반 친근한 리포트(간단 버전).
"""
from __future__ import annotations

import events


def _level_lines(levels: dict | None) -> list[str]:
    if not levels:
        return ["- 데이터 없음"]
    L: list[str] = []
    for key, emoji, label in (
        ("strong_support", "🟢", "강한 지지"),
        ("near_support", "🟢", "단기 지지"),
        ("near_resistance", "🔴", "단기 저항"),
        ("strong_resistance", "🔴", "강한 저항"),
    ):
        for item in levels.get(key) or []:
            strike = item["strike"]
            if "oi" in item and item["oi"]:
                L.append(
                    f"{emoji} {label}: ${strike:g} ⭐ — "
                    f"옵션 시장에 이 가격 계약이 {item['oi']:,}개 몰려 있어요. "
                    f"마치 ${strike:g} 가격표를 든 대기자 {item['oi']:,}명이 있는 셈이에요."
                )
            elif "volume" in item:
                L.append(
                    f"{emoji} {label}: ${strike:g} — "
                    f"현재가 근처에서 거래가 {item['volume']:,}건 몰린 심리적 레벨이에요."
                )
    return L or ["- 데이터 없음"]


def build_friendly_fallback(
    data, base, anomalies, volume_anomaly, prev, eventinfo=None, day_over_day=None
) -> str:
    """LLM 없이도 읽히는 친근한 리포트(규칙 기반). 섹션 순서 고정."""
    spot = data["spot"]
    prev_close = data.get("previous_close")
    earn = (eventinfo or {}).get("earnings") or {}
    in_earnings = earn.get("phase") in ("임박", "직후")
    senti = base.get("sentiment")
    near = base["expiry_metrics"].get("this_week") or next(
        iter(base["expiry_metrics"].values())
    )

    L: list[str] = []
    L.append(f"📊 오늘의 {data['ticker']} 옵션 시장 이야기 - {data['date']}")
    L.append("")

    # 1) 한 줄 요약
    mood = {"강세": "상승 쪽에 기대가 큰", "약세": "하락을 걱정하는", "중립": "관망하는"}.get(
        senti, ""
    )
    L.append("🎯 한 줄 요약")
    if in_earnings:
        sur = earn.get("surprise_pct")
        sur_s = f" (EPS 서프라이즈 {sur:+.1f}%)" if sur is not None else ""
        L.append(
            f"지금은 실적 발표 {earn.get('phase')} 국면이에요{sur_s}. "
            f"콜/풋 비율상 '{senti}'처럼 보이지만 어닝 전후엔 단정하지 마세요."
        )
    else:
        L.append(
            f"오늘 시장은 {mood} 분위기예요. (콜/풋 비율 {base.get('call_put_volume_ratio')})"
        )
    L.append("")

    # 2) 주가
    regular = data.get("regular_close")
    extended = data.get("extended_price")
    gap = data.get("extended_vs_regular_pct")
    if regular and extended and gap is not None and abs(gap) >= 1.0:
        L.append(
            f"💰 분석 기준가: ${spot} (장외/프리마켓) · "
            f"정규장 종가 ${regular:g} → 장외 ${extended:g} ({gap:+.1f}%)"
        )
    elif prev_close:
        chg = round((spot - prev_close) / prev_close * 100, 2)
        arrow = "올랐어요" if chg > 0 else "내렸어요"
        L.append(f"💰 지금 주가: ${spot} (어제보다 {chg:+}% {arrow})")
    else:
        L.append(f"💰 지금 주가: ${spot}")
    L.append("")

    # 3) 이벤트
    if earn and earn.get("message"):
        L.append(earn["message"])
        L.append("")
    price = (eventinfo or {}).get("price") or {}
    if price.get("note"):
        L.append(f"💱 가격 주의: {price['note']}")
        L.append("")

    # 4) 시장 온도
    cpr = base.get("call_put_volume_ratio")
    if cpr:
        up = round(cpr / (1 + cpr) * 100)
        caveat = " (어닝 국면 — 참고용, 단정 금지)" if in_earnings else ""
        L.append("🌡️ 시장 온도")
        L.append(
            f"오늘 옵션 거래에서 약 100명 중 {up}명은 상승, {100 - up}명은 하락에 걸었어요. "
            f"→ 분위기는 '{senti}'{caveat}."
        )
        L.append("")

    # 5) 지지/저항
    if base.get("oi_source") and "전일" in str(base.get("oi_source")):
        L.append("※ 아래 강한 지지/저항 OI는 오늘 미갱신이라 '전일 기준'이에요.")
        L.append("")
    L.append("🟢🔴 지지선 / 저항선")
    L.extend(_level_lines(base.get("levels")))
    L.append("")

    # 6) 예상 범위 + 밴드 트렌드
    st = near.get("straddle")
    if st:
        L.append("📈 이번주 예상 범위")
        L.append(
            f"옵션 가격을 역산한 시장의 예상: **${st['lower']} ~ ${st['upper']}** "
            f"(±{st['band_pct']}%)."
        )
        bt = base.get("band_trend") or {}
        if bt.get("interpretation"):
            L.append(f"💡 {bt['interpretation']}")
        L.append("")

    # 7) 다음 장 시나리오
    nxt = (eventinfo or {}).get("next_session") or {}
    if nxt:
        L.append("🔮 다음 장 개장 시나리오")
        if nxt.get("gap_note"):
            L.append(nxt["gap_note"])
        for s in nxt.get("scenarios") or []:
            L.append(f"- {s['name']}: {s['condition']}")
            L.append(f"  → {s['watch']}")
        if nxt.get("action_hint"):
            L.append(f"※ {nxt['action_hint']}")
        L.append("")

    # 8) 특이 / 어제대비
    L.append("⚠️ 오늘 특이한 일")
    noted = False
    if day_over_day and day_over_day.get("highlights"):
        for h in day_over_day["highlights"]:
            L.append(f"- {h}")
        noted = True
    elif day_over_day and day_over_day.get("note"):
        L.append(f"- {day_over_day['note']}")
        noted = True
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        L.append(
            f"- 거래량이 평소({int(volume_anomaly['recent_avg']):,}건)의 "
            f"{volume_anomaly['mult']}배!"
        )
        noted = True
    for a in (anomalies or [])[:4]:
        L.append(f"- {a['message']}")
        noted = True
    if not noted:
        L.append("- 특별히 눈에 띄는 급변 신호는 없어요.")
    L.append("")

    # 9) 뉴스
    news = (eventinfo or {}).get("news") or []
    if news:
        L.append("📰 관련 뉴스")
        L.extend(events.format_news_lines(news, limit=4))
        L.append("")

    # 10) 액션
    L.append("🎯 그래서 뭘 해야 하나")
    if in_earnings:
        L.append("- 실적 발표 전후예요. 변동성이 크니 지지/저항 반응을 우선 보세요.")
    if nxt.get("action_hint"):
        L.append(f"- {nxt['action_hint']}")
    levels = base.get("levels") or {}
    if levels.get("strong_support"):
        s = levels["strong_support"][0]
        L.append(f"- 보유 중이면 강한 지지 ${s['strike']:g}이 지켜지는지 보세요.")
    if levels.get("strong_resistance"):
        r = levels["strong_resistance"][0]
        L.append(f"- 강한 저항 ${r['strike']:g} 근처에서는 매도 압력을 염두에 두세요.")
    L.append("")
    L.append("⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다.")
    return "\n".join(L)


def build_narrative(
    data, base, anomalies, volume_anomaly, prev, trend, eventinfo=None, day_over_day=None
) -> tuple[str, str]:
    """(본문, 출처). 출처: 'openai' | 'rule'."""
    import llm

    text = llm.generate_report(
        data, base, anomalies, volume_anomaly, prev, trend, eventinfo, day_over_day
    )
    if text:
        return events.with_linked_news(text, eventinfo), "openai"
    return (
        events.with_linked_news(
            build_friendly_fallback(
                data, base, anomalies, volume_anomaly, prev, eventinfo, day_over_day
            ),
            eventinfo,
        ),
        "rule",
    )
