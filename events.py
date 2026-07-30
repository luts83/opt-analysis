"""이벤트/뉴스 인식 + 어닝 전후 옵션 반응.

리포트가 '숫자'만 보고 어닝(실적 발표) 같은 대형 이벤트를 놓치는 문제를 막기 위해:
  1) 어닝 캘린더 — 실적 발표가 임박/직후면 경고 + EPS 서프라이즈(있으면).
  2) 뉴스 헤드라인 — 종목 최신 뉴스 제목(LLM 컨텍스트/리포트 첨부).
  3) 가격 신뢰성 — 전일 종가 대비 이상 급변이면 '확인 필요' 라벨.
  4) 옵션 반응 — 어닝 창이면 전일 스냅샷 대비 밴드/볼륨/심리 변화를 요약.

모든 함수는 네트워크/파싱 실패에도 조용히 빈 값을 돌려주도록 방어적으로 작성한다.
"""
from __future__ import annotations

import datetime as dt
import re

import config


# ------------------------------------------------------------------ #
# 1. 어닝(실적 발표) 캘린더
# ------------------------------------------------------------------ #

def get_earnings_flag(t, today: dt.date) -> dict | None:
    """가장 가까운 실적 발표일을 찾아 임박/직후 여부를 판정.

    반환: {"date","days_to","phase","message","eps_estimate","eps_reported","surprise_pct"}
      phase: "임박"(향후 window 내) | "직후"(과거 window 내) | "예정"(그 외 미래)
    """
    win = config.EARNINGS_WINDOW_DAYS
    rows = _earnings_rows(t)
    if not rows:
        return None

    nearest = min(rows, key=lambda r: abs((r["date"] - today).days))
    days_to = (nearest["date"] - today).days  # 음수=과거, 양수=미래
    surprise = nearest.get("surprise_pct")
    surprise_txt = ""
    if surprise is not None and days_to <= 0:
        direction = "상회" if surprise > 0 else "하회"
        surprise_txt = (
            f" EPS {nearest.get('eps_reported')} vs 예상 {nearest.get('eps_estimate')} "
            f"({surprise:+.1f}% {direction})."
        )

    if 0 <= days_to <= win:
        phase = "임박"
        msg = (
            f"⚠️ 실적 발표 임박: {nearest['date'].isoformat()} (D-{days_to}). "
            "발표 전후 변동성이 매우 큽니다 — 옵션 볼륨 기반 심리는 '포지션 준비'일 수 있어 "
            "단정하지 말고 신중히 해석하세요."
        )
    elif -win <= days_to < 0:
        phase = "직후"
        msg = (
            f"⚠️ 실적 발표 직후: {nearest['date'].isoformat()} ({-days_to}일 전 발표)."
            f"{surprise_txt} "
            "발표 결과로 주가·옵션이 급변했을 수 있습니다 — 옵션 반응·뉴스를 함께 보세요."
        )
    else:
        future = sorted((r for r in rows if r["date"] >= today), key=lambda r: r["date"])
        if not future:
            return None
        nxt = future[0]
        return {
            "date": nxt["date"].isoformat(),
            "days_to": (nxt["date"] - today).days,
            "phase": "예정",
            "message": None,
            "eps_estimate": nxt.get("eps_estimate"),
            "eps_reported": nxt.get("eps_reported"),
            "surprise_pct": nxt.get("surprise_pct"),
        }

    return {
        "date": nearest["date"].isoformat(),
        "days_to": days_to,
        "phase": phase,
        "message": msg,
        "eps_estimate": nearest.get("eps_estimate"),
        "eps_reported": nearest.get("eps_reported"),
        "surprise_pct": surprise,
    }


