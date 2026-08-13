"""미국 주식 정규장 시계 (US/Eastern).

세션:
  premarket  04:00 ~ 09:30 ET  — 장 전 프리마켓
  regular    09:30 ~ 16:00 ET  — 정규장(장중)
  afterhours 16:00 ~ 20:00 ET  — 애프터마켓
  closed     그 외 + 주말      — 장 마감/휴장
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# 분 단위 (시*60 + 분)
_PRE_OPEN = 4 * 60          # 04:00
_REGULAR_OPEN = 9 * 60 + 30  # 09:30
_REGULAR_CLOSE = 16 * 60     # 16:00
_AFTER_CLOSE = 20 * 60       # 20:00


def now_et(when: dt.datetime | None = None) -> dt.datetime:
    if when is None:
        return dt.datetime.now(_ET)
    if when.tzinfo is None:
        return when.replace(tzinfo=dt.timezone.utc).astimezone(_ET)
    return when.astimezone(_ET)


def get_market_session(when: dt.datetime | None = None) -> str:
    """premarket | regular | afterhours | closed."""
    et = now_et(when)
    if et.weekday() >= 5:  # Sat/Sun
        return "closed"
    mins = et.hour * 60 + et.minute
    if _PRE_OPEN <= mins < _REGULAR_OPEN:
        return "premarket"
    if _REGULAR_OPEN <= mins < _REGULAR_CLOSE:
        return "regular"
    if _REGULAR_CLOSE <= mins < _AFTER_CLOSE:
        return "afterhours"
    return "closed"


def session_label_ko(market_session: str) -> str:
    return {
        "premarket": "프리마켓",
        "regular": "정규장(장중)",
        "afterhours": "애프터마켓",
        "closed": "장 마감",
    }.get(market_session, market_session)


def scenario_section_title(market_session: str) -> str:
    if market_session == "regular":
        return "🔮 남은 장중 시나리오"
    return "🔮 다음 장 개장 시나리오"


def scenario_when_phrase(market_session: str) -> str:
    """시나리오 조건에 쓰는 시점 표현."""
    if market_session == "regular":
        return "장중"
    return "개장 후"


def action_hint_prefix(market_session: str) -> str:
    if market_session == "regular":
        return "앞으로 남은 장중"
    return "개장 직후 첫 30분"


def format_price_line(data: dict) -> str:
    """세션별 💰 가격 블록 — 날짜·절대금액·변동폭을 함께 표시."""
    import datetime as _dt

    ms = data.get("market_session") or get_market_session()
    spot = data.get("spot")
    prev = data.get("previous_close")
    regular = data.get("regular_close")
    pre = data.get("pre_market_price")
    report_date = data.get("date")

    def _fmt(v) -> str:
        if v is None:
            return "-"
        try:
            return f"${float(v):.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(v)

    def _pct(a, b) -> str | None:
        try:
            if a is None or b is None or float(b) == 0:
                return None
            return f"{(float(a) - float(b)) / float(b) * 100:+.2f}%"
        except Exception:
            return None

    def _date_label(iso: str | None, delta_days: int = 0) -> str:
        if not iso:
            return ""
        try:
            d = _dt.date.fromisoformat(iso)
            if delta_days:
                d -= _dt.timedelta(days=1)
                while d.weekday() >= 5:
                    d -= _dt.timedelta(days=1)
            dow = "월화수목금토일"[d.weekday()]
            return f"{d.month}/{d.day}({dow})"
        except Exception:
            return ""

    close_date = _date_label(report_date)
    prev_date = _date_label(report_date, delta_days=1)

    if ms == "closed":
        reg_label = f"{close_date} 종가" if close_date else "종가"
        reg_price = regular if regular is not None else spot
        day_base = prev
    elif ms == "regular":
        reg_label = "장중 실시간가"
        reg_price = regular if regular is not None else spot
        day_base = prev
    else:
        reg_label = f"{close_date} 종가" if close_date else "정규장 종가"
        reg_price = regular if regular is not None else spot
        day_base = prev

    lines: list[str] = ["💰 가격"]
    # 전일 종가를 먼저 보여 비교 가능하게
    if day_base is not None and prev_date:
        lines.append(f"- {prev_date} 종가: {_fmt(day_base)}")

    day_pct = _pct(reg_price, day_base) if day_base is not None else None
    if day_pct and day_base is not None:
        try:
            delta = float(reg_price) - float(day_base)
            delta_s = f"{delta:+.2f}".rstrip("0").rstrip(".")
            lines.append(f"- {reg_label}: {_fmt(reg_price)} (${delta_s}, {day_pct})")
        except Exception:
            lines.append(f"- {reg_label}: {_fmt(reg_price)} ({day_pct})")
    else:
        lines.append(f"- {reg_label}: {_fmt(reg_price)}")

    if pre is not None:
        pp = _pct(pre, reg_price)
        today_label = ""
        try:
            from zoneinfo import ZoneInfo

            td = _dt.datetime.now(ZoneInfo("America/New_York")).date()
            dow = "월화수목금토일"[td.weekday()]
            today_label = f"{td.month}/{td.day}({dow}) "
        except Exception:
            pass
        if pp:
            lines.append(f"- {today_label}프리마켓: {_fmt(pre)} ({pp} vs 종가)")
        else:
            lines.append(f"- {today_label}프리마켓: {_fmt(pre)}")

    ext = data.get("extended_price")
    after = data.get("after_market_price")
    ext_show = after if after is not None else ext
    if ext_show is not None and ms in ("afterhours", "premarket", "closed"):
        ep = _pct(ext_show, reg_price)
        label = "애프터마켓" if ms == "afterhours" else "시간외"
        if ms == "premarket" and pre is not None:
            pass  # 프리마켓은 위에서 표시
        elif ep:
            lines.append(f"- {label}: {_fmt(ext_show)} ({ep} vs 종가)")
        else:
            lines.append(f"- {label}: {_fmt(ext_show)}")

    return "\n".join(lines)


def appendix_session_snip(data: dict) -> str:
    """데이터 요약용 짧은 세션/가격 꼬리표(애프터/프리 포함)."""
    ms = data.get("market_session") or get_market_session()
    regular = data.get("regular_close")
    prev = data.get("previous_close")
    after = data.get("after_market_price")
    pre = data.get("pre_market_price")

    base = regular if regular is not None else prev if prev is not None else data.get("spot")
    parts: list[str] = []
    if base is not None:
        label = "장중" if ms == "regular" else "정규"
        parts.append(f"{label} ${float(base):g}")
    if after is not None and base is not None:
        try:
            pct = (float(after) - float(base)) / float(base) * 100
            parts.append(f"애프터 ${float(after):g} ({pct:+.2f}%)")
        except Exception:
            parts.append(f"애프터 ${float(after):g}")
    if pre is not None and base is not None:
        try:
            pct = (float(pre) - float(base)) / float(base) * 100
            parts.append(f"프리 ${float(pre):g} ({pct:+.2f}%)")
        except Exception:
            parts.append(f"프리 ${float(pre):g}")

    return f" | " + " / ".join(parts) if parts else ""


def apply_session_to_narrative(narrative: str, data: dict, eventinfo: dict | None = None) -> str:
    """LLM/폴백 본문의 💰·🔮 헤더를 세션에 맞게 교정."""
    import re

    text = narrative or ""
    price_line = format_price_line(data)
    # 첫 번째 💰 섹션을 다음 섹션 시작 전까지 통째로 교체
    text = re.sub(
        r"^💰[\s\S]*?(?=^🚨|^🌡️|^🟢|^🔴|^📈|^🔮)",
        price_line + "\n\n",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    ms = data.get("market_session") or get_market_session()
    nxt = (eventinfo or {}).get("next_session") or {}
    title = nxt.get("section_title") or scenario_section_title(ms)
    text = re.sub(r"^🔮[^\n]*", title, text, count=1, flags=re.MULTILINE)

    if ms == "regular":
        text = text.replace("개장 후 ", "장중 ")
        text = text.replace("개장 직후 첫 30분", "앞으로 남은 장중")

    return text
