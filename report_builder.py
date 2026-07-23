"""텍스트 리포트 조립 (지시서 포맷 + 리뷰 피드백 반영).

섹션: 🎯 오늘 핵심 / 💡 인사이트 / 🔥 어제 대비 특이 관찰(anomalies)
      / 📈 만기별 예상 밴드 / 📊 V/OI 상위 / 🔊 볼륨 상위
"""
from __future__ import annotations

_ROLE_LABEL = {"this_week": "이번주", "next_week": "다음주", "monthly": "월간"}


def _pct(value) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value}"


def _clusters_str(clusters: list[dict]) -> str:
    if not clusters:
        return "N/A"
    return ", ".join(f"${c['strike']:g}(OI {c['oi']:,})" for c in clusters)


def build_report(
    data: dict,
    base: dict,
    anomalies: list[dict],
    volume_anomaly: dict | None,
    insights: list[str],
    ai_narrative: str | None = None,
    ai_source: str = "rule",
) -> str:
    L: list[str] = []
    tk = data["ticker"]

    L.append(f"📊 {tk} Options Daily Report - {data['date']}")
    if base.get("oi_data_stale"):
        L.append("⚠ OI 데이터 미갱신 — OI 기반 지표는 전일 값 기준 (V/OI·OI 이상신호 생략)")
    L.append("")

    # 🎯 오늘 핵심
    prev_close = data.get("previous_close")
    if prev_close:
        chg = round(data["spot"] - prev_close, 2)
        chg_pct = round((data["spot"] - prev_close) / prev_close * 100, 2)
        chg_str = f"{_pct(chg)}, {_pct(chg_pct)}%"
    else:
        chg_str = "N/A"

    near = base["expiry_metrics"].get("this_week") or next(
        iter(base["expiry_metrics"].values())
    )

    oi_tag = " (전일 OI 기준)" if base.get("oi_data_stale") else ""
    L.append("🎯 오늘 핵심")
    L.append(f"- 현재가: ${data['spot']} ({chg_str})")
    L.append(f"- 콜/풋 볼륨 비율 기반 심리: {base['sentiment']} "
             f"(C/P {base['call_put_volume_ratio']})")
    L.append(f"- 저항선 후보{oi_tag}: {_clusters_str(near['call_oi_clusters'])}")
    L.append(f"- 지지선 후보{oi_tag}: {_clusters_str(near['put_oi_clusters'])}")
    L.append("")

    # 💡 인사이트 (규칙 기반)
    L.append("💡 오늘의 인사이트")
    for line in insights:
        L.append(f"- {line}")
    L.append("")

    # 🤖 AI 해설 (ChatGPT) — 키가 있을 때만
    if ai_narrative and ai_source == "openai":
        L.append("🤖 AI 해설 (ChatGPT)")
        for line in ai_narrative.splitlines():
            L.append(line)
        L.append("")

    # 🔥 어제 대비 특이 관찰
    L.append("🔥 어제 대비 특이 관찰")
    has_prev = bool(data.get("volume_anomaly")) or bool(anomalies)
    lines_added = 0
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        L.append(f"- 거래량 이상: 오늘 {int(volume_anomaly['today']):,} "
                 f"(최근 평균 {int(volume_anomaly['recent_avg']):,} 대비 {volume_anomaly['mult']}배)")
        lines_added += 1
    if anomalies:
        for a in anomalies[:8]:
            L.append(f"- {a['message']}")
            lines_added += 1
    if lines_added == 0:
        if has_prev:
            L.append("- 특이 급변 없음")
        else:
            L.append("비교 데이터 없음 — 내일부터 표시됩니다")
    L.append("")

    # 📈 만기별 예상 밴드
    L.append("📈 만기별 예상 밴드 (Straddle 기반)")
    for role in ("this_week", "next_week", "monthly"):
        em = base["expiry_metrics"].get(role)
        if not em:
            continue
        st = em["straddle"]
        label = _ROLE_LABEL.get(role, role)
        if st:
            L.append(f"- {label} ({em['date']}): ${st['lower']} ~ ${st['upper']} "
                     f"(±{st['band_pct']}%)")
        else:
            L.append(f"- {label} ({em['date']}): 밴드 계산 불가")
    L.append("")

    # 📊 V/OI 상위 (최소 볼륨 필터 적용)
    L.append(f"📊 V/OI 상위 {len(base['top_voi'])} (볼륨 필터 적용)")
    if not base["top_voi"]:
        L.append("- 조건(최소 볼륨/OI)을 만족하는 계약 없음")
    else:
        for i, r in enumerate(base["top_voi"], 1):
            L.append(
                f"{i}. {r['expiry']} {r['type']} ${r['strike']:g} — "
                f"V/OI {r['voi']} ({r['class']}) [거래량 {r['volume']:,} / OI {r['oi']:,}]"
            )
    L.append("")

    # 🔊 볼륨 상위 (절대 거래량)
    L.append(f"🔊 거래량 상위 {len(base['top_volume'])}")
    for i, r in enumerate(base["top_volume"], 1):
        L.append(
            f"{i}. {r['expiry']} {r['type']} ${r['strike']:g} — "
            f"거래량 {r['volume']:,} (OI {r['oi']:,}, V/OI {r['voi']})"
        )

    return "\n".join(L)