def _earnings_rows(t) -> list[dict]:
    """[{date, eps_estimate, eps_reported, surprise_pct}]"""
    out: list[dict] = []
    try:
        df = t.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                d = _to_date(idx)
                if not d:
                    continue
                est = _num(row.get("EPS Estimate"))
                rep = _num(row.get("Reported EPS"))
                sur = _num(row.get("Surprise(%)"))
                out.append(
                    {
                        "date": d,
                        "eps_estimate": est,
                        "eps_reported": rep,
                        "surprise_pct": sur,
                    }
                )
    except Exception:
        pass
    if out:
        # 같은 날짜 중복 제거(첫 값 유지)
        seen: set[dt.date] = set()
        uniq = []
        for r in sorted(out, key=lambda x: x["date"], reverse=True):
            if r["date"] in seen:
                continue
            seen.add(r["date"])
            uniq.append(r)
        return uniq

    # 폴백: calendar (미래 발표일만, EPS 없음)
    try:
        cal = t.calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed is not None:
            items = ed if isinstance(ed, (list, tuple)) else [ed]
            for e in items:
                d = _to_date(e)
                if d:
                    out.append(
                        {
                            "date": d,
                            "eps_estimate": None,
                            "eps_reported": None,
                            "surprise_pct": None,
                        }
                    )
    except Exception:
        pass
    return out


def _num(v) -> float | None:
    try:
        if v is None:
            return None
        # pandas NaN
        if v != v:  # noqa: PLR0124
            return None
        return round(float(v), 4)
    except Exception:
        return None


def _to_date(x) -> dt.date | None:
    try:
        if isinstance(x, dt.datetime):
            return x.date()
        if isinstance(x, dt.date):
            return x
        if hasattr(x, "to_pydatetime"):
            return x.to_pydatetime().date()
        if hasattr(x, "date") and callable(x.date):
            return x.date()
        return dt.date.fromisoformat(str(x)[:10])
    except Exception:
        return None


# ------------------------------------------------------------------ #
# 2. 뉴스 헤드라인
# ------------------------------------------------------------------ #

def get_news(t, limit: int | None = None) -> list[dict]:
    """최신 뉴스 헤드라인 [{title, publisher, published, link}] (실패 시 빈 리스트)."""
    limit = limit or config.NEWS_COUNT
    try:
        raw = t.news or []
    except Exception:
        return []

    items: list[dict] = []
    for n in raw:
        item = _parse_news_item(n)
        if item and item["title"]:
            items.append(item)
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return items[:limit]


def _parse_news_item(n: dict) -> dict | None:
    if not isinstance(n, dict):
        return None
    c = n.get("content") if isinstance(n.get("content"), dict) else None
    if c:
        title = c.get("title")
        publisher = (
            ((c.get("provider") or {}) if isinstance(c.get("provider"), dict) else {}).get(
                "displayName"
            )
        )
        published = c.get("pubDate") or c.get("displayTime")
        link = (
            ((c.get("canonicalUrl") or {}) if isinstance(c.get("canonicalUrl"), dict) else {}).get(
                "url"
            )
        )
        return {
            "title": title,
            "publisher": publisher,
            "published": published,
            "link": _clean_url(link or ""),
        }
    published = n.get("providerPublishTime")
    if isinstance(published, (int, float)):
        try:
            published = dt.datetime.fromtimestamp(published, dt.timezone.utc).isoformat()
        except Exception:
            published = None
    return {
        "title": n.get("title"),
        "publisher": n.get("publisher"),
        "published": published,
        "link": _clean_url(n.get("link") or ""),
    }


def format_news_lines(news: list[dict], *, limit: int = 3, indent: str = "") -> list[str]:
    """텔레그램용 plain URL (마크다운 링크 금지 — 파싱 깨짐 방지)."""
    lines: list[str] = []
    for n in (news or [])[:limit]:
        title = (n.get("title") or "").strip() or "(제목 없음)"
        # 마크다운/제어문자 제거
        title = re.sub(r"\[|\]|\(|\)", "", title)
        title = title.replace("\ufffc", "").strip()
        pub = f" ({n['publisher']})" if n.get("publisher") else ""
        link = _clean_url(n.get("link") or "")
        summary = (n.get("summary") or n.get("snippet") or "").strip()
        lines.append(f"{indent}- {title}{pub}")
        if summary:
            one = summary.replace("\n", " ").replace("\ufffc", "")
            if len(one) > 80:
                one = one[:77] + "..."
            lines.append(f"{indent}  · {one}")
        if link:
            lines.append(f"{indent}  {link}")
    return lines


