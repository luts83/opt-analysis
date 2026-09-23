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

    L.append("   · V/OI 상위 (활동성 지표 · 방향 신호 아님):")
    if base.get("top_voi"):
        for i, r in enumerate(base["top_voi"][:5], 1):
            oi = r.get("oi") or 0
            L.append(
                f"     {i}. {r['expiry']} {r['type']} ${r['strike']:g} "
                f"V/OI {r['voi']} ({r['class']}) vol {r['volume']:,} "
                f"/ OI {oi:,} — 기존 OI 대비 당일 거래 비중"
            )
    else:
        L.append("     (없음)")

    L.append("   · 거래량 상위 콜 [Volume: 당일]:")
    L.extend(_volume_lines((base.get("top_call_volume") or [])[:5]))
    L.append("   · 거래량 상위 풋 [Volume: 당일]:")
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

    def _acc_line(label: str, s: dict) -> None:
        if not s.get("available"):
            return
        n = s.get("n") or 0
        dh = s.get("direction_hits")
        dn = s.get("direction_n")
        bits = []
        if s.get("band_accuracy_pct") is not None:
            bits.append(f"밴드 {s['band_accuracy_pct']}%")
        if s.get("support_accuracy_pct") is not None:
            bits.append(f"지지 {s['support_accuracy_pct']}%")
        if s.get("direction_accuracy_pct") is not None and dh is not None and dn:
            bits.append(f"방향 {dh}/{dn} ({s['direction_accuracy_pct']}%)")
        elif s.get("direction_accuracy_pct") is not None:
            bits.append(f"방향 {s['direction_accuracy_pct']}%")
        if bits:
            caveat = ""
            if n < 15:
                caveat = " · 표본 작음 → 장기 정확도로 해석 금지"
            L.append(f"   · {label}: {', '.join(bits)} (n={n}){caveat}")

    _acc_line("최근7회", ctx.get("최근7일") or {})
    _acc_line("최근30회", ctx.get("최근30일") or {})
    _acc_line("최근60회", ctx.get("최근60일") or {})
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
