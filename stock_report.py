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
    """📊 옵션 변화 — 쉬운 말 + 만기."""
    L = ["📊 옵션 쪽 변화 (쉽게)"]
    d = dod or {}
    if not d.get("available"):
        L.append("· 어제와 비교 데이터 없음")
        return "\n".join(L)

    today_calls = _top_option_refs(snap, 2)
    if today_calls:
        L.append(f"· 오늘 콜 거래 집중: {_fmt_opt_refs(today_calls, report_date, 2)}")
        note = _horizon_note(today_calls, report_date)
        if note:
            L.append(f"· {note.lstrip('※ ')}")

    vm = d.get("volume_mult")
    if vm is not None and vm >= 1.5:
        L.append(
            f"· 거래량 어제의 {vm:.1f}배 — "
            + ("급락일에 옵션도 같이 뜨거워짐" if day["chg_pct"] <= -3 else "활발한 거래일")
        )
    elif vm is not None:
        L.append("· 거래량은 평소와 비슷")

    cpr_p, cpr_t = d.get("cpr_prev"), d.get("cpr_today")
    if cpr_p and cpr_t:
        if cpr_t > cpr_p * 1.1:
            L.append("· 콜 비중↑ — '오른다' 베팅 증가 흔적 (확정 아님)")
        elif cpr_t < cpr_p * 0.9:
            L.append("· 풋 비중↑ — '떨어진다/헤지' 쪽 베팅 증가 흔적 (확정 아님)")

    put_surge = [
        an for an in (anomalies or [])
        if isinstance(an, dict) and "PUT" in (an.get("message") or "").upper()
    ]
    if put_surge:
        msg = (put_surge[0].get("message") or "")[:72]
        L.append(f"· 풋 OI 급증: {msg} — 만기·strike 함께 봐야 함")

    oi_p, oi_t = d.get("oi_prev"), d.get("oi_today")
    if oi_p and oi_t and oi_p > 0:
        pct = (oi_t - oi_p) / oi_p * 100
        if abs(pct) >= 10:
            L.append(f"· 전체 OI {'늘음' if pct > 0 else '줄음'} ({pct:+.0f}%) — 포지션 재배치")

    if len(L) == 1:
        L.append("· 눈에 띄는 변화 없음")
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
    """주가 3막 + 기존 유용 섹션(뉴스·옵션변화·시나리오·채점·학습) 통합."""
    import events
    import learning
    import market_clock
    import report_evidence as ev
    import report_flow
    import snapshot_store

    if snap is None:
        snap = snapshot_store.load_snapshot(ticker, date)
    if not snap:
        body = build_stock_timeline_mockup(ticker, date, episodes=episodes)
        return body + "\n\n※ 스냅샷 없음 — 뉴스·옵션·학습 섹션은 생략됐습니다."

    df = fetch_5m_bars(ticker, date)
    if df.empty:
        day = {
            "open": snap.get("spot") or 0,
            "close": snap.get("regular_close") or snap.get("spot") or 0,
            "high": snap.get("spot") or 0,
            "low": snap.get("spot") or 0,
            "chg_pct": 0,
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

    prev_date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    prev_snap = snapshot_store.load_snapshot(ticker, prev_date) or _load_snapshot(
        ticker, _prev_trading_snapshot_date(ticker, date)
    )
    prev_as_of = prev_snap.get("date", prev_date) if prev_snap else prev_date
    prev_calls = _top_option_refs(prev_snap, 3)
    near = _near_strikes(snap, day["close"], date)

    L: list[str] = []
    L.append(f"📊 {ticker} 데일리")
    L.append(date)
    L.append("")

    banner = ev.low_confidence_banner(base)
    if banner:
        L.append(banner)
        L.append("")

    earn = eventinfo.get("earnings") or {}
    if earn.get("phase") in ("임박", "직후") and earn.get("message"):
        L.append(f"🚨 {earn['message']}")
        L.append("")

    L.append(market_clock.format_price_line(data))
    price_note = (eventinfo.get("price") or {}).get("note")
    if price_note:
        L.append(f"· {price_note}")
    L.append("")

    fb_block = learning.format_feedback_section(fb, include_lesson=False)
    if fb_block.strip():
        L.append(fb_block.rstrip())
        L.append("")

    news = eventinfo.get("news") or []
    L.append(_headline_block(day, news, prev_calls, prev_as_of))
    L.append("")

    _append_timeline_section(
        L, df, episodes=episodes,
        prev_calls=prev_calls, near=near, day=day,
        report_date=date, prev_as_of=prev_as_of,
    )

    L.append("📊 옵션 ↔ 주가 검증")
    L.append(_option_verdict(prev_calls, day, prev_as_of))
    L.append("")

    L.append(_price_moves_plain(day, data, base, dod, fb))
    L.append("")

    L.append(_option_change_plain(dod, anomalies, day, snap, date))
    L.append("")

    L.append(_day_story_block(day, news, prev_calls, dod, prev_as_of))
    L.append("")

    L.append("📰 관련 뉴스")
    if news:
        L.extend(events.format_news_lines(news, limit=3))
    else:
        L.append("- 오늘 유의미한 종목 관련 뉴스 없음")
    L.append("")

    opt_rx = eventinfo.get("options_reaction")
    if opt_rx and opt_rx.get("summary"):
        L.append("📈 어닝·이벤트 옵션 반응")
        L.append(f"· {opt_rx['summary']}")
        L.append("")

    # 시나리오는 종가 기준으로 재계산 (스냅샷 시점 spot과 다를 수 있음)
    nxt = events.next_session_scenarios(
        base, day["close"], data=data, earnings=earn or None
    )
    if nxt:
        nxt["reference_spot"] = round(day["close"], 2)
    scenarios = ev.format_scenarios(nxt)
    if scenarios:
        L.append(scenarios)
        L.append("")

    L.append(_watch_block(nxt, day, fb))
    L.append("")

    L.append(report_flow.cumulative_learning_block(ticker, ctx))
    L.append("")

    L.append(report_flow.limits_block(base))
    L.append("")
    L.append("⚠️ 관측·학습 기록이며 투자 조언이 아닙니다.")
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