def _clean_url(url: str) -> str:
    """URL 끝의 이상문자·마크다운 잔여물 제거."""
    if not url:
        return ""
    u = url.strip().replace("\ufffc", "").replace("\u200b", "")
    # [url](url) 형태면 안쪽만
    m = re.match(r"^\[([^\]]*)\]\((https?://[^)]+)\)\s*$", u)
    if m:
        u = m.group(2)
    # 뒤에 붙은 비ASCII/구두점 제거
    u = re.sub(r"[^\x21-\x7E]+$", "", u)
    u = u.rstrip(".,;)]}>\"'-_")
    # 경로 끝 '/-' 같은 잔여
    u = re.sub(r"/-+$", "/", u)
    if not u.startswith("http"):
        return ""
    return u


def with_linked_news(narrative: str, eventinfo: dict | None) -> str:
    """본문 📰 뉴스를 최대 3건·1회만 삽입 (중복 제거)."""
    import report_polish

    news = (eventinfo or {}).get("news") or []
    polished = report_polish.polish_narrative(narrative or "")
    if not news:
        return polished

    block = "📰 관련 뉴스\n" + "\n".join(format_news_lines(news, limit=3))
    return report_polish.inject_news_once(polished, block)


# ------------------------------------------------------------------ #
# 3. 가격 신뢰성(이상 급변)
# ------------------------------------------------------------------ #

def price_sanity(
    spot: float,
    prev_close: float | None,
    regular_close: float | None = None,
    extended_vs_regular_pct: float | None = None,
) -> dict:
    """전일 종가 대비 변동 + 정규장 vs 장외 괴리 주의."""
    change = None
    if prev_close:
        change = round((spot - prev_close) / prev_close * 100, 2)
    abnormal = bool(change is not None and abs(change) >= config.PRICE_MOVE_ALERT_PCT)
    notes: list[str] = []
    if abnormal and change is not None:
        notes.append(
            f"전일 대비 {change:+.1f}% 로 변동이 큽니다 — 실제 시세를 확인하세요."
        )
    if (
        extended_vs_regular_pct is not None
        and abs(extended_vs_regular_pct) >= 1.0
        and regular_close is not None
    ):
        direction = "하락" if extended_vs_regular_pct < 0 else "상승"
        import market_clock

        ms = market_clock.get_market_session()
        if ms == "premarket":
            notes.append(
                f"전일 종가 ${regular_close:g} 대비 프리마켓에서 {extended_vs_regular_pct:+.1f}% "
                f"{direction} — 리포트 기준가는 프리마켓 반영값입니다."
            )
        elif ms == "afterhours":
            notes.append(
                f"정규장 종가 ${regular_close:g} 대비 애프터마켓에서 {extended_vs_regular_pct:+.1f}% "
                f"{direction} — 리포트 기준가는 애프터마켓 반영값입니다."
            )
        else:
            notes.append(
                f"정규장 종가 ${regular_close:g} 대비 장외에서 {extended_vs_regular_pct:+.1f}% "
                f"{direction} — 리포트 기준가는 장외 반영값입니다."
            )
        abnormal = True
    return {
        "change_pct": change,
        "abnormal": abnormal,
        "note": " ".join(notes) if notes else None,
    }


# ------------------------------------------------------------------ #
# 4. 어닝 전후 옵션 반응 (전일 스냅샷 대비)
# ------------------------------------------------------------------ #

