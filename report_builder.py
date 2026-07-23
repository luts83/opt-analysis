"""리포트 조립.

구성:
  [본문] 일반인용 친근한 리포트 (ChatGPT 또는 규칙기반 폴백)
  [부록] 📋 데이터 요약 — 참고용 핵심 숫자 (콜/풋 거래량 분리, OI 없음 명시)
"""
from __future__ import annotations

_ROLE_LABEL = {"this_week": "이번주", "next_week": "다음주", "monthly": "월간"}


def _oi_str(entry: dict) -> str:
    """OI 표기. 0이면 '데이터없음', 보간값이면 '(전일)' 표시."""
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


def _events_appendix(eventinfo: dict | None) -> list[str]:
    if not eventinfo:
        return []
    out: list[str] = []
    earn = eventinfo.get("earnings")
    if earn:
        if earn.get("phase") in ("임박", "직후"):
            sur = earn.get("surprise_pct")
            sur_s = f", EPS surprise {sur:+.1f}%" if sur is not None else ""
            out.append(f"   · 실적발표: {earn['date']} ({earn['phase']}) ⚠{sur_s}")
        elif earn.get("date"):
            out.append(f"   · 다음 실적발표(예정): {earn['date']} (D-{earn.get('days_to')})")
    react = eventinfo.get("options_reaction") or {}
    if react.get("available") and react.get("highlights"):
        out.append("   · 어닝 옵션 반응:")
        for h in react["highlights"]:
            out.append(f"     - {h}")
    news = eventinfo.get("news") or []
    if news:
        out.append("   · 최신 뉴스:")
        for n in news[:5]:
            pub = f" ({n['publisher']})" if n.get("publisher") else ""
            out.append(f"     - {n['title']}{pub}")
    return out


def build_report(data: dict, base: dict, anomalies: list[dict],
                 volume_anomaly: dict | None, narrative: str,
                 narrative_source: str, eventinfo: dict | None = None) -> str:
    L: list[str] = []

    # ── 본문 (일반인용) ──
    L.append(narrative)
    L.append("")
    L.append("─" * 60)

    # ── 부록: 데이터 요약 ──
    L.append("📋 데이터 요약 (참고용)")
    src = base.get("oi_source", "-")
    ai = "ChatGPT" if narrative_source == "openai" else "규칙기반(폴백)"
    price = (eventinfo or {}).get("price") or {}
    chg = price.get("change_pct")
    chg_s = f" ({chg:+}%{' ⚠' if price.get('abnormal') else ''})" if chg is not None else ""
    L.append(f"   현재가 ${data['spot']}{chg_s} | 심리 {base['sentiment']} "
             f"(C/P {base['call_put_volume_ratio']}) | OI {src} | 해설:{ai}")

    # 만기별 밴드
    L.append("   · 만기별 예상 밴드:")
    for role in ("this_week", "next_week", "monthly"):
        em = base["expiry_metrics"].get(role)
        if not em:
            continue
        st = em.get("straddle")
        label = _ROLE_LABEL.get(role, role)
        if st:
            L.append(f"     - {label}({em['date']}): ${st['lower']}~${st['upper']} "
                     f"(±{st['band_pct']}%)")

    # V/OI 상위
    L.append("   · V/OI 상위:")
    if base["top_voi"]:
        for i, r in enumerate(base["top_voi"], 1):
            L.append(f"     {i}. {r['expiry']} {r['type']} ${r['strike']:g} — "
                     f"V/OI {r['voi']} ({r['class']}) [거래량 {r['volume']:,} / {_oi_str(r)}]")
    else:
        L.append("     (조건 충족 계약 없음 — OI 미갱신이면 갱신 후 표시됨)")

    # 거래량 상위 (콜/풋 분리)
    L.append("   · 거래량 상위 콜:")
    L.extend(_volume_lines(base.get("top_call_volume", [])))
    L.append("   · 거래량 상위 풋:")
    L.extend(_volume_lines(base.get("top_put_volume", [])))

    # 어제 대비 이상신호
    if volume_anomaly and volume_anomaly.get("is_anomaly"):
        L.append(f"   · 거래량 이상: 오늘 {int(volume_anomaly['today']):,} "
                 f"(평균 대비 {volume_anomaly['mult']}배)")
    if anomalies:
        L.append("   · OI 급변:")
        for a in anomalies[:6]:
            L.append(f"     - {a['message']}")

    # 이벤트/뉴스 부록
    L.extend(_events_appendix(eventinfo))

    return "\n".join(L)
