"""리포트 조립.

구성:
  [본문] 일반인용 친근한 리포트
  [부록] 📋 데이터 요약 — 본문에 없는 숫자만 (V/OI·거래량 상위, OI 급변)
"""
from __future__ import annotations


def _oi_str(entry: dict) -> str:
    oi = entry.get("oi", 0)
    if not oi:
        return "OI 데이터없음"
    tag = " 전일" if entry.get("oi_carried_forward") else ""
    return f"OI {oi:,}{tag}"


def _volume_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["   (없음)"]
    out = []
    for i, r in enumerate(rows, 1):
        voi = r.get("voi")
        voi_s = f"V/OI {voi}" if voi is not None else "V/OI -"
        out.append(
            f"   {i}. {r['expiry']} ${r['strike']:g} — 거래량 {r['volume']:,} "
            f"({_oi_str(r)}, {voi_s})"
        )
    return out


def format_data_summary(
    data: dict,
    base: dict,
    anomalies: list[dict],
    volume_anomaly: dict | None,
    *,
    narrative_source: str = "rule",
) -> list[str]:
    """V/OI·거래량·정확도 등 숫자 참고자료."""
    L: list[str] = []
    L.append("─" * 40)
    L.append("📋 데이터 요약")

    src = base.get("oi_source", "-")
    ai = {
        "openai": "ChatGPT",
        "stock": "주가중심",
        "rule": "규칙기반",
    }.get(narrative_source, narrative_source)
    L.append(f"   OI {src} | 해설:{ai} | 심리 {base.get('sentiment')} "
             f"(C/P {base.get('call_put_volume_ratio')})")

    L.append("   · V/OI 상위:")
    if base.get("top_voi"):
        for i, r in enumerate(base["top_voi"][:5], 1):
            L.append(
                f"     {i}. {r['expiry']} {r['type']} ${r['strike']:g} "
                f"V/OI {r['voi']} ({r['class']}) vol {r['volume']:,}"
            )
    else:
        L.append("     (없음)")

    L.append("   · 거래량 상위 콜:")
    L.extend(_volume_lines((base.get("top_call_volume") or [])[:5]))
    L.append("   · 거래량 상위 풋:")
    L.extend(_volume_lines((base.get("top_put_volume") or [])[:5]))

    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        L.append(
            f"   · 거래량 이상: 오늘 {int(volume_anomaly['today']):,} "
            f"(평균 대비 {volume_anomaly['mult']}배)"
        )
    if anomalies:
        L.append("   · OI 급변:")
        for a in anomalies[:6]:
            L.append(f"     - {a['message']}")

    ctx = data.get("learning_context") or {}
    s7 = ctx.get("최근7일") or {}
    if s7.get("available"):
        bits = []
        if s7.get("band_accuracy_pct") is not None:
            bits.append(f"밴드 {s7['band_accuracy_pct']}%")
        if s7.get("support_accuracy_pct") is not None:
            bits.append(f"지지 {s7['support_accuracy_pct']}%")
        if s7.get("direction_accuracy_pct") is not None:
            bits.append(f"방향 {s7['direction_accuracy_pct']}%")
        if bits:
            L.append(f"   · 최근7일 정확도: {', '.join(bits)} (n={s7.get('n')})")
    return L


def build_report(data: dict, base: dict, anomalies: list[dict],
                 volume_anomaly: dict | None, narrative: str,
                 narrative_source: str, eventinfo: dict | None = None) -> str:
    if narrative_source == "stock":
        return narrative

    L: list[str] = [narrative, ""]
    L.extend(
        format_data_summary(
            data, base, anomalies, volume_anomaly, narrative_source=narrative_source
        )
    )
    return "\n".join(L)