def options_reaction(base: dict, prev: dict | None, earnings: dict | None) -> dict | None:
    """어닝 창(임박/직후)일 때 옵션 시장 반응 요약.

    - 전일 스냅샷이 있으면: 밴드/볼륨/심리 변화 비교
    - 없어도: 오늘 스냅샷 기준으로 콜·풋 집중·밴드 폭을 요약
    """
    if not earnings or earnings.get("phase") not in ("임박", "직후"):
        return None

    today_em = (base.get("expiry_metrics") or {}).get("this_week") or {}
    today_st = today_em.get("straddle") or {}
    band_today = today_st.get("band_pct")
    vol_today = base.get("total_volume") or 0
    call_vol = base.get("total_call_volume") or 0
    put_vol = base.get("total_put_volume") or 0
    cpr_today = base.get("call_put_volume_ratio")
    senti_today = base.get("sentiment")

    top_calls = (base.get("top_call_volume") or [])[:3]
    top_puts = (base.get("top_put_volume") or [])[:3]
    call_focus = ", ".join(f"${r['strike']:g}({r['volume']:,})" for r in top_calls) or "-"
    put_focus = ", ".join(f"${r['strike']:g}({r['volume']:,})" for r in top_puts) or "-"

    snapshot_highlights = []
    if band_today is not None:
        snapshot_highlights.append(
            f"이번주 예상 변동폭(스트래들) ±{band_today}% "
            f"(${today_st.get('lower')}~${today_st.get('upper')})"
        )
    if vol_today:
        snapshot_highlights.append(
            f"옵션 거래량 합계 {vol_today:,}건 (콜 {call_vol:,} / 풋 {put_vol:,})"
        )
    snapshot_highlights.append(f"거래 집중 콜: {call_focus}")
    snapshot_highlights.append(f"거래 집중 풋: {put_focus}")
    if cpr_today is not None:
        snapshot_highlights.append(
            f"콜/풋 비율 {cpr_today} → '{senti_today}'처럼 보이지만 어닝 국면이라 단정 금지"
        )

    if not prev:
        return {
            "available": True,
            "compared_to_prev": False,
            "phase": earnings.get("phase"),
            "band_pct_today": band_today,
            "volume_today": vol_today,
            "sentiment_today": senti_today,
            "call_put_today": cpr_today,
            "highlights": snapshot_highlights,
            "note": (
                "전일 스냅샷이 없어 '변화율' 비교는 못 했지만, "
                "오늘 옵션 시장의 집중 구간은 위와 같습니다."
            ),
        }

    prev_m = prev.get("metrics") or {}
    prev_em = (prev_m.get("expiry_metrics") or {}).get("this_week") or {}
    prev_st = prev_em.get("straddle") or {}
    band_prev = prev_st.get("band_pct")

    change_highlights: list[str] = []
    band_delta = None
    if band_today is not None and band_prev is not None:
        band_delta = round(band_today - band_prev, 2)
        if band_delta >= 1.0:
            change_highlights.append(
                f"변동성 기대 확대 (+{band_delta}%p) — 시장이 더 큰 움직임을 가격에 반영"
            )
        elif band_delta <= -1.0:
            change_highlights.append(
                f"변동성 기대 축소 ({band_delta}%p) — 시장이 숨고르기 중"
            )
        else:
            change_highlights.append(f"변동성 기대 비슷 ({band_delta:+}%p)")

    vol_prev = prev_m.get("total_volume") or 0
    vol_mult = round(vol_today / vol_prev, 2) if vol_prev else None
    if vol_mult is not None:
        if vol_mult >= 1.5:
            change_highlights.append(f"옵션 거래량 급증 (전일 대비 {vol_mult}배)")
        elif vol_mult <= 0.7:
            change_highlights.append(f"옵션 거래량 감소 (전일 대비 {vol_mult}배)")
        else:
            change_highlights.append(f"옵션 거래량 비슷 (전일 대비 {vol_mult}배)")

    senti_prev = prev_m.get("sentiment")
    cpr_prev = prev_m.get("call_put_volume_ratio")
    if senti_today and senti_prev:
        if senti_today != senti_prev:
            change_highlights.append(
                f"심리 전환: {senti_prev} → {senti_today} (C/P {cpr_prev} → {cpr_today})"
            )
        else:
            change_highlights.append(
                f"심리 유지: {senti_today} (C/P {cpr_prev} → {cpr_today}) — 어닝 국면이라 단정 금지"
            )

    return {
        "available": True,
        "compared_to_prev": True,
        "phase": earnings.get("phase"),
        "band_pct_today": band_today,
        "band_pct_prev": band_prev,
        "band_delta_pp": band_delta,
        "volume_today": vol_today,
        "volume_prev": vol_prev,
        "volume_mult": vol_mult,
        "sentiment_today": senti_today,
        "sentiment_prev": senti_prev,
        "call_put_today": cpr_today,
        "call_put_prev": cpr_prev,
        "highlights": change_highlights + snapshot_highlights[:2],
        "note": (
            "어닝 전후엔 콜/풋 비율만으로 '강세/약세'를 단정하지 마세요. "
            "밴드(변동성 기대)와 거래량·거래 집중 구간이 더 중요합니다."
        ),
    }


