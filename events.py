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
        return {"title": title, "publisher": publisher, "published": published, "link": link}
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
        "link": n.get("link"),
    }


# ------------------------------------------------------------------ #
# 3. 가격 신뢰성(이상 급변)
# ------------------------------------------------------------------ #

def price_sanity(spot: float, prev_close: float | None) -> dict:
    """전일 종가 대비 변동률과 이상치 여부."""
    if not prev_close:
        return {"change_pct": None, "abnormal": False, "note": None}
    change = round((spot - prev_close) / prev_close * 100, 2)
    abnormal = abs(change) >= config.PRICE_MOVE_ALERT_PCT
    note = None
    if abnormal:
        note = (
            f"전일 대비 {change:+.1f}% 로 변동이 큽니다 — 장중 급변/지연 호가일 수 있으니 "
            "실제 시세를 확인하세요."
        )
    return {"change_pct": change, "abnormal": abnormal, "note": note}


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
# 통합 수집
# ------------------------------------------------------------------ #

def collect_events(
    ticker: str,
    spot: float,
    prev_close: float | None,
    base: dict | None = None,
    prev: dict | None = None,
) -> dict:
    """어닝 + 뉴스 + 가격 + (선택) 옵션 반응을 한 번에 수집."""
    result = {
        "earnings": None,
        "news": [],
        "price": price_sanity(spot, prev_close),
        "options_reaction": None,
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
    return result
