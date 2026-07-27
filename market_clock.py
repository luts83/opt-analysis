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
    """세션에 맞는 💰 주가 한 줄."""
    ms = data.get("market_session") or get_market_session()
    spot = data.get("spot")
    prev = data.get("previous_close")
    regular = data.get("regular_close")
    extended = data.get("extended_price")

    def _fmt(v) -> str:
        if v is None:
            return "-"
        try:
            return f"${float(v):g}"
        except (TypeError, ValueError):
            return str(v)

    if ms == "regular":
        return f"💰 지금 주가 (장중 실시간): {_fmt(spot)}"

    if ms == "premarket":
        prem = extended if extended is not None else spot
        if prev is not None:
            return f"💰 프리마켓: {_fmt(prem)} / 전일 종가: {_fmt(prev)}"
        return f"💰 프리마켓: {_fmt(prem)}"

    if ms == "afterhours":
        reg = regular if regular is not None else prev
        aft = extended if extended is not None else spot
        if reg is not None and aft is not None:
            return f"💰 정규장 종가: {_fmt(reg)} / 애프터마켓: {_fmt(aft)}"
        if reg is not None:
            return f"💰 정규장 종가: {_fmt(reg)}"
        return f"💰 애프터마켓: {_fmt(aft)}"

    # closed — 주말/야간: 마지막 확정 종가
    close = regular if regular is not None else (prev if prev is not None else spot)
    return f"💰 전일 종가: {_fmt(close)}"


def appendix_session_snip(data: dict) -> str:
    """데이터 요약용 짧은 세션/가격 꼬리표."""
    ms = data.get("market_session") or get_market_session()
    regular = data.get("regular_close")
    extended = data.get("extended_price")
    prev = data.get("previous_close")
    if ms == "regular":
        return f" | 장중 ${data.get('spot')}"
    if ms == "premarket" and extended is not None and prev is not None:
        return f" | 프리 ${extended}/전일 ${prev}"
    if ms == "afterhours" and regular is not None and extended is not None:
        return f" | 정규 ${regular}→애프터 ${extended}"
    if ms == "closed" and (regular is not None or prev is not None):
        return f" | 전일종가 ${regular if regular is not None else prev}"
    return ""


def apply_session_to_narrative(narrative: str, data: dict, eventinfo: dict | None = None) -> str:
    """LLM/폴백 본문의 💰·🔮 헤더를 세션에 맞게 교정."""
    import re

    text = narrative or ""
    price_line = format_price_line(data)
    text = re.sub(
        r"^💰[^\n]*(?:\n[ \t]+[^\n]*)*",
        price_line,
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