# ------------------------------------------------------------------ #
# 5. 다음 장 개장 시나리오 (옵션 집중 구간 → 주가 해석)
# ------------------------------------------------------------------ #

def next_session_scenarios(
    base: dict,
    spot: float,
    data: dict | None = None,
    earnings: dict | None = None,
) -> dict | None:
    """옵션 거래 집중 + OI 강지지/저항 → 가능성 순 시나리오 3개 + 체크포인트."""
    import market_clock

    levels = base.get("levels") or {}
    st = ((base.get("expiry_metrics") or {}).get("this_week") or {}).get("straddle") or {}
    senti = base.get("sentiment") or "중립"
    change_pct = base.get("price_change_pct")
    if change_pct is None and data:
        prev_c = data.get("previous_close")
        if prev_c and spot:
            change_pct = round((spot - float(prev_c)) / float(prev_c) * 100, 2)
    max_dist = spot * 0.15  # 시나리오 1차 레벨: 현재가 ±15%

    strong_sup_list = levels.get("strong_support") or []
    near_sup_list = levels.get("near_support") or []
    strong_res_list = levels.get("strong_resistance") or []
    near_res_list = levels.get("near_resistance") or []

    strong_sup = strong_sup_list[0]["strike"] if strong_sup_list else None
    strong_res = strong_res_list[0]["strike"] if strong_res_list else None
    strong_sup_meta = strong_sup_list[0] if strong_sup_list else None
    strong_res_meta = strong_res_list[0] if strong_res_list else None

    support_cands: list[float] = []
    resist_cands: list[float] = []
    for item in near_sup_list + strong_sup_list:
        support_cands.append(float(item["strike"]))
    for item in near_res_list + strong_res_list:
        resist_cands.append(float(item["strike"]))
    for r in (base.get("top_put_volume") or [])[:8]:
        support_cands.append(float(r["strike"]))
    for r in (base.get("top_call_volume") or [])[:8]:
        resist_cands.append(float(r["strike"]))

    # 밴드 하단/상단도 시나리오 1차 레벨로 포함 (현재가에 더 가까울 수 있음)
    if st.get("lower") is not None:
        support_cands.append(float(st["lower"]))
    if st.get("upper") is not None:
        resist_cands.append(float(st["upper"]))

    supports_below = sorted(
        {s for s in support_cands if s < spot * 0.995 and (spot - s) <= max_dist},
        reverse=True,
    )
    resists_above = sorted(
        {s for s in resist_cands if s > spot * 1.005 and (s - spot) <= max_dist}
    )
    # 후보가 비면 근처 레벨을 합성해 시나리오 3개를 항상 채운다
    if not supports_below:
        supports_below = sorted({s for s in support_cands if s < spot * 0.995}, reverse=True)[:2]
    if not resists_above:
        resists_above = sorted({s for s in resist_cands if s > spot * 1.005})[:2]
    if not supports_below:
        supports_below = [round(spot * 0.95, 2), round(spot * 0.90, 2)]
    if len(supports_below) < 2:
        supports_below = list(supports_below) + [round(supports_below[0] * 0.93, 2)]
    if not resists_above:
        resists_above = [round(spot * 1.05, 2)]

    # 1차 = 현재가에 가장 가까운 아래/위 (멀면 안 씀)
    primary_support = supports_below[0]
    next_support = supports_below[1] if len(supports_below) > 1 else round(primary_support * 0.93, 2)
    primary_resist = resists_above[0]
    secondary_resist = resists_above[1] if len(resists_above) > 1 else None
    reclaim = None
    if strong_sup is not None and strong_sup > spot * 1.005 and (strong_sup - spot) <= max_dist:
        reclaim = strong_sup

    # 근거 텍스트용 메타
    def _why_level(strike: float, side: str) -> str:
        for item in near_sup_list + strong_sup_list:
            if abs(float(item["strike"]) - strike) < 0.02:
                oi = item.get("oi")
                return f"풋 OI {oi:,}개 밀집" if oi else "풋 지지 후보"
        for item in near_res_list + strong_res_list:
            if abs(float(item["strike"]) - strike) < 0.02:
                oi = item.get("oi")
                vol = item.get("volume")
                if oi:
                    return f"콜 OI {oi:,}개 밀집"
                if vol:
                    return f"콜 거래 {vol:,}계약 집중"
                return "콜 저항 후보"
        if st.get("lower") is not None and abs(float(st["lower"]) - strike) < 0.6:
            return "이번주 옵션 밴드 하단"
        if st.get("upper") is not None and abs(float(st["upper"]) - strike) < 0.6:
            return "이번주 옵션 밴드 상단"
        return "옵션 거래·OI 후보" if side else "레벨"

    regular = (data or {}).get("regular_close")
    extended = (data or {}).get("extended_price")
    gap_pct = (data or {}).get("extended_vs_regular_pct")
    market_session = (data or {}).get("market_session") or market_clock.get_market_session()
    when = market_clock.scenario_when_phrase(market_session)
    section_title = "🔮 시나리오 (가능성 순)"

    gap_note = None
    if (
        market_session in ("premarket", "afterhours")
        and gap_pct is not None
        and abs(gap_pct) >= 1.0
        and regular
        and extended
    ):
        direction = "갭다운" if gap_pct < 0 else "갭업"
        if market_session == "premarket":
            gap_note = (
                f"전일 종가 ${regular:g} → 프리마켓 ${extended:g} ({gap_pct:+.1f}%, {direction})."
            )
        else:
            gap_note = (
                f"정규장 종가 ${regular:g} → 애프터마켓 ${extended:g} ({gap_pct:+.1f}%, {direction})."
            )

    bearish = senti in ("약세", "반등 시도 국면", "양방향 극단 베팅", "차익실현/헤지 국면") or (
        change_pct is not None and change_pct <= -5
    )
    bullish = senti == "강세" and not bearish

    reclaim_bit = f" → ${reclaim:g} 탈환(옛 지지→저항)" if reclaim else ""
    why_ps = _why_level(primary_support, "sup")
    why_ns = _why_level(next_support, "sup")
    why_pr = _why_level(primary_resist, "res")

    candidates: list[dict] = [
        {
            "name": "하락 지속",
            "condition": (
                f"{when} ${primary_support:g} 하회 시 → ${next_support:g} 도전"
            ),
            "watch": f"${primary_support:g}({why_ps}) 이탈이면 하락 가속.",
            "evidence": f"다음 방어 ${next_support:g} ({why_ns})",
            "rank_bias": 4 if bearish else (2 if senti == "중립" else 1),
        },
        {
            "name": "반등 시도",
            "condition": f"{when} ${primary_resist:g} 돌파{reclaim_bit}",
            "watch": (
                f"숏커버·반등 가능."
                + (f" 다음 저항 ${secondary_resist:g}." if secondary_resist else "")
            ),
            "evidence": f"${primary_resist:g} ({why_pr})",
            "rank_bias": 3 if bullish else (2 if senti in ("반등 시도 국면", "중립") else 1),
        },
        {
            "name": "횡보",
            "condition": f"{when} ${primary_support:g}~${primary_resist:g} 박스권",
            "watch": "돌파/이탈 전까진 방향 단정하지 않기.",
            "evidence": "가까운 지지·저항 사이 소화 구간",
            "rank_bias": 3 if senti == "중립" and not bearish else 2,
        },
    ]

    if gap_pct is not None and abs(gap_pct) >= 1.5:
        for c in candidates:
            if gap_pct < 0 and c["name"] == "하락 지속":
                c["rank_bias"] += 2
            elif gap_pct > 0 and c["name"] == "반등 시도":
                c["rank_bias"] += 2
    if change_pct is not None and change_pct <= -5:
        for c in candidates:
            if c["name"] == "하락 지속":
                c["rank_bias"] += 2

    candidates.sort(key=lambda x: -x["rank_bias"])
    scenarios = []
    for i, c in enumerate(candidates[:3], 1):
        tag = " (가장 유력)" if i == 1 else ""
        scenarios.append(
            {
                "name": f"{i}. {c['name']}{tag}",
                "condition": c["condition"],
                "watch": c["watch"],
                "evidence": c.get("evidence"),
            }
        )

    context = None
    if earnings and earnings.get("phase") == "직후":
        sur = earnings.get("surprise_pct")
        sur_s = f" (EPS {sur:+.1f}%)" if sur is not None else ""
        context = f"실적 발표 직후{sur_s}. 돌파/이탈 확인용으로만 쓰세요."
    elif earnings and earnings.get("phase") == "임박":
        context = "실적 발표 임박. 방향 단정 대신 변동성·레벨 반응에 주목."

    # 체크포인트: 현재가에 가까운 것부터 + 근거
    checks: list[dict] = [
        {
            "text": f"${primary_support:g} 이탈 여부 — 하락 가속 첫 신호",
            "why": why_ps,
        },
        {
            "text": f"${primary_resist:g} 돌파 여부 — 반등 확인 신호",
            "why": why_pr,
        },
    ]
    if next_support and next_support != primary_support:
        checks.append(
            {
                "text": f"${next_support:g} 도달 시 — 다음 방어/반등 관심",
                "why": why_ns,
            }
        )
    if reclaim and reclaim not in (primary_support, primary_resist):
        checks.append(
            {
                "text": f"${reclaim:g} 탈환 여부 — 옛 지지 되찾기",
                "why": "이미 뚫린 지지 → 지금은 저항 역할",
            }
        )

    action_hint = " / ".join(c["text"] for c in checks)

    return {
        "reference_spot": spot,
        "session": (data or {}).get("session") or "regular",
        "market_session": market_session,
        "section_title": section_title,
        "when_phrase": when,
        "gap_note": gap_note,
        "nearest_support": primary_support,
        "nearest_resistance": primary_resist,
        "strong_support": strong_sup,
        "strong_resistance": strong_res,
        "strong_support_meta": strong_sup_meta,
        "strong_resistance_meta": strong_res_meta,
        "reclaim_level": reclaim,
        "band": [st.get("lower"), st.get("upper")] if st else None,
        "band_pct": st.get("band_pct"),
        "context": context,
        "scenarios": scenarios,
        "checkpoints": checks,
        "action_hint": action_hint,
    }



# ------------------------------------------------------------------ #
# 통합 수집
# ------------------------------------------------------------------ #

def collect_events(
    ticker: str,
    spot: float,
    prev_close: float | None,
    base: dict | None = None,
    prev: dict | None = None,
    data: dict | None = None,
) -> dict:
    """어닝 + 뉴스 + 가격 + 옵션 반응 + 다음장 시나리오를 한 번에 수집."""
    result = {
        "earnings": None,
        "news": [],
        "price": price_sanity(
            spot,
            prev_close,
            regular_close=(data or {}).get("regular_close"),
            extended_vs_regular_pct=(data or {}).get("extended_vs_regular_pct"),
        ),
        "options_reaction": None,
        "next_session": None,
    }
    if not config.EVENTS_ENABLED:
        return result
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        result["earnings"] = get_earnings_flag(t, dt.date.today())
        result["news"] = get_news(t)
    except Exception as e:  # noqa: BLE001
        print(f"[events] {ticker} 이벤트 수집 실패(무시): {e}")

    if base is not None:
        result["options_reaction"] = options_reaction(base, prev, result["earnings"])
        result["next_session"] = next_session_scenarios(
            base, spot, data=data, earnings=result["earnings"]
        )
    return result
