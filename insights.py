"""리포트 본문(자연어) 생성 오케스트레이션.

- 1순위: ChatGPT(OpenAI) 로 일반인용 친근한 리포트 생성.
- 폴백: API 키 없음/실패 시 규칙 기반 친근한 리포트(간단 버전).
"""
from __future__ import annotations

import config

_ROLE_LABEL = {"this_week": "이번주", "next_week": "다음주", "monthly": "월간"}


def _fmt_clusters_sentence(clusters: list[dict], verb: str) -> str:
    if not clusters:
        return ""
    top = clusters[0]
    s = (f"**${top['strike']:g} 근처가 가장 강해요.** 옵션 시장에 '{verb}' 계약이 "
         f"{top['oi']:,}개 쌓여 있거든요. 마치 ${top['strike']:g} 가격표를 든 "
         f"{top['oi']:,}명이 대기 중인 셈이에요.")
    if len(clusters) > 1:
        s += f" 그다음은 ${clusters[1]['strike']:g}({clusters[1]['oi']:,}개)예요."
    return s


def build_friendly_fallback(data, base, anomalies, volume_anomaly, prev) -> str:
    """LLM 없이도 읽히는 친근한 리포트(규칙 기반)."""
    spot = data["spot"]
    prev_close = data.get("previous_close")
    L: list[str] = []
    L.append(f"📊 오늘의 {data['ticker']} 옵션 시장 이야기 - {data['date']}")
    L.append("")

    if prev_close:
        chg = round((spot - prev_close) / prev_close * 100, 2)
        arrow = "올랐어요" if chg > 0 else "내렸어요"
        L.append(f"💰 지금 주가: ${spot} (어제보다 {chg:+}% {arrow})")
    else:
        L.append(f"💰 지금 주가: ${spot}")
    L.append("")

    # 한 줄 요약
    senti = base.get("sentiment")
    mood = {"강세": "상승 쪽에 기대가 큰", "약세": "하락을 걱정하는", "중립": "관망하는"}.get(senti, "")
    L.append("🎯 한 줄 요약")
    L.append(f"오늘 시장은 {mood} 분위기예요. (콜/풋 비율 {base.get('call_put_volume_ratio')})")
    L.append("")

    near = base["expiry_metrics"].get("this_week") or next(
        iter(base["expiry_metrics"].values())
    )
    if base.get("oi_source") and "전일" in str(base.get("oi_source")):
        L.append("※ 아래 지지/저항은 아직 오늘 OI가 갱신되지 않아 '전일 기준'이에요.")
        L.append("")

    L.append("🟢 지지선 (여기서 반등 기대)")
    L.append(_fmt_clusters_sentence(near["put_oi_clusters"], "이 가격에 사겠다") or "- 데이터 없음")
    L.append("")
    L.append("🔴 저항선 (여기서 막힐 것으로 예상)")
    L.append(_fmt_clusters_sentence(near["call_oi_clusters"], "이 가격에 팔겠다") or "- 데이터 없음")
    L.append("")

    # 시장 온도
    cpr = base.get("call_put_volume_ratio")
    if cpr:
        up = round(cpr / (1 + cpr) * 100)
        L.append("🌡️ 오늘 시장 온도")
        L.append(f"오늘 옵션 거래에서 약 100명 중 {up}명은 상승, {100-up}명은 하락에 걸었어요. "
                 f"→ 분위기는 '{senti}'.")
        L.append("")

    # 예상 범위
    st = near.get("straddle")
    if st:
        L.append("📈 이번주 예상 이동 범위")
        L.append(f"옵션 가격을 역산한 시장의 예상: **${st['lower']} ~ ${st['upper']}** "
                 f"(±{st['band_pct']}%). 대략 이 범위 안에서 움직일 걸로 봐요.")
        L.append("")

    # 특이
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        L.append("⚠️ 오늘 특이한 일")
        L.append(f"거래량이 평소({int(volume_anomaly['recent_avg']):,}건)의 "
                 f"{volume_anomaly['mult']}배인 {int(volume_anomaly['today']):,}건! "
                 f"뭔가 큰 변화 신호일 수 있어요 — 뉴스 확인을 권해요.")
        L.append("")

    # 액션
    L.append("🎯 그래서 뭘 해야 하나")
    if near["put_oi_clusters"]:
        L.append(f"- 보유 중이면 ${near['put_oi_clusters'][0]['strike']:g} 지지선이 지켜지는지 보세요.")
    if near["call_oi_clusters"]:
        L.append(f"- ${near['call_oi_clusters'][0]['strike']:g} 저항 근처에선 매도 압력이 예상돼요.")
    L.append("- 급증한 거래량이 있으면 관련 뉴스부터 확인하세요." if volume_anomaly and volume_anomaly.get("is_anomaly") else "- 서두르기보다 지지/저항 반응을 지켜보세요.")
    L.append("")
    L.append("⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다.")
    return "\n".join(L)


def build_narrative(data, base, anomalies, volume_anomaly, prev, trend) -> tuple[str, str]:
    """(본문, 출처). 출처: 'openai' | 'rule'."""
    import llm

    text = llm.generate_report(data, base, anomalies, volume_anomaly, prev, trend)
    if text:
        return text, "openai"
    return build_friendly_fallback(data, base, anomalies, volume_anomaly, prev), "rule"
