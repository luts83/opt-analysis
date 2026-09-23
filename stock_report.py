"""주가 중심 타임라인 리포트 (목업 / 파일럿).

옵션은 센서, 주가가 본체. 장중 5분봉 + EOD 옵션 스냅샷으로
'시간대별 주가 움직임 + 옵션 각주 1줄' 목업을 만든다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

_ET = ZoneInfo("America/New_York")

# 장중 3막 (ET 분, inclusive start / exclusive end style for filter)
THREE_ACTS: tuple[tuple[str, int, int], ...] = (
    ("장 초반", 9 * 60 + 30, 11 * 60),
    ("장 중반", 11 * 60, 14 * 60),
    ("장 마감", 14 * 60, 16 * 60),
)


@dataclass
class Episode:
    title: str
    start: dt.datetime
    end: dt.datetime
    open: float
    high: float
    low: float
    close: float
    move_pct: float
    label: str  # up | down | flat
    intra_high_time: dt.datetime
    intra_low_time: dt.datetime


def _fmt_px(v: float) -> str:
    return f"${v:.2f}".rstrip("0").rstrip(".")


def _fmt_time(t: dt.datetime) -> str:
    return t.astimezone(_ET).strftime("%H:%M")


def _fmt_range(start: dt.datetime, end: dt.datetime) -> str:
    return f"{_fmt_time(start)}–{_fmt_time(end)}"


def fetch_5m_bars(ticker: str, date: str) -> pd.DataFrame:
    d = dt.date.fromisoformat(date)
    df = yf.Ticker(ticker).history(
        start=d.isoformat(),
        end=(d + dt.timedelta(days=1)).isoformat(),
        interval="5m",
        prepost=False,
    )
    if df.empty:
        return df
    df = df.tz_convert(_ET)
    mins = df.index.hour * 60 + df.index.minute
    mask = (mins >= 9 * 60 + 30) & (mins < 16 * 60)
    return df.loc[mask].copy()


def _mins_of(idx) -> int:
    return idx.hour * 60 + idx.minute


def _episode_from_slice(df: pd.DataFrame, title: str) -> Episode | None:
    if df.empty:
        return None
    o = float(df["Open"].iloc[0])
    c = float(df["Close"].iloc[-1])
    h = float(df["High"].max())
    l = float(df["Low"].min())
    move = (c - o) / o * 100 if o else 0.0
    if move > 0.2:
        lab = "up"
    elif move < -0.2:
        lab = "down"
    else:
        lab = "flat"
    hi_idx = df["High"].idxmax()
    lo_idx = df["Low"].idxmin()
    return Episode(
        title=title,
        start=df.index[0].to_pydatetime(),
        end=df.index[-1].to_pydatetime(),
        open=o,
        high=h,
        low=l,
        close=c,
        move_pct=round(move, 2),
        label=lab,
        intra_high_time=hi_idx.to_pydatetime().astimezone(_ET),
        intra_low_time=lo_idx.to_pydatetime().astimezone(_ET),
    )


def segment_three_acts(df: pd.DataFrame) -> list[Episode]:
    """09:30–11 / 11–14 / 14–16 ET 고정 3막."""
    out: list[Episode] = []
    for title, t0, t1 in THREE_ACTS:
        mins = df.index.map(_mins_of)
        chunk = df.loc[(mins >= t0) & (mins < t1)]
        ep = _episode_from_slice(chunk, title)
        if ep:
            out.append(ep)
    return out


def segment_fine(df: pd.DataFrame, window_bars: int = 6, max_n: int = 6) -> list[Episode]:
    """30분 창 — 시가·종가 포함, 움직임 큰 구간 (비교용)."""
    if df.empty or len(df) < window_bars:
        return []
    windows: list[Episode] = []
    for i in range(0, len(df) - window_bars + 1, window_bars):
        chunk = df.iloc[i : i + window_bars]
        t0 = chunk.index[0].to_pydatetime()
        t1 = chunk.index[-1].to_pydatetime()
        ep = _episode_from_slice(chunk, _fmt_range(t0, t1))
        if ep:
            windows.append(ep)
    if not windows:
        return []
    must = {0, len(windows) - 1}
    sig = [w for w in windows if abs(w.move_pct) >= 0.35 or windows.index(w) in must]
    if len(sig) < 3:
        for w in sorted(windows, key=lambda x: abs(x.move_pct), reverse=True):
            if w not in sig:
                sig.append(w)
            if len(sig) >= max_n:
                break
    sig = sorted({id(w): w for w in sig}.values(), key=lambda w: w.start)[:max_n]
    return sig


def _load_snapshot(ticker: str, date: str) -> dict | None:
    p = Path("snapshots") / ticker / f"{date}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class OptionRef:
    strike: float
    expiry: str
    role: str | None = None
    opt_type: str = "CALL"


def _expiry_timing(expiry: str, report_date: str, role: str | None = None) -> tuple[str, str]:
    """(만기 라벨, 시간축 설명)."""
    ed = dt.date.fromisoformat(expiry)
    rd = dt.date.fromisoformat(report_date)
    dte = (ed - rd).days
    if dte <= 0:
        return "당일 만기", "오늘 안"
    if dte == 1:
        return "내일 만기", "1~2일"
    if dte <= 7 or role in ("this_week", "zero_dte"):
        return f"이번주({ed.month}/{ed.day})", "며칠 안"
    if dte <= 14 or role == "next_week":
        return f"다음주({ed.month}/{ed.day})", "1~2주"
    return f"월간({ed.month}/{ed.day})", "수주 뒤"


def _row_to_ref(row: dict) -> OptionRef | None:
    try:
        return OptionRef(
            strike=float(row["strike"]),
            expiry=str(row["expiry"]),
            role=row.get("role"),
            opt_type=str(row.get("type") or "CALL").upper(),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _top_option_refs(snap: dict | None, n: int = 3, opt_type: str = "CALL") -> list[OptionRef]:
    if not snap:
        return []
    key = "top_call_volume" if opt_type.upper() == "CALL" else "top_put_volume"
    rows = (snap.get("metrics") or {}).get(key) or []
    out: list[OptionRef] = []
    for row in rows[:n]:
        ref = _row_to_ref(row)
        if ref:
            out.append(ref)
    return out


def _lookup_expiry(snap: dict | None, strike: float, opt_type: str | None = None) -> OptionRef | None:
    if not snap:
        return None
    m = snap.get("metrics") or {}
    for key in ("top_call_volume", "top_put_volume"):
        for row in m.get(key) or []:
            try:
                if abs(float(row["strike"]) - strike) > 0.02:
                    continue
                t = str(row.get("type") or "CALL").upper()
                if opt_type and t != opt_type.upper():
                    continue
                ref = _row_to_ref(row)
                if ref:
                    return ref
            except (TypeError, ValueError, KeyError):
                continue
    return None


def _fmt_opt_ref(ref: OptionRef, report_date: str) -> str:
    lab, _ = _expiry_timing(ref.expiry, report_date, ref.role)
    kind = "콜" if ref.opt_type == "CALL" else "풋"
    return f"{lab} {_fmt_px(ref.strike)} {kind}"


def _fmt_opt_refs(refs: list[OptionRef], report_date: str, n: int = 2) -> str:
    return ", ".join(_fmt_opt_ref(r, report_date) for r in refs[:n])


def _horizon_note(refs: list[OptionRef], report_date: str) -> str | None:
    if not refs:
        return None
    timings = {_expiry_timing(r.expiry, report_date, r.role)[1] for r in refs}
    if "수주 뒤" in timings and ("며칠 안" in timings or "1~2일" in timings):
        return "※ 만기 섞임 — 이번주=며칠 안, 월간=수주 뒤 (같은 가격이라도 시계가 다름)"
    if all(t == "수주 뒤" for t in timings):
        return "※ 월간 만기 위주 — 당장 주가보다 수주 뒤 시나리오"
    if all(t in ("며칠 안", "1~2일", "오늘 안") for t in timings):
        return "※ 단기(이번주·내일) 만기 위주 — 며칠 안 움직임과 연결"
    return None


def _near_strikes(snap: dict | None, spot: float, report_date: str) -> list[OptionRef]:
    if not snap:
        return []
    m = snap.get("metrics") or {}
    out: list[OptionRef] = []
    for it in (m.get("levels") or {}).get("interest_all") or []:
        try:
            s = float(it["strike"])
            if abs(s - spot) / spot > 0.12:
                continue
            t = str(it.get("type") or "CALL").upper()
            ref = _lookup_expiry(snap, s, t) or OptionRef(
                strike=s, expiry=report_date, role=None, opt_type=t
            )
            out.append(ref)
        except (TypeError, ValueError, KeyError):
            pass
    seen: set[tuple[float, str]] = set()
    uniq: list[OptionRef] = []
    for r in sorted(out, key=lambda x: abs(x.strike - spot)):
        key = (round(r.strike, 2), r.opt_type)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq[:3]


def _top_call_strikes(snap: dict | None, n: int = 2) -> list[float]:
    """하위 호환 — strike만."""
    return [r.strike for r in _top_option_refs(snap, n)]


def _day_summary(df: pd.DataFrame) -> dict:
    o = float(df["Open"].iloc[0])
    c = float(df["Close"].iloc[-1])
    h = float(df["High"].max())
    l = float(df["Low"].min())
    hi_idx = df["High"].idxmax()
    lo_idx = df["Low"].idxmin()
    return {
        "open": o,
        "close": c,
        "high": h,
        "low": l,
        "chg_pct": round((c - o) / o * 100, 2) if o else 0,
        "high_time": hi_idx.to_pydatetime().astimezone(_ET),
        "low_time": lo_idx.to_pydatetime().astimezone(_ET),
    }


def _narrate_fine(ep: Episode) -> str:
    sign = f"{ep.move_pct:+.1f}%"
    base = f"{_fmt_px(ep.open)}→{_fmt_px(ep.close)} ({sign})"
    if ep.label == "up" and ep.high > ep.open * 1.003:
        tail = "되돌림" if ep.close < ep.high * 0.995 else "유지"
        return (
            f"{base} — {_fmt_time(ep.intra_high_time)} "
            f"{_fmt_px(ep.high)} 고점 후 {tail}"
        )
    if ep.label == "down" and abs(ep.move_pct) >= 0.8:
        return (
            f"{base} — 하락 가속, 저 {_fmt_time(ep.intra_low_time)} "
            f"{_fmt_px(ep.low)}"
        )
    if ep.label == "down":
        return f"{base} — 완만한 매도, 범위 {_fmt_px(ep.low)}~{_fmt_px(ep.high)}"
    return f"{base} — 변동 축소"


def _narrate_stock(ep: Episode, day: dict, *, fine: bool = False) -> str:
    if fine:
        return _narrate_fine(ep)
    sign = f"{ep.move_pct:+.1f}%"
    base = f"{_fmt_px(ep.open)}→{_fmt_px(ep.close)} ({sign})"

    if ep.title == "장 초반":
        if ep.label == "up" and ep.high > ep.open * 1.005:
            return (
                f"{base} — 시가에서 {_fmt_time(ep.intra_high_time)} "
                f"{_fmt_px(ep.high)}까지 올랐다가 "
                + ("마감 쪽으로 되돌림" if ep.close < ep.high * 0.995 else "강세 유지")
            )
        if ep.label == "down":
            return (
                f"{base} — 개장 직후부터 매도 우세, "
                f"구간 저 {_fmt_px(ep.low)} ({_fmt_time(ep.intra_low_time)})"
            )
        return f"{base} — 방향성 약한 출발, {_fmt_px(ep.low)}~{_fmt_px(ep.high)} 박스"

    if ep.title == "장 중반":
        if ep.label == "down" and abs(ep.move_pct) >= 1.5:
            return (
                f"{base} — 점심 전후 급락 구간, "
                f"저점 {_fmt_time(ep.intra_low_time)} {_fmt_px(ep.low)}"
            )
        if ep.label == "up":
            return (
                f"{base} — 중반 반등, 고점 {_fmt_time(ep.intra_high_time)} "
                f"{_fmt_px(ep.high)}"
            )
        if day["low_time"].hour >= 11 and day["low_time"].hour < 14:
            return (
                f"{base} — 장중 저점 {_fmt_time(day['low_time'])} "
                f"{_fmt_px(day['low'])} 형성 구간"
            )
        return f"{base} — 횡보·조정, 범위 {_fmt_px(ep.low)}~{_fmt_px(ep.high)}"

    # 장 마감 or fine-grained title
    if ep.label == "flat" or abs(ep.move_pct) < 0.5:
        return f"{base} — 바닥/천장권 횡보로 마감, 큰 반등 없음"
    if ep.label == "down":
        return f"{base} — 마감까지 매도 잔존, 종가 {_fmt_px(ep.close)}"
    return f"{base} — 마감 전 매수 유입, 종가 {_fmt_px(ep.close)}"


def _option_footnote(
    ep: Episode,
    prev_calls: list[OptionRef],
    near: list[OptionRef],
    day_high: float,
    report_date: str,
    prev_as_of: str,
    *,
    act_index: int = 0,
    fine: bool = False,
) -> str:
    """옵션 각주 — strike + 만기."""
    if prev_calls and day_high < min(r.strike for r in prev_calls) * 0.995:
        if act_index > 0:
            return "—"
        gap = (prev_calls[0].strike - day_high) / prev_calls[0].strike * 100
        focus = _fmt_opt_refs(prev_calls, prev_as_of, 2)
        _, timing = _expiry_timing(prev_calls[0].expiry, prev_as_of, prev_calls[0].role)
        return (
            f"어제 {focus} — {gap:.0f}% 위 · 미접촉 "
            f"({timing} 시나리오, 오늘 주가와 무관할 수 있음)"
        )

    touched = [
        r for r in near
        if ep.low <= r.strike * 1.003 and ep.high >= r.strike * 0.997
    ]
    if touched:
        r = min(touched, key=lambda x: abs(x.strike - ep.high))
        tag = _fmt_opt_ref(r, report_date)
        if ep.label == "up" and ep.close < r.strike * 0.995:
            return f"{tag} 터치 → 되돌림"
        if ep.label == "down" and ep.low < r.strike * 0.99:
            return f"{tag} 아래 이탈"
        return f"{tag} 근처 변동"

    if near:
        r = min(near, key=lambda x: abs(x.strike - ep.close))
        gap = abs(ep.close - r.strike) / r.strike * 100
        if gap > 3:
            return f"{_fmt_opt_ref(r, report_date)}와 {gap:.0f}% 거리"
        return f"{_fmt_opt_ref(r, report_date)} 근처"

    return "장중 옵션 tick 없음"


def _option_verdict(prev_calls: list[OptionRef], day: dict, prev_as_of: str) -> str:
    if not prev_calls:
        return "· 전일 스냅샷 없음"
    hi = day["high"]
    focus = _fmt_opt_refs(prev_calls, prev_as_of, 2)
    _, timing = _expiry_timing(prev_calls[0].expiry, prev_as_of, prev_calls[0].role)
    if hi >= min(r.strike for r in prev_calls) * 0.995:
        return f"· 어제 {focus} → 오늘 고가 접촉 · 연결 검증 ({timing})"
    chg = day["chg_pct"]
    word = "하락" if chg < -1 else "상승" if chg > 1 else "횡보"
    return (
        f"· 어제 {focus} → 오늘 최고 {_fmt_px(hi)} 미접촉 · "
        f"주가 {word} ({chg:+.1f}%) · {timing} 베팅이었음"
    )


def _strike_dist_pct(strike: float, spot: float) -> float:
    if not spot:
        return 99.0
    return abs(float(strike) - float(spot)) / float(spot) * 100.0


def _price_tier(strike: float, spot: float) -> int:
    """1=현재가±3%, 2=±5~8%, 3=원거리."""
    d = _strike_dist_pct(strike, spot)
    if d <= 3.0:
        return 1
    if d <= 8.0:
        return 2
    return 3


def _tier_label(tier: int) -> str:
    return {1: "1차(현재가 인접)", 2: "2차(중거리)", 3: "원거리 옵션 관심"}.get(
        tier, "원거리"
    )


def _judge_signal(
    ref: OptionRef,
    day: dict,
    prev_as_of: str,
    *,
    chg: float | None = None,
) -> dict[str, str]:
    """전일 옵션 관측 → 당일 가격 검증.

    규칙:
    - 콜/풋 거래량 ≠ 매수/매도 ≠ 방향 베팅 확정
    - 판정은 가격이 그 strike에 접근·반응했는지(적중/부분/미검증)
    - '기대와 방향이 달랐다' 식의 방향 확정 문구 금지
    """
    del chg  # 방향 성패로 쓰지 않음
    hi, lo, cl = float(day["high"]), float(day["low"]), float(day["close"])
    o = float(day.get("open") or cl)
    opt = _fmt_opt_ref(ref, prev_as_of)
    _, timing = _expiry_timing(ref.expiry, prev_as_of, ref.role)
    spot = cl or o
    tier = _price_tier(ref.strike, spot)
    actual = f"{_fmt_px(o)}→{_fmt_px(cl)} (고 {_fmt_px(hi)} / 저 {_fmt_px(lo)})"

    if ref.opt_type == "CALL":
        # 관측만 — 상승 기대 확정 금지
        observe = f"[관측] {opt} 거래 증가"
        if hi < ref.strike * 0.97:
            status, note = (
                "⬜ 미검증",
                f"[가격] 고 {_fmt_px(hi)} · {opt} 미도달 "
                f"(거리 {_strike_dist_pct(ref.strike, hi):.1f}%) → 실패가 아니라 미검증",
            )
        elif lo > ref.strike * 1.01:
            status, note = (
                "➖ 상회 유지",
                f"[가격] 저 {_fmt_px(lo)}이 이미 {opt} 위 — "
                f"오늘 그 가격대 반응은 따로 검증되지 않음",
            )
        elif hi >= ref.strike * 0.995:
            if cl >= ref.strike * 0.99:
                status, note = (
                    "✅ 적중",
                    f"[가격] 고 {_fmt_px(hi)} · {opt} 도달·종가 유지 → 가격 반응 확인",
                )
            else:
                status, note = (
                    "⚠️ 부분 검증",
                    f"[가격] 고 {_fmt_px(hi)} · {opt} 터치 후 종가 {_fmt_px(cl)}",
                )
        else:
            status, note = (
                "⚠️ 부분 검증",
                f"[가격] 고 {_fmt_px(hi)} · {opt}에 {_fmt_px(ref.strike - hi)} 근접",
            )
    else:
        observe = f"[관측] {opt} 거래 증가"
        if lo <= ref.strike * 1.005:
            if cl < ref.strike * 0.995:
                status, note = (
                    "⚠️ 부분 검증",
                    f"[가격] 저 {_fmt_px(lo)} · {opt} 구간을 하방으로 통과 "
                    f"(지지로 단정하지 않음)",
                )
            else:
                status, note = (
                    "✅ 적중",
                    f"[가격] 저 {_fmt_px(lo)} · {opt} 근처에서 반응 확인",
                )
        elif lo <= ref.strike * 1.03:
            status, note = (
                "⚠️ 부분 검증",
                f"[가격] 저 {_fmt_px(lo)} · {opt}에 근접",
            )
        else:
            status, note = (
                "⬜ 미검증",
                f"[가격] 저 {_fmt_px(lo)} · {opt} 미도달 → 미검증",
            )

    return {
        "kind": "콜" if ref.opt_type == "CALL" else "풋",
        "signal": opt,
        "timing": timing,
        "observe": observe,
        "expect": observe,  # 하위 호환
        "actual": actual,
        "verdict": status,
        "note": note,
        "strike": f"{ref.strike:g}",
        "approach": f"{(hi / ref.strike * 100) if ref.strike else 0:.1f}",
        "tier": str(tier),
        "tier_label": _tier_label(tier),
        "status_code": (
            "hit" if "적중" in status
            else "partial" if "부분" in status
            else "unverified" if "미검증" in status
            else "above" if "상회" in status
            else "other"
        ),
    }


def _yesterday_signal_refs(
    prev_snap: dict | None,
    day: dict,
    anomalies: list | None,
) -> list[tuple[OptionRef, str]]:
    """검증용 — 어제 콜·풋 상위(거리 무관 관측) 최대 4건."""
    if not prev_snap:
        return []
    rows: list[tuple[OptionRef, str]] = []
    for ref in _top_option_refs(prev_snap, 2, "CALL"):
        rows.append((ref, "콜"))
    for ref in _top_option_refs(prev_snap, 2, "PUT"):
        rows.append((ref, "풋"))
    # 중복 strike 제거
    seen: set[tuple[float, str]] = set()
    out: list[tuple[OptionRef, str]] = []
    for ref, kind in rows:
        key = (round(ref.strike, 2), ref.opt_type)
        if key in seen:
            continue
        seen.add(key)
        out.append((ref, kind))
    return out[:4]


def _overall_signal_verdict(judgments: list[dict[str, str]]) -> str:
    if not judgments:
        return "어제 옵션과 비교할 데이터가 없어요"
    codes = [j.get("status_code") for j in judgments]
    if any(c == "hit" for c in codes):
        return "일부 strike에서 가격 반응 확인"
    if any(c == "partial" for c in codes):
        return "일부 strike 근접·부분 검증"
    if all(c == "unverified" for c in codes):
        return "전일 옵션 관심 가격은 오늘 미도달(미검증)"
    return "옵션·가격 연결은 제한적"


def _prev_focus_strikes(prev_snap: dict | None) -> list[float]:
    out: list[float] = []
    for ref in _top_option_refs(prev_snap, 3, "CALL"):
        out.append(ref.strike)
    for ref in _top_option_refs(prev_snap, 2, "PUT"):
        out.append(ref.strike)
    seen: set[float] = set()
    uniq: list[float] = []
    for s in out:
        rs = round(s, 2)
        if rs in seen:
            continue
        seen.add(rs)
        uniq.append(s)
    return uniq


def _strikes_from_older_snap(older: dict | None) -> set[float]:
    """이틀 전 스냅의 콜/풋 관심 행사가."""
    if not older:
        return set()
    return {round(s, 2) for s in _prev_focus_strikes(older)}


def _what_changed_lines(
    *,
    day: dict,
    prev_snap: dict | None,
    older_snap: dict | None,
    judgments: list[dict[str, str]],
    dod: dict | None,
    news: list[dict],
) -> list[str]:
    """어제(또는 직전)와 다른 점만. 최소 1줄 — 정크 반복 방지용."""
    lines: list[str] = []
    spot = float(day.get("close") or 0)
    prev_focus = [round(s, 2) for s in _prev_focus_strikes(prev_snap)]
    older_focus = _strikes_from_older_snap(older_snap)

    # 1) 관심 행사가 이동 (현재가 근처 위주)
    if prev_focus and older_focus:
        def _near_enough(s: float) -> bool:
            return bool(spot) and abs(s - spot) / spot <= 0.08

        appeared = [s for s in prev_focus if s not in older_focus and _near_enough(s)]
        disappeared = [
            s for s in older_focus if s not in set(prev_focus) and _near_enough(s)
        ]
        if appeared:
            lines.append(
                "관심 행사가 이동: "
                + ", ".join(_fmt_px(s) for s in appeared[:3])
                + " 신규(현재가±8%)"
            )
        if disappeared and len(lines) < 3:
            lines.append(
                "어제 관심에서 빠짐: "
                + ", ".join(_fmt_px(s) for s in list(disappeared)[:2])
            )
    elif prev_focus and not older_focus:
        near = [s for s in prev_focus if spot and abs(s - spot) / spot <= 0.05]
        far = [s for s in prev_focus if s not in near]
        if near:
            lines.append(
                "어제 관심(현재가±5%): " + ", ".join(_fmt_px(s) for s in near[:3])
            )
        elif far:
            lines.append(
                "어제 관심은 원거리: " + ", ".join(_fmt_px(s) for s in far[:2])
                + " — 오늘 1차 검증 대상 아님"
            )

    # 2) 검증 결과 구성 (적중/부분/미검증 비율)
    if judgments:
        n = len(judgments)
        hits = sum(1 for j in judgments if j.get("status_code") == "hit")
        parts = sum(1 for j in judgments if j.get("status_code") == "partial")
        unv = sum(1 for j in judgments if j.get("status_code") == "unverified")
        if hits:
            hit_strikes = [
                _fmt_px(float(j["strike"]))
                for j in judgments
                if j.get("status_code") == "hit" and j.get("strike")
            ][:2]
            lines.append(
                f"오늘 새로 확인: {', '.join(hit_strikes)} 가격 반응(적중 {hits}/{n})"
            )
        elif parts:
            lines.append(f"부분 검증 {parts}/{n} · 완전 적중은 없음")
        elif unv == n:
            near_unv = [
                j
                for j in judgments
                if j.get("tier") == "1" and j.get("status_code") == "unverified"
            ]
            if near_unv:
                lines.append(
                    "가까운 관심가조차 미도달 — 오늘 옵션→주가 연결 증거 없음"
                )
            else:
                lines.append(
                    f"검증 {n}건 모두 미도달(미검증) — 원거리 관심만 있었음"
                )

    # 3) 옵션 활동성
    d = dod or {}
    vm = d.get("volume_mult")
    if vm is not None and vm >= 1.5:
        lines.append(f"옵션 총거래 어제 대비 {vm:.1f}배 (활동성↑ · 방향 아님)")
    elif vm is not None and vm <= 0.7:
        lines.append(f"옵션 총거래 어제 대비 {vm:.1f}배 (활동성↓)")

    cpr_p, cpr_t = d.get("cpr_prev"), d.get("cpr_today")
    if cpr_p and cpr_t and abs(cpr_t - cpr_p) / max(cpr_p, 0.01) >= 0.25:
        if cpr_t > cpr_p:
            lines.append("콜/풋 거래 비중: 콜 쪽으로 이동 (매수·매도 미확정)")
        else:
            lines.append("콜/풋 거래 비중: 풋 쪽으로 이동 (매수·매도 미확정)")

    # 4) 뉴스
    news_ctx = _news_day_context(news, day)
    if news_ctx:
        lines.append(f"뉴스: {news_ctx}")

    # 5) 주가 움직임 규모
    chg = abs(float(day.get("chg_pct") or 0))
    hi = float(day.get("high") or 0)
    lo = float(day.get("low") or 0)
    o = float(day.get("open") or spot or 1)
    range_pct = (hi - lo) / o * 100 if o else 0
    if chg >= 3 or range_pct >= 4:
        lines.append(
            f"주가 변동 큼: {_fmt_chg_label(day)} · 장중 폭 {range_pct:.1f}%"
        )
    elif chg < 0.3 and range_pct < 1.5 and not lines:
        lines.append(
            f"주가·옵션 모두 전일과 유사 — 오늘은 "
            f"{_fmt_px(spot)} 유지 여부만 짧게 기록"
        )

    # 최소 1줄 보장
    if not lines:
        if judgments:
            j0 = judgments[0]
            lines.append(
                f"대표 기록: {j0.get('observe', '')} → {j0.get('verdict', '')}"
            )
        else:
            lines.append(f"전일 옵션 스냅 없음 — 오늘 주가 {_fmt_chg_label(day)}만 기록")
    return lines[:4]


def _fmt_chg_label(day: dict) -> str:
    """등락 표기 — 시가→종가가 0에 가까우면 고가 기준으로 보완."""
    o = float(day.get("open") or 0)
    c = float(day.get("close") or 0)
    h = float(day.get("high") or 0)
    chg = float(day.get("chg_pct") or 0)
    if abs(chg) >= 0.3:
        return f"{_fmt_px(o)}→{_fmt_px(c)} ({chg:+.1f}%)"
    if o and h and (h - o) / o * 100 >= 0.5:
        up = (h - o) / o * 100
        return f"{_fmt_px(o)}→{_fmt_px(c)} (고 {_fmt_px(h)}, 장중 +{up:.1f}%)"
    return f"{_fmt_px(o)}→{_fmt_px(c)} ({chg:+.1f}%)"


def _verification_table(judgments: list[dict[str, str]]) -> str:
    """옵션 ↔ 주가 검증 표 (방향 단정 없음)."""
    if not judgments:
        return ""
    L = ["📋 어제 옵션 → 오늘 주가 (검증)"]
    L.append("전일 옵션 관측 | 오늘 가격 | 결과")
    for j in judgments:
        L.append(f"· {j['signal']} | {j['actual'].split('(')[0].strip()} | {j['verdict']}")
        L.append(f"  {j['note']}")
    return "\n".join(L)


def _plain_story(
    day: dict,
    news: list[dict],
    judgments: list[dict[str, str]],
    prev_as_of: str,
    prev_snap: dict | None,
    df: pd.DataFrame,
    *,
    episodes: int,
    fb: dict | None,
    older_snap: dict | None = None,
    dod: dict | None = None,
) -> str:
    """본문: 어제와 다른 점 → 검증 → 내일 (방향 베팅 확정 금지)."""
    move = _fmt_chg_label(day)
    spot = float(day.get("close") or 0)
    L: list[str] = ["💡 한눈에"]

    # 1) 어제와 다른 점 (강제 — 정크 반복 방지)
    L.append("📌 어제와 다른 점")
    for line in _what_changed_lines(
        day=day,
        prev_snap=prev_snap,
        older_snap=older_snap,
        judgments=judgments,
        dod=dod,
        news=news,
    ):
        L.append(f"· {line}")

    # 2) 검증 요약 (짧고 구체)
    if not judgments:
        L.append(f"· 어제 옵션 기록 없음 — 오늘 주가({move})만 기록.")
    else:
        summary = _overall_signal_verdict(judgments)
        L.append(f"· 검증 요약: {summary}. 주가 {move}.")

        def _rank(jj):
            try:
                s = float(jj["strike"])
            except (TypeError, ValueError):
                s = 0
            code = jj.get("status_code") or ""
            # 가까운 strike + 실제 반응 우선
            near = 0 if _price_tier(s, spot) == 1 else 1
            pri = {"hit": 0, "partial": 1, "unverified": 2}.get(code, 3)
            return (near, pri, abs(s - spot))

        j = sorted(judgments, key=_rank)[0]
        L.append(f"· 대표: {j['observe']} → {j['verdict']}")
        L.append(f"  {j['note']}")
        if j.get("status_code") == "unverified" and j.get("tier") == "1":
            L.append("· ※ 가까운 관심가도 미도달 = 실패가 아니라 미검증.")

    if not df.empty:
        L.append(
            f"· 장중: {_fmt_px(day['open'])} → "
            f"고 {_fmt_px(day['high'])} / 저 {_fmt_px(day['low'])} → "
            f"종 {_fmt_px(day['close'])}"
        )

    if fb and fb.get("available"):
        g = (fb.get("accuracy") or {}).get("grade") or {}
        L.append(
            f"· 어제 예측 채점: {g.get('grade', '?')} ({g.get('score', '?')}점) · 상세 ↓"
        )

    # 내일: 현재가 ±3%만 1차 — 옵션 근거 있는 것만
    near_watch: list[str] = []
    for j in judgments:
        try:
            s = float(j["strike"])
        except (TypeError, ValueError):
            continue
        if _price_tier(s, spot) == 1:
            near_watch.append(f"{_fmt_px(s)} ({j['kind']})")
    anchors: list[str] = []
    if day.get("close") is not None:
        anchors.append(_fmt_px(float(day["close"])))
    if day.get("high") is not None and abs(float(day["high"]) - spot) / max(spot, 1) <= 0.03:
        anchors.append(f"고 {_fmt_px(float(day['high']))}")
    if day.get("low") is not None and abs(float(day["low"]) - spot) / max(spot, 1) <= 0.03:
        anchors.append(f"저 {_fmt_px(float(day['low']))}")
    if near_watch:
        L.append(
            "· 내일 1차(현재가±3% · 옵션 근거): "
            + ", ".join(dict.fromkeys(near_watch + anchors))
        )
    else:
        L.append(
            f"· 내일 1차: {_fmt_px(spot)} 유지·이탈만 "
            f"(가까운 옵션 관심 없음 · 차트 관찰)"
        )
    far = [
        f"{_fmt_px(float(j['strike']))}"
        for j in judgments
        if j.get("tier") == "3"
        and float(j["strike"]) > spot * 1.03
        and j.get("status_code") == "unverified"
    ]
    if far:
        L.append(
            f"· 원거리 참고만: {', '.join(dict.fromkeys(far))} — 1차 시나리오 아님"
        )

    table = _verification_table(judgments)
    if table:
        L.append("")
        L.append(table)
    return "\n".join(L)



def _strike_plain(s: str) -> str:
    try:
        return f"{float(s):g}"
    except (TypeError, ValueError):
        return str(s)


def _signal_vs_stock_block(
    prev_snap: dict | None,
    day: dict,
    prev_as_of: str,
    anomalies: list | None,
) -> tuple[str, list[dict[str, str]]]:
    """판정 데이터만 계산 (본문 문구는 _plain_story)."""
    rows = _yesterday_signal_refs(prev_snap, day, anomalies)
    judgments: list[dict[str, str]] = []
    for ref, _kind in rows:
        judgments.append(_judge_signal(ref, day, prev_as_of))
    return "", judgments


def _today_conclusion(
    day: dict,
    news: list[dict],
    judgments: list[dict[str, str]],
    prev_as_of: str,
    prev_snap: dict | None,
) -> str:
    """하위 호환 — plain story로 대체됨."""
    return _plain_story(
        day, news, judgments, prev_as_of, prev_snap,
        pd.DataFrame(), episodes=3, fb=None,
    )


def _append_timeline_compact(
    L: list[str],
    df: pd.DataFrame,
    *,
    episodes: int,
    day: dict,
) -> None:
    """장중 주가만 — 옵션 각주 없음."""
    if df.empty:
        L.append("🕐 장중 실제 움직임")
        L.append(f"· 5분봉 없음 — 종가 {_fmt_px(day['close'])} 기준")
        L.append("")
        return
    eps = segment_three_acts(df) if episodes <= 3 else segment_fine(df, max_n=episodes)
    fine = episodes > 3
    act_label = "3막" if not fine else f"{len(eps)}구간"
    L.append(f"🕐 장중 실제 움직임 ({act_label})")
    for i, ep in enumerate(eps):
        stock = _narrate_stock(ep, day, fine=fine)
        if fine:
            head = f"{i + 1}) {_fmt_range(ep.start, ep.end)}"
        else:
            head = f"{i + 1}) {ep.title} ({_fmt_range(ep.start, ep.end)})"
        L.append(head)
        L.append(f"   {stock}")
    L.append("")


def _compact_lesson_line(fb: dict | None) -> str | None:
    if not fb or not fb.get("available"):
        return None
    import report_evidence as ev

    bl = ev.beginner_lesson(fb.get("lesson"), fb.get("missed_signals") or [])
    if not bl:
        missed = fb.get("missed_signals") or []
        if missed:
            short = missed[0] if len(missed[0]) <= 100 else missed[0][:97] + "..."
            return f"주목: {short}"
        return None
    for ln in bl.split("\n"):
        ln = ln.strip()
        if ln and not ln.startswith("💡") and not ln.startswith("→"):
            return ln[:120]
    return None


def _learning_today(
    fb: dict | None,
    ctx: dict | None,
    ticker: str,
    judgments: list[dict[str, str]] | None = None,
) -> str:
    """📚 오늘의 학습 — 실제 기록만. 빈 후보 문구는 넣지 않음."""
    import learning as learn
    import pattern_store as ps

    L = ["📚 오늘의 학습"]
    has_meat = False

    if fb and fb.get("available"):
        g = (fb.get("accuracy") or {}).get("grade") or {}
        grade = g.get("grade", "?")
        score = g.get("score", "?")
        pdate = fb.get("prediction_date") or "?"
        L.append(f"· 어제({pdate}) 예측 → 오늘 채점 {grade} ({score}점)")
        has_meat = True

        acc = fb.get("accuracy") or {}
        bits: list[str] = []
        if acc.get("band") == "PASS":
            bits.append("밴드✓")
        elif acc.get("band") == "FAIL":
            bits.append("밴드✗")
        if acc.get("support") == "PASS":
            bits.append("지지✓")
        elif acc.get("support") == "FAIL":
            bits.append("지지✗")
        if acc.get("direction") == "PASS":
            bits.append("방향✓")
        elif acc.get("direction") == "FAIL":
            bits.append("방향✗")
        elif acc.get("direction") in ("SKIP", "N/A", None) and "direction" in acc:
            if acc.get("direction") in ("SKIP", "N/A"):
                bits.append("방향(특수국면·채점제외)")
        if bits:
            L.append(f"  {' · '.join(bits)}")

        lesson = _compact_lesson_line(fb)
        if lesson:
            L.append(f"· 교훈: {lesson}")
            has_meat = True

    # 오늘 검증에서 바로 뽑은 실기록 (빈 '학습 중' 대체)
    if judgments:
        hits = [j for j in judgments if j.get("status_code") == "hit"]
        parts = [j for j in judgments if j.get("status_code") == "partial"]
        unv_near = [
            j
            for j in judgments
            if j.get("status_code") == "unverified" and j.get("tier") == "1"
        ]
        bits2: list[str] = []
        if hits:
            bits2.append(
                "적중 "
                + ", ".join(_fmt_px(float(j["strike"])) for j in hits[:2] if j.get("strike"))
            )
        if parts:
            bits2.append(f"부분 {len(parts)}건")
        if unv_near:
            bits2.append(
                "근거리 미검증 "
                + ", ".join(
                    _fmt_px(float(j["strike"])) for j in unv_near[:2] if j.get("strike")
                )
            )
        if bits2:
            L.append(f"· 오늘 관측 기록: {' · '.join(bits2)}")
            has_meat = True

    st = ps.pattern_state(ps.PATTERN_BREAKOUT_EXPAND)
    n_obs = st.get("n") or 0
    if n_obs > 0:
        rate = st.get("hit_rate")
        rate_s = f"{rate * 100:.0f}%" if rate is not None else "-"
        L.append(
            f"· 패턴 관측 {n_obs}회 · 적중 {st.get('hits', 0)} · 실패 {st.get('fails', 0)} "
            f"· 미검증 {st.get('unverified', 0)} · 평가승률 {rate_s}"
            f"{' · 가중 소폭반영' if st.get('status') == 'active' else ' · 후보(미반영)'}"
        )
        has_meat = True

    stats = (ctx or {}).get("최근7일") or learn.cumulative_stats(ticker, limit=7)
    if stats.get("available"):
        n = stats.get("n") or 0
        band = stats.get("band_accuracy_pct")
        if band is not None:
            L.append(f"· 누적 {n}회 · 밴드 {band}%")
            has_meat = True

    if not has_meat:
        L.append("· 오늘 새로 쌓을 학습 기록이 없습니다 (빈 후보 문구 생략).")
    return "\n".join(L)


def _build_reference_appendix(
    *,
    data: dict,
    base: dict,
    day: dict,
    dod: dict | None,
    anomalies: list,
    vol_anom: dict | None,
    snap: dict,
    fb: dict | None,
    ctx: dict | None,
    ticker: str,
    date: str,
    eventinfo: dict,
    news: list[dict],
    nxt: dict | None,
    prev_as_of: str,
    df: pd.DataFrame | None = None,
    episodes: int = 3,
) -> str:
    """📎 참고자료 — 장중 상세·옵션·뉴스·채점."""
    import events
    import learning
    import market_clock
    import report_builder
    import report_evidence as ev
    import report_flow

    L: list[str] = ["", "─" * 40, "📎 참고자료 (숫자·뉴스가 더 필요하면)", ""]

    L.append(market_clock.format_price_line(data))
    price_note = (eventinfo.get("price") or {}).get("note")
    if price_note:
        L.append(f"· {price_note}")
    L.append("")

    if df is not None and not df.empty:
        _append_timeline_compact(L, df, episodes=episodes, day=day)

    L.append(_option_change_plain(dod, anomalies, day, snap, date))
    L.append("")

    opt_rx = eventinfo.get("options_reaction")
    if opt_rx and opt_rx.get("summary"):
        L.append("📈 어닝·이벤트 옵션 반응")
        L.append(f"· {opt_rx['summary']}")
        L.append("")

    L.append("📰 관련 뉴스")
    if news:
        L.extend(events.format_news_lines(news, limit=3))
    else:
        L.append("- 오늘 유의미한 종목 관련 뉴스 없음")
    L.append("")

    scenarios = ev.format_scenarios(nxt)
    if scenarios:
        L.append(scenarios)
        L.append("")

    fb_full = learning.format_feedback_section(fb, include_lesson=True)
    if fb_full.strip():
        L.append(fb_full.rstrip())
        L.append("")

    L.append(report_flow.cumulative_learning_block(ticker, ctx))
    L.append("")

    import short_squeeze as sq_mod

    L.append(sq_mod.format_squeeze_section(data.get("short_squeeze")))
    L.append("")

    L.append(report_flow.limits_block(base))
    L.append("")

    L.extend(
        report_builder.format_data_summary(
            data, base, anomalies, vol_anom, narrative_source="stock"
        )
    )
    L.append("")
    L.append("⚠️ 관측·학습 기록이며 투자 조언이 아닙니다.")
    return "\n".join(L)


def _watch_lines(
    prev_calls: list[OptionRef], near: list[OptionRef], day: dict, report_date: str
) -> list[str]:
    lines: list[str] = []
    spot = day["close"]
    if near:
        r = min(near, key=lambda x: abs(x.strike - spot))
        lines.append(
            f"· {_fmt_opt_ref(r, report_date)} 재테스트 — 오늘과 같은 패턴인지"
        )
    elif prev_calls:
        r = min(prev_calls, key=lambda x: x.strike)
        if spot < r.strike:
            lines.append(
                f"· {_fmt_opt_ref(r, report_date)}까지 회복 시 옵션 맥락 재평가"
            )
    if day["chg_pct"] <= -3:
        lines.append("· 급락 후 다음날: 장 초반 30분 방향이 전일 패턴과 같은지")
    if not lines:
        lines.append("· 특별 watch 없음 — 변동 축소일")
    return lines


def _append_timeline_section(
    L: list[str],
    df: pd.DataFrame,
    *,
    episodes: int,
    prev_calls: list[OptionRef],
    near: list[OptionRef],
    day: dict,
    report_date: str,
    prev_as_of: str,
) -> None:
    """장중 타임라인 + 해석 블록."""
    eps = segment_three_acts(df) if episodes <= 3 else segment_fine(df, max_n=episodes)
    fine = episodes > 3
    act_label = "3막" if not fine else f"{len(eps)}구간"
    L.append(f"🕐 장중 {act_label}")
    for i, ep in enumerate(eps):
        stock = _narrate_stock(ep, day, fine=fine)
        opt = _option_footnote(
            ep, prev_calls, near, day["high"],
            report_date, prev_as_of, act_index=i, fine=fine,
        )
        if fine:
            head = f"{i + 1}) {_fmt_range(ep.start, ep.end)}"
        else:
            head = f"{i + 1}) {ep.title} ({_fmt_range(ep.start, ep.end)})"
        L.append(head)
        L.append(f"   {stock}")
        if opt != "—":
            L.append(f"   ↳ 옵션: {opt}")
    L.append("")


def _news_day_context(news: list[dict], day: dict | None = None) -> str | None:
    """뉴스 제목에서 오늘 장면 한 줄."""
    chg = (day or {}).get("chg_pct", 0)
    titles = [(n.get("title") or "") for n in (news or [])]

    if chg <= -5:
        for t in titles:
            tl = t.lower()
            if "data center" in tl or "transformation" in tl or "profitability" in tl:
                return "데이터센터 전환·수익성 우려로 급락 (실적 이후)"
            if any(k in tl for k in ("sink", "tumble", "selloff", "plunge", "falls")):
                return "뉴스 악재로 급락 — 옵션보다 헤드라인·주가 우선"

    for t in titles:
        tl = t.lower()
        if any(k in tl for k in ("earnings", "실적", " q1", " q2", " q3", " q4")):
            if chg <= -3 and any(k in tl for k in ("sink", "tumble", "fall", "miss")):
                return "실적·가이던스 실망 후 급락"
            if chg <= -3:
                return "실적 발표 전후 — 옵션 숫자보다 실적·뉴스·주가 우선"
            return "실적 관련 뉴스 — 헤드라인과 주가를 먼저 본다"
    return None


def _headline_block(
    day: dict,
    news: list[dict],
    prev_calls: list[OptionRef],
    prev_as_of: str,
) -> str:
    """💡 오늘 무슨 일 — 주가·뉴스 중심."""
    L = ["💡 오늘 무슨 일"]
    ctx = _news_day_context(news, day)
    chg = day["chg_pct"]
    if ctx:
        L.append(f"· {ctx}")
    L.append(
        f"· 주가 {_fmt_px(day['open'])} → {_fmt_px(day['close'])} ({chg:+.1f}%), "
        f"고 {_fmt_time(day['high_time'])} {_fmt_px(day['high'])} · "
        f"저 {_fmt_time(day['low_time'])} {_fmt_px(day['low'])}"
    )
    if prev_calls:
        focus = _fmt_opt_refs(prev_calls, prev_as_of, 2)
        _, timing = _expiry_timing(prev_calls[0].expiry, prev_as_of, prev_calls[0].role)
        hi = day["high"]
        if hi < min(r.strike for r in prev_calls) * 0.995:
            L.append(
                f"· 어제 옵션: {focus} (어제 기준 {timing}) — "
                f"오늘 주가는 {_fmt_px(hi)}까지만, 당장 상방과는 거리 있음"
            )
        note = _horizon_note(prev_calls, prev_as_of)
        if note:
            L.append(f"· {note.lstrip('※ ')}")
    elif chg <= -3:
        L.append("· 급락일 — 옵션 집중가보다 실제 주가 움직임을 먼저 본다")
    return "\n".join(L)


def _price_moves_plain(
    day: dict,
    data: dict,
    base: dict,
    dod: dict | None,
    fb: dict | None,
) -> str:
    """🎯 오늘 가격에서 일어난 일 — 멀리 있는 strike 목록 제거."""
    import report_flow

    a = report_flow._build_case_analysis(data, base, dod, fb)
    L = ["🎯 오늘 가격에서 일어난 일"]
    primary = a.get("primary")
    hi, lo, cl = day["high"], day["low"], day["close"]

    if primary and primary["code"] in (
        "test_reject",
        "break_hold",
        "tested",
        "support_hold",
        "support_fail",
    ):
        s = primary["strike"]
        if primary["code"] == "test_reject":
            L.append(
                f"· {_fmt_px(s)}: 여기까지 올랐다가(고 {_fmt_px(hi)}) "
                f"못 넘고 밀림 → 종가 {_fmt_px(cl)}"
            )
        elif primary["code"] == "break_hold":
            L.append(f"· {_fmt_px(s)}: 돌파 후 종가까지 위에 유지")
        elif primary["code"] == "tested":
            L.append(
                f"· {_fmt_px(s)}: 한번 테스트했지만 종가 {_fmt_px(cl)} — "
                f"아직 '안착'은 아님"
            )
        elif primary["code"] == "support_fail":
            L.append(f"· {_fmt_px(s)}: 아래로 깨짐 — 추가 하락 구간")
        else:
            L.append(f"· {_fmt_px(s)}: 지지 테스트 후 유지")
    elif day["chg_pct"] <= -3:
        L.append(
            f"· 뚜렷한 '저항 문' 없이 하루 종일 밀림 "
            f"({_fmt_px(day['open'])} → {_fmt_px(cl)})"
        )
    else:
        L.append(f"· 큰 가격대 반응 없음 — {_fmt_px(lo)}~{_fmt_px(hi)} 범위")

    return "\n".join(L)


def _option_change_plain(
    dod: dict | None,
    anomalies: list | None,
    day: dict,
    snap: dict | None,
    report_date: str,
) -> str:
    """📊 옵션 변화 — 거래량≠방향 베팅."""
    L = ["📊 옵션 쪽 변화 (쉽게 말하면)"]
    L.append(
        "· 규칙: Call/Put Volume 증가 ≠ 매수 증가 ≠ 상승·하락 베팅. "
        "매수·매도 합이므로 방향은 확정하지 않습니다."
    )
    d = dod or {}
    if not d.get("available"):
        L.append("· 어제와 비교할 옵션 숫자가 없어요.")
        return "\n".join(L)

    oi_tag = "[OI: 전일]" if "전일" in str(
        (snap or {}).get("metrics", {}).get("oi_source")
        or (snap or {}).get("oi_source")
        or ""
    ) else "[OI]"
    # snap may store oi_source on metrics
    src = ""
    if snap:
        src = str((snap.get("metrics") or {}).get("oi_source") or snap.get("oi_source") or "")
    if "전일" in src or "미갱신" in src:
        L.append(f"· 시간축: [Volume: 당일] vs {oi_tag} — 오늘 거래가 신규 포지션인지는 확정 불가.")

    today_calls = _top_option_refs(snap, 3)
    if today_calls:
        focus = _fmt_opt_refs(today_calls, report_date, 3)
        L.append(f"· 오늘 콜 거래 TOP: {focus} [Volume: 당일]")
        L.append(
            "· 주가가 행사가에 가까워지면 콜 프리미엄이 "
            "내재가치·델타 변화의 영향을 받을 수 있지만, "
            "IV·시간가치 변화도 함께 작용합니다."
        )
        note = _horizon_note(today_calls, report_date)
        if note:
            L.append(f"· {note.lstrip('※ ')}")

    vm = d.get("volume_mult")
    if vm is not None and vm >= 1.5:
        L.append(f"· 옵션 총거래량이 어제보다 {vm:.1f}배 — 활동성 증가 (방향 아님).")
    elif vm is not None:
        L.append("· 옵션 총거래량은 어제와 비슷한 수준.")

    cpr_p, cpr_t = d.get("cpr_prev"), d.get("cpr_today")
    if cpr_p and cpr_t:
        if cpr_t > cpr_p * 1.1:
            L.append(
                "· 콜 거래가 늘었습니다. 다만 거래량만으로 콜 매수인지 매도인지는 "
                "확인할 수 없어 방향성은 확정하지 않습니다."
            )
        elif cpr_t < cpr_p * 0.9:
            L.append(
                "· 풋 거래가 늘었습니다. 다만 거래량만으로 풋 매수인지 매도인지는 "
                "확인할 수 없어 방향성은 확정하지 않습니다."
            )

    # V/OI 상위 — 활동성 지표
    top_voi = ((snap or {}).get("metrics") or {}).get("top_voi") or []
    if top_voi:
        r0 = top_voi[0]
        try:
            L.append(
                f"· V/OI 극단 예: {r0.get('expiry')} {r0.get('type')} "
                f"${float(r0['strike']):g} V/OI {r0.get('voi')} "
                f"(vol {int(r0.get('volume') or 0):,} / OI {int(r0.get('oi') or 0):,}). "
                f"V/OI는 방향 신호가 아니라 "
                f"‘기존 OI 대비 오늘 거래가 컸다’는 활동성 지표입니다. "
                f"다음날 OI 증감으로 신규·교체 후보를 사후 확인합니다."
            )
        except (TypeError, ValueError, KeyError):
            pass

    put_surge = [
        an for an in (anomalies or [])
        if isinstance(an, dict) and "PUT" in (an.get("message") or "").upper()
    ]
    if put_surge:
        msg = (put_surge[0].get("message") or "")[:72]
        L.append(f"· 풋 OI 급변 관측: {msg}")

    oi_p, oi_t = d.get("oi_prev"), d.get("oi_today")
    if oi_p and oi_t and oi_p > 0:
        pct = (oi_t - oi_p) / oi_p * 100
        if abs(pct) >= 10:
            L.append(
                f"· 전체 OI {'증가' if pct > 0 else '감소'} ({pct:+.0f}%) — "
                f"포지션 재배치 흔적 (방향 미확정)."
            )

    if len(L) <= 2:
        L.append("· 오늘 특별히 눈에 띄는 옵션 변화는 적습니다.")
    return "\n".join(L)


def _day_story_block(
    day: dict,
    news: list[dict],
    prev_calls: list[OptionRef],
    dod: dict | None,
    prev_as_of: str,
) -> str:
    """🧠 정리 — 뉴스·주가 맥락 (헤드라인과 중복 최소)."""
    L = ["🧠 정리"]
    chg = day["chg_pct"]

    if prev_calls and day["high"] < min(r.strike for r in prev_calls) * 0.85:
        focus = _fmt_opt_refs(prev_calls, prev_as_of, 2)
        _, timing = _expiry_timing(prev_calls[0].expiry, prev_as_of, prev_calls[0].role)
        L.append(
            f"· 어제 {focus} ({timing})는 오늘 고가 {_fmt_px(day['high'])}와 무관 — "
            f"월간 만기면 더욱 '당장' 신호 아님"
        )

    vm = (dod or {}).get("volume_mult")
    if vm and vm >= 2 and chg <= -3:
        L.append(
            "· 옵션 거래는 늘었지만, 급락일엔 '반등 베팅'과 '하락 헤지'가 섞여 있음"
        )
    elif chg <= -5 and not _news_day_context(news, day):
        L.append(f"· {abs(chg):.0f}%대 급락 — 어제 옵션 신호보다 오늘 주가가 우선")

    if len(L) == 1:
        L.append("· 특별한 해석 없음 — 데이터만 기록")
    return "\n".join(L)


def _watch_verify_block(
    day: dict,
    judgments: list[dict[str, str]],
) -> str:
    """📌 내일 검증 — 현재가 ±3%만 1차."""
    L = ["📌 내일 검증"]
    spot = float(day["close"])
    primary: list[str] = [f"{_fmt_px(spot)} 유지 vs 이탈"]
    secondary: list[str] = []
    for j in judgments:
        try:
            s = float(j["strike"])
        except (TypeError, ValueError):
            continue
        tier = _price_tier(s, spot)
        bit = f"{_fmt_px(s)} ({j['kind']} · {j.get('tier_label', '')})"
        if tier == 1:
            primary.append(bit)
        elif tier == 2:
            secondary.append(bit)
    prim = list(dict.fromkeys(primary))[:4]
    L.append("· 1차: " + " · ".join(prim))
    sec = list(dict.fromkeys(secondary))[:3]
    if sec:
        L.append("· 2차(참고): " + " · ".join(sec))
    far = [j for j in judgments if j.get("tier") == "3"]
    if far:
        L.append(
            "· 원거리(1차 금지): "
            + ", ".join(j["signal"] for j in far[:2])
        )
    L.append("· 목표가 아님 — 가격이 그 구간에 닿았는지·어떻게 반응했는지만 확인")
    return "\n".join(L)


def _watch_block(nxt: dict | None, day: dict, fb: dict | None) -> str:
    """📌 내일 관찰 — 체크포인트 + 검증 통합."""
    L = ["📌 내일 관찰"]
    spot = day["close"]
    seen: set[str] = set()

    def _add(text: str, why: str | None = None) -> None:
        key = text.split("—")[0].strip().lower()
        if key in seen:
            return
        seen.add(key)
        line = f"· {text}"
        if why:
            line += f" ({why})"
        L.append(line)

    for c in (nxt or {}).get("checkpoints") or []:
        if isinstance(c, dict) and c.get("text"):
            _add(str(c["text"]), c.get("why"))

    if fb and fb.get("available"):
        act = fb.get("actual") or {}
        hi = act.get("high")
        if hi and float(hi) > spot * 1.02:
            _add(f"{_fmt_px(float(hi))} 재테스트 — 오늘처럼 되돌림인지")

    if len(L) == 1:
        _add(f"{_fmt_px(spot)} 근처 유지 vs 이탈")

    L.append("· 목표가 아님 — 어제·오늘 말이 맞는지만 검증")
    return "\n".join(L)


def _patch_data_with_intraday(data: dict, day: dict) -> dict:
    """스냅샷 data + 채점 actual에 5분봉 OHLC 반영."""
    out = dict(data)
    out["regular_close"] = day["close"]
    out["spot"] = day["close"]
    out["market_session"] = "closed"
    fb = dict(out.get("prediction_feedback") or {})
    if fb.get("available"):
        act = dict(fb.get("actual") or {})
        act["open"] = round(day["open"], 2)
        act["high"] = round(day["high"], 2)
        act["low"] = round(day["low"], 2)
        act["close"] = round(day["close"], 2)
        if act.get("open"):
            act["return_pct"] = round(
                (day["close"] - day["open"]) / day["open"] * 100, 2
            )
        fb["actual"] = act
        out["prediction_feedback"] = fb
    return out


def build_full_report(
    ticker: str,
    date: str,
    *,
    episodes: int = 3,
    snap: dict | None = None,
) -> str:
    """선형 스토리: 결론 → 신호판정 → 장중 → 학습 → 내일 / 참고자료."""
    import events
    import report_evidence as ev
    import snapshot_store

    if snap is None:
        snap = snapshot_store.load_snapshot(ticker, date)
    if not snap:
        body = build_stock_timeline_mockup(ticker, date, episodes=episodes)
        return body + "\n\n※ 스냅샷 없음 — 뉴스·옵션·학습 섹션은 생략됐습니다."

    df = fetch_5m_bars(ticker, date)
    if df.empty:
        close = float(snap.get("regular_close") or snap.get("spot") or 0)
        prev_close = float(snap.get("previous_close") or 0)
        fb0 = snap.get("prediction_feedback") or {}
        act0 = (fb0.get("actual") or {}) if fb0.get("available") else {}
        o = float(act0.get("open") or prev_close or close)
        h = float(act0.get("high") or max(close, o))
        l = float(act0.get("low") or min(close, o))
        # 전일 종가 대비 등락 (시가 데이터가 비어도 방향이 보이게)
        base_px = prev_close or o
        chg = round((close - base_px) / base_px * 100, 2) if base_px else 0.0
        day = {
            "open": o,
            "close": close,
            "high": h,
            "low": l,
            "chg_pct": chg,
            "high_time": dt.datetime.now(_ET),
            "low_time": dt.datetime.now(_ET),
        }
    else:
        day = _day_summary(df)

    data = _patch_data_with_intraday(snap, day)
    base = data.get("metrics") or {}
    dod = data.get("day_over_day")
    eventinfo = data.get("events") or {}
    fb = data.get("prediction_feedback")
    ctx = data.get("learning_context")
    anomalies = data.get("anomalies") or []
    vol_anom = data.get("volume_anomaly")

    prev_trade = _prev_trading_snapshot_date(ticker, date)
    prev_date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    prev_snap = snapshot_store.load_snapshot(ticker, prev_date) or _load_snapshot(
        ticker, prev_trade
    )
    older_date = _prev_trading_snapshot_date(
        ticker, prev_snap.get("date") if prev_snap else prev_trade
    )
    older_snap = _load_snapshot(ticker, older_date) if older_date else None
    if older_snap and prev_snap and older_snap.get("date") == prev_snap.get("date"):
        older_snap = None
    prev_as_of = prev_snap.get("date", prev_date) if prev_snap else prev_date

    news = eventinfo.get("news") or []
    earn = eventinfo.get("earnings") or {}

    signal_block, judgments = _signal_vs_stock_block(
        prev_snap, day, prev_as_of, anomalies
    )
    del signal_block  # 본문은 plain story만

    nxt = events.next_session_scenarios(
        base, day["close"], data=data, earnings=earn or None
    )
    if nxt:
        nxt["reference_spot"] = round(day["close"], 2)

    L: list[str] = []
    L.append(f"📊 {ticker} 데일리")
    L.append(date)
    L.append("")

    banner = ev.low_confidence_banner(base)
    if banner:
        L.append(banner)
        L.append("")

    if earn.get("phase") in ("임박", "직후") and earn.get("message"):
        L.append(f"🚨 {earn['message']}")
        L.append("")

    L.append(
        _plain_story(
            day, news, judgments, prev_as_of, prev_snap, df,
            episodes=episodes, fb=fb, older_snap=older_snap, dod=dod,
        )
    )
    L.append("")
    L.append(_learning_today(fb, ctx, ticker, judgments=judgments))

    L.append(
        _build_reference_appendix(
            data=data,
            base=base,
            day=day,
            dod=dod,
            anomalies=anomalies,
            vol_anom=vol_anom,
            snap=snap,
            fb=fb,
            ctx=ctx,
            ticker=ticker,
            date=date,
            eventinfo=eventinfo,
            news=news,
            nxt=nxt,
            prev_as_of=prev_as_of,
            df=df,
            episodes=episodes,
        )
    )
    return "\n".join(L)


def build_stock_timeline_mockup(
    ticker: str,
    date: str,
    *,
    episodes: int = 3,
) -> str:
    df = fetch_5m_bars(ticker, date)
    if df.empty:
        return f"📊 {ticker} · {date} (목업)\n\n5분봉 데이터 없음."

    day = _day_summary(df)

    prev_date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    prev_snap = _load_snapshot(ticker, prev_date) or _load_snapshot(
        ticker, _prev_trading_snapshot_date(ticker, date)
    )
    today_snap = _load_snapshot(ticker, date)
    prev_as_of = prev_snap.get("date", prev_date) if prev_snap else prev_date
    prev_calls = _top_option_refs(prev_snap, 3)
    near = _near_strikes(today_snap or prev_snap, day["close"], date)

    chg = day["chg_pct"]
    mood = "🟢" if chg > 1.5 else "🔴" if chg < -1.5 else "🟡"

    L: list[str] = []
    L.append(f"📊 {ticker} · {date} (목업)")
    L.append("")

    # 1) 주가 먼저
    L.append("💰 오늘 주가")
    L.append(
        f"{mood} {_fmt_px(day['open'])} → {_fmt_px(day['close'])} ({chg:+.1f}%)"
    )
    L.append(
        f"고 {_fmt_time(day['high_time'])} {_fmt_px(day['high'])} · "
        f"저 {_fmt_time(day['low_time'])} {_fmt_px(day['low'])}"
    )
    L.append("")

    # 2) 장중 서사
    _append_timeline_section(
        L, df, episodes=episodes,
        prev_calls=prev_calls, near=near, day=day,
        report_date=date, prev_as_of=prev_as_of,
    )

    # 3) 옵션 검증
    L.append("📊 옵션 검증")
    L.append(_option_verdict(prev_calls, day, prev_as_of))
    L.append("")

    # 4) 내일
    L.append("📌 내일 관찰")
    L.extend(_watch_lines(prev_calls, near, day, date))
    L.append("")

    L.append("⚠️ 목업 · 장중 옵션 미수집 · 투자 조언 아님")
    return "\n".join(L)


def _prev_trading_snapshot_date(ticker: str, before: str) -> str:
    d = Path("snapshots") / ticker
    if not d.exists():
        return before
    dates = sorted(p.stem for p in d.glob("*.json") if p.stem < before)
    return dates[-1] if dates else before


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="주가 중심 데일리 리포트")
    p.add_argument("ticker", nargs="?", default="IREN")
    p.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD")
    p.add_argument(
        "--episodes",
        type=int,
        default=3,
        choices=[3, 6],
        help="3=장 초·중·마감, 6=30분 세부 구간",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="뉴스·옵션변화·시나리오·채점·학습 포함 전체 리포트",
    )
    p.add_argument(
        "--save-sample",
        action="store_true",
        help="samples/<TICKER>-<DATE>.txt 로 저장",
    )
    args = p.parse_args(argv)
    ticker = args.ticker.upper()
    date = args.date
    if not date:
        files = sorted((Path("snapshots") / ticker).glob("*.json"))
        date = files[-1].stem if files else dt.date.today().isoformat()

    if args.full:
        text = build_full_report(ticker, date, episodes=args.episodes)
    else:
        text = build_stock_timeline_mockup(ticker, date, episodes=args.episodes)

    if args.save_sample:
        out = Path("samples") / f"{ticker}-{date}.txt"
        out.parent.mkdir(exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"저장: {out}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
