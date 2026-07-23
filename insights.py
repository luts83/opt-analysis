"""자연어 인사이트 생성.

지금(Phase 0)은 외부 API 없이 규칙 기반으로 오늘의 요약 문장을 만든다.
Priority 4(Claude API 해설)는 Phase 1/2 에서 build_llm_insights() 훅에 붙인다.
"""
from __future__ import annotations

import config

_ROLE_LABEL = {"this_week": "이번주", "next_week": "다음주", "monthly": "월간"}


def _far_otm_call_clusters(data: dict) -> list[dict]:
    """현재가 대비 멀리 있는(먼 OTM) 콜에 대량 OI 가 쌓인 지점.

    예: 8/21 만기 $70 콜 OI 15,602 같은 '급등/인수 베팅' 관측.
    """
    spot = data["spot"]
    threshold_strike = spot * config.FAR_OTM_CALL_MULT
    out = []
    for role, leg in data["expiries"].items():
        for r in leg["calls"]:
            if r["strike"] >= threshold_strike and r.get("openInterest", 0) >= config.FAR_OTM_MIN_OI:
                out.append(
                    {
                        "role": role,
                        "expiry": leg["date"],
                        "strike": r["strike"],
                        "oi": int(r["openInterest"]),
                    }
                )
    out.sort(key=lambda x: x["oi"], reverse=True)
    return out


def build_insights(
    data: dict,
    base: dict,
    anomalies: list[dict],
    volume_anomaly: dict | None,
) -> list[str]:
    """규칙 기반 자연어 인사이트 리스트."""
    ins: list[str] = []
    spot = data["spot"]

    # 0) OI 미갱신 경고 (장 시작 전/거래소 지연)
    if base.get("oi_data_stale"):
        ins.append(
            "⚠ 오늘 OI 데이터가 아직 갱신되지 않음(장 시작 전/거래소 지연) — "
            "OI 기반 저항·지지·클러스터는 전일 값 기준이며, OI 이상신호는 생략됨."
        )

    # 1) 심리
    cpr = base.get("call_put_volume_ratio")
    ins.append(
        f"콜/풋 볼륨비 {cpr} → 시장 심리 '{base['sentiment']}' "
        f"(콜 {base['total_call_volume']:,} vs 풋 {base['total_put_volume']:,})."
    )

    # 2) 이번주 저항/지지
    near = base["expiry_metrics"].get("this_week") or next(
        iter(base["expiry_metrics"].values())
    )
    if near["call_oi_clusters"]:
        top_res = near["call_oi_clusters"][0]
        ins.append(
            f"단기 최대 저항은 ${top_res['strike']:g} (콜 OI {top_res['oi']:,}), "
            f"현재가 ${spot} 대비 {round((top_res['strike']/spot-1)*100,1)}% 위."
        )
    if near["put_oi_clusters"]:
        top_sup = near["put_oi_clusters"][0]
        ins.append(
            f"단기 최대 지지는 ${top_sup['strike']:g} (풋 OI {top_sup['oi']:,}), "
            f"현재가 대비 {round((top_sup['strike']/spot-1)*100,1)}%."
        )

    # 3) 먼 OTM 콜 대량 베팅 (인수/급등 시나리오)
    for c in _far_otm_call_clusters(data)[:2]:
        ins.append(
            f"⭐ {c['expiry']} 만기 ${c['strike']:g} 콜에 OI {c['oi']:,} 집중 — "
            f"현재가의 {round(c['strike']/spot,2)}배 지점에 대량 베팅(급등/인수 시나리오 관측)."
        )

    # 4) 어제 대비 이상 신호
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        ins.append(
            f"전체 거래량 {int(volume_anomaly['today']):,}로 최근 평균의 "
            f"{volume_anomaly['mult']}배 — 평소보다 크게 과열."
        )
    for a in anomalies[:3]:
        ins.append("🔥 " + a["message"])

    # 5) 예상 밴드 한 줄 요약
    tw = base["expiry_metrics"].get("this_week")
    if tw and tw.get("straddle"):
        st = tw["straddle"]
        ins.append(
            f"이번주 예상 변동폭 ±{st['band_pct']}% (${st['lower']} ~ ${st['upper']})."
        )

    return ins


def build_ai_narrative(data, base, anomalies, volume_anomaly) -> tuple[str | None, str]:
    """ChatGPT(OpenAI) 자연어 해설을 생성한다.

    Returns:
        (narrative_text, source)
        - 성공 시: (요약문, "openai")
        - 키 없음/실패 시: (규칙기반 요약을 줄바꿈으로 이은 문자열, "rule")
    """
    import llm

    text = llm.generate_summary(data, base, anomalies, volume_anomaly)
    if text:
        return text, "openai"
    # 폴백: 규칙 기반 인사이트를 자연어 문단처럼
    rule = build_insights(data, base, anomalies, volume_anomaly)
    return "\n".join(f"- {line}" for line in rule), "rule"
