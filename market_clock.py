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
    """세션별 💰 가격 블록 — 기준(전일/종가)을 명시."""
    ms = data.get("market_session") or get_market_session()
    spot = data.get("spot")
    prev = data.get("previous_close")
    regular = data.get("regular_close")
    after = data.get("after_market_price")
    pre = data.get("pre_market_price")

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

    if ms == "closed":
        # regular_close = history[-1] = 가장 최근 거래일 종가 (진짜 "전일 종가")
        # prev = history[-2] = 전전 거래일 (변동률 계산용)
        reg_label = "전일 종가"
        reg_price = regular if regular is not None else spot
        day_base = prev
    elif ms == "regular":
        reg_label = "장중 실시간가"
        reg_price = regular if regular is not None else spot
        day_base = prev
    else:
        reg_label = "정규장 종가"
        reg_price = regular if regular is not None else spot
        day_base = prev

    lines: list[str] = ["💰 가격"]
    day_pct = _pct(reg_price, day_base) if day_base is not None else None
    if day_pct:
        lines.append(f"- {reg_label}: {_fmt(reg_price)} (전일 대비 {day_pct})")
    else:
        lines.append(f"- {reg_label}: {_fmt(reg_price)}")

    # 프리마켓만 (오늘 ET에 데이터가 있을 때). 애프터마켓은 리포트 시점에 무의미하므로 미표시.
    if pre is not None:
        pp = _pct(pre, reg_price)
        if pp:
            lines.append(f"- 오늘 프리마켓: {_fmt(pre)} ({pp} vs 종가)")
        else:
            lines.append(f"- 오늘 프리마켓: {_fmt(pre)}")

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
