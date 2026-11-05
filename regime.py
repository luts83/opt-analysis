"""가격 레짐·낙폭·동조건 전진 빈도 + 일일 브리프 / 주간 방향 본문.

방향의 주신호는 가격(+SPY). 콜/풋 우세를 강세·약세로 쓰지 않는다.
확률 % 연출 금지 — 과거 동조건 빈도와 표본수만 명시.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import snapshot_store

MIN_FREQ_SAMPLES = 8
LOOKBACK = 20
FWD_DAYS = 5

# main.py 가 일일 실행 시 SPY 레짐을 한 번 넣어 둔다
_SPY_CACHE: dict[str, Any] | None = None

REGIME_KO = {
    "uptrend": "상승추세",
    "downtrend": "하락추세",
    "sideways": "횡보",
    "crash": "급락중",
}


def _spot_of(snap: dict) -> float | None:
    for k in ("regular_close", "spot", "previous_close"):
        v = snap.get(k)
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                continue
    return None


def _closes_from_snaps(snaps: list[dict]) -> list[tuple[str, float]]:
    """날짜 오름차순 (date, close)."""
    rows: list[tuple[str, float]] = []
    for s in snaps:
        px = _spot_of(s)
        d = str(s.get("date") or "")
        if px is not None and d:
            rows.append((d, px))
    rows.sort(key=lambda x: x[0])
    return rows


def load_price_series(ticker: str, asof: str | None = None) -> list[tuple[str, float]]:
    """스냅샷에서 종가 시계열. asof 포함 이전."""
    import config

    here = Path(__file__).resolve().parent
    base = Path(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else here / base
    d = base / ticker.upper()
    if not d.exists():
        return []
    rows: list[tuple[str, float]] = []
    for p in sorted(d.glob("????-??-??.json")):
        if asof and p.stem > asof:
            continue
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        px = _spot_of(snap)
        if px is not None:
            rows.append((p.stem, px))
    return rows


def classify_regime(closes: list[float]) -> dict[str, Any]:
    """종가 리스트(오래된→최근). 레짐·낙폭·기울기."""
    if len(closes) < 5:
        return {
            "available": False,
            "regime": "sideways",
            "regime_ko": REGIME_KO["sideways"],
            "reason": "가격 이력 부족",
        }
    last = closes[-1]
    window = closes[-LOOKBACK:] if len(closes) >= LOOKBACK else closes
    w0, w1 = window[0], window[-1]
    slope_pct = (w1 - w0) / w0 * 100 if w0 else 0.0
    peak = max(window)
    drawdown_pct = (last - peak) / peak * 100 if peak else 0.0
    # 최근 5일 음수 일수
    rets = []
    for i in range(1, min(6, len(closes))):
        a, b = closes[-(i + 1)], closes[-i]
        if a:
            rets.append((b - a) / a * 100)
    down_days = sum(1 for r in rets if r < -0.15)
    ma = sum(window) / len(window)
    below_ma = last < ma

    if drawdown_pct <= -20 or (drawdown_pct <= -12 and slope_pct <= -8):
        regime = "crash"
    elif slope_pct <= -5 and below_ma:
        regime = "downtrend"
    elif slope_pct >= 5 and not below_ma:
        regime = "uptrend"
    else:
        regime = "sideways"

    flags: list[str] = []
    if drawdown_pct <= -20:
        flags.append(f"고점대비 {drawdown_pct:.1f}%")
    elif drawdown_pct <= -10:
        flags.append(f"고점대비 {drawdown_pct:.1f}%")
    if down_days >= 4:
        flags.append(f"최근{len(rets)}일 중 {down_days}일 약세")
    if slope_pct <= -15:
        flags.append(f"20일 기울기 {slope_pct:.1f}%")

    return {
        "available": True,
        "regime": regime,
        "regime_ko": REGIME_KO[regime],
        "slope_pct": round(slope_pct, 2),
        "drawdown_pct": round(drawdown_pct, 2),
        "peak": round(peak, 2),
        "last": round(last, 2),
        "ma": round(ma, 2),
        "below_ma": below_ma,
        "down_days_5": down_days,
        "flags": flags,
        "lookback": len(window),
    }


def option_status_line(snap: dict | None) -> str:
    """콜 우세=강세 금지. 구성/근접만."""
    if not snap:
        return "옵션: 데이터 없음"
    m = snap.get("metrics") or {}
    va = snap.get("volume_anomaly") or {}
    dod = snap.get("day_over_day") or {}
    bits: list[str] = []
    vm = dod.get("volume_mult")
    if vm is None:
        vm = va.get("mult")
    if va.get("is_anomaly") or (vm is not None and float(vm) >= 2.0):
        bits.append(f"거래 급증({float(vm):.1f}배)" if vm else "거래 급증")
    # 풋 top이 현가 근처인지
    spot = _spot_of(snap)
    near_put = False
    if spot:
        for row in (m.get("top_put_volume") or [])[:3]:
            try:
                s = float(row["strike"])
                if abs(s - spot) / spot <= 0.03:
                    near_put = True
                    break
            except (TypeError, ValueError, KeyError):
                continue
    if near_put:
        bits.append("근거리 풋 거래 집중")
    near_call = False
    if spot:
        for row in (m.get("top_call_volume") or [])[:3]:
            try:
                s = float(row["strike"])
                if abs(s - spot) / spot <= 0.03:
                    near_call = True
                    break
            except (TypeError, ValueError, KeyError):
                continue
    if near_call and not near_put:
        bits.append("근거리 콜 거래 집중(방향≠매수)")
    if snap.get("oi_stale_raw"):
        bits.append("OI 미갱신")
    if not bits:
        return "옵션: 특이 없음"
    return "옵션: " + " · ".join(bits)


def compute_regime_for_ticker(
    ticker: str,
    *,
    asof: str | None = None,
    today_snap: dict | None = None,
) -> dict[str, Any]:
    series = load_price_series(ticker, asof=asof)
    # today_snap 종가가 시리즈에 없으면 추가
    if today_snap:
        d = str(today_snap.get("date") or asof or "")
        px = _spot_of(today_snap)
        if d and px is not None and (not series or series[-1][0] != d):
            series = [r for r in series if r[0] < d] + [(d, px)]
    closes = [c for _, c in series]
    info = classify_regime(closes)
    info["ticker"] = ticker.upper()
    info["asof"] = asof or (series[-1][0] if series else None)
    info["n_closes"] = len(closes)
    if today_snap:
        info["option_line"] = option_status_line(today_snap)
        prev = today_snap.get("previous_close")
        spot = _spot_of(today_snap)
        if prev and spot:
            try:
                info["day_return_pct"] = round(
                    (float(spot) - float(prev)) / float(prev) * 100, 2
                )
            except (TypeError, ValueError, ZeroDivisionError):
                info["day_return_pct"] = None
        else:
            info["day_return_pct"] = (today_snap.get("metrics") or {}).get(
                "price_change_pct"
            )
    else:
        info["option_line"] = "옵션: -"
        info["day_return_pct"] = None
    return info


def drawdown_bucket(dd: float | None) -> str:
    if dd is None:
        return "na"
    if dd <= -20:
        return "dd20+"
    if dd <= -10:
        return "dd10"
    if dd <= -5:
        return "dd5"
    return "dd0"


def empirical_forward(
    ticker: str,
    *,
    asof: str,
    regime: str,
    dd_bucket: str,
    fwd_days: int = FWD_DAYS,
) -> dict[str, Any]:
    """동일 레짐+낙폭 버킷에서 다음 fwd_days 스냅 수익률 빈도."""
    series = load_price_series(ticker, asof=asof)
    if len(series) < LOOKBACK + fwd_days + 2:
        return {"available": False, "n": 0, "reason": "이력 부족"}

    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    hits_down = 0
    hits_up = 0
    rets: list[float] = []

    # asof 이전 구간만 walk (미래 누수 방지)
    try:
        end_i = dates.index(asof)
    except ValueError:
        end_i = len(dates) - 1

    for i in range(LOOKBACK - 1, end_i - fwd_days + 1):
        window_closes = closes[: i + 1]
        reg = classify_regime(window_closes)
        if not reg.get("available"):
            continue
        if reg["regime"] != regime:
            continue
        if drawdown_bucket(reg.get("drawdown_pct")) != dd_bucket:
            continue
        c0 = closes[i]
        c1 = closes[i + fwd_days]
        if not c0:
            continue
        r = (c1 - c0) / c0 * 100
        rets.append(r)
        if r < -0.5:
            hits_down += 1
        elif r > 0.5:
            hits_up += 1

    n = len(rets)
    if n < MIN_FREQ_SAMPLES:
        return {
            "available": False,
            "n": n,
            "reason": f"표본 {n} < {MIN_FREQ_SAMPLES}",
            "fwd_days": fwd_days,
            "regime": regime,
            "dd_bucket": dd_bucket,
        }
    avg = sum(rets) / n
    return {
        "available": True,
        "n": n,
        "fwd_days": fwd_days,
        "regime": regime,
        "dd_bucket": dd_bucket,
        "pct_down": round(100 * hits_down / n, 1),
        "pct_up": round(100 * hits_up / n, 1),
        "pct_flat": round(100 * (n - hits_down - hits_up) / n, 1),
        "avg_return_pct": round(avg, 2),
        "direction_hint": (
            "하락 편향"
            if hits_down / n >= 0.55
            else ("상승 편향" if hits_up / n >= 0.55 else "혼조·보류")
        ),
    }


def spy_sync_flag(ticker_regime: dict, spy_regime: dict | None) -> str | None:
    if not spy_regime or not spy_regime.get("available"):
        return None
    tr = ticker_regime.get("regime")
    sr = spy_regime.get("regime")
    if tr in ("downtrend", "crash") and sr in ("downtrend", "crash"):
        return "SPY도 하락 동조"
    if tr == "uptrend" and sr == "uptrend":
        return "SPY도 상승 동조"
    if tr in ("downtrend", "crash") and sr == "uptrend":
        return "개별 약세 · 시장은 상대 강함"
    if tr == "uptrend" and sr in ("downtrend", "crash"):
        return "개별 상대 강세 · 시장은 약함"
    return None


# ---- SPY 수집 ----

def _market_dir() -> Path:
    import config

    here = Path(__file__).resolve().parent
    base = Path(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else here / base
    d = base / "_market"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_and_save_spy(date: str | None = None, *, save: bool = True) -> dict[str, Any]:
    """SPY 최근 종가 + 레짐. snapshots/_market/SPY/{date}.json."""
    import yfinance as yf

    t = yf.Ticker("SPY")
    hist = t.history(period="60d")
    if hist is None or hist.empty:
        return {"available": False, "ticker": "SPY"}
    closes = [float(x) for x in hist["Close"].dropna().tolist()]
    dates = [d.date().isoformat() if hasattr(d, "date") else str(d)[:10] for d in hist["Close"].dropna().index]
    asof = date or dates[-1]
    # align
    pairs = list(zip(dates, closes))
    pairs = [p for p in pairs if p[0] <= asof]
    info = classify_regime([c for _, c in pairs])
    info["ticker"] = "SPY"
    info["asof"] = pairs[-1][0] if pairs else asof
    info["spot"] = pairs[-1][1] if pairs else None
    info["available"] = bool(pairs) and info.get("available", True)
    if save and pairs:
        path = _market_dir() / "SPY" / f"{info['asof']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": "SPY",
            "date": info["asof"],
            "regular_close": info["spot"],
            "spot": info["spot"],
            "regime": info,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        info["path"] = str(path)
    return info


def load_spy_regime(asof: str | None = None) -> dict[str, Any] | None:
    d = _market_dir() / "SPY"
    if not d.exists():
        return None
    files = sorted(d.glob("????-??-??.json"))
    if asof:
        files = [p for p in files if p.stem <= asof]
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("regime") or data


# ---- 일일 브리프 ----

def format_daily_brief(
    ticker: str,
    date: str,
    regime: dict,
    *,
    spy_regime: dict | None = None,
    event_tail: str | None = None,
) -> str:
    L: list[str] = []
    L.append(f"📌 {ticker} 일일 브리프 · {date}")
    if not regime.get("available"):
        L.append("· 레짐: 데이터 부족")
        L.append("· 방향 판단 보류 · 주간 리포트 참고")
        return "\n".join(L)

    L.append(f"· 레짐: {regime.get('regime_ko')} (20일 기울기 {regime.get('slope_pct')}%)")
    ret = regime.get("day_return_pct")
    if ret is not None:
        L.append(f"· 전일비 {ret:+.1f}% · 종가 참고 {_fmt(regime.get('last'))}")
    else:
        L.append(f"· 종가 참고 {_fmt(regime.get('last'))}")
    L.append(f"· {regime.get('option_line') or '옵션: -'}")

    flags = list(regime.get("flags") or [])
    sync = spy_sync_flag(regime, spy_regime)
    if sync:
        flags.append(sync)
    for f in flags[:3]:
        L.append(f"· ⚠ {f}")

    if regime.get("regime") == "sideways" and not flags:
        L.append("· 방향 판단 보류 · 주간 리포트 참고")
    elif regime.get("regime") in ("downtrend", "crash"):
        L.append("· 하락 레짐 유지 중 — 일일 미세등락으로 방향 뒤집지 말 것")
    elif regime.get("regime") == "uptrend":
        L.append("· 상승 레짐 — 세부 방향은 주간 리포트")

    if event_tail and event_tail.strip():
        L.append("")
        L.append("── 이벤트 메모 ──")
        # 이벤트 본문이 길면 앞부분만
        tail = event_tail.strip()
        if len(tail) > 900:
            tail = tail[:900] + "…"
        L.append(tail)
    return "\n".join(L)


def _fmt(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return "-"


# ---- 주간 방향 ----

def _week_price_facts(ticker: str, monday: str, friday: str) -> dict[str, Any]:
    snaps = snapshot_store.list_snapshots_between(ticker, monday, friday)
    series = _closes_from_snaps(snaps)
    if len(series) < 2:
        # fallback full series slice
        full = load_price_series(ticker, asof=friday)
        series = [r for r in full if monday <= r[0] <= friday]
    if not series:
        return {"available": False}
    opens = series[0][1]
    close = series[-1][1]
    hi = max(c for _, c in series)
    lo = min(c for _, c in series)
    ret = (close - opens) / opens * 100 if opens else None
    # 주 시작 전 레짐 vs 주말 레짐
    before = load_price_series(ticker, asof=monday)
    # exclude monday itself for "start" — use through previous
    start_closes = [c for d, c in before if d < monday]
    end_closes = [c for _, c in load_price_series(ticker, asof=friday)]
    start_reg = classify_regime(start_closes) if len(start_closes) >= 5 else {}
    end_reg = classify_regime(end_closes) if len(end_closes) >= 5 else {}
    flipped = (
        start_reg.get("regime")
        and end_reg.get("regime")
        and start_reg.get("regime") != end_reg.get("regime")
    )
    return {
        "available": True,
        "open": opens,
        "close": close,
        "high": hi,
        "low": lo,
        "return_pct": round(ret, 2) if ret is not None else None,
        "n_days": len(series),
        "start_regime": start_reg.get("regime_ko"),
        "end_regime": end_reg.get("regime_ko"),
        "end_regime_code": end_reg.get("regime"),
        "end_drawdown_pct": end_reg.get("drawdown_pct"),
        "end_slope_pct": end_reg.get("slope_pct"),
        "end_below_ma": end_reg.get("below_ma"),
        "end_ma": end_reg.get("ma"),
        "flipped": flipped,
        "end_reg": end_reg,
    }


def strong_option_lessons(ticker: str, monday: str, friday: str) -> list[str]:
    """option_events.jsonl 에서 주간 강한 라벨만."""
    try:
        import option_events as oe
    except ImportError:
        return []
    strong = {
        "breakout_success",
        "breakout_fail",
        "support_success",
        "support_fail",
        "approach_reverse",
        "level_pass",
    }
    lines: list[str] = []
    for r in oe.load_event_records(ticker, limit=500):
        od = str(r.get("outcome_date") or "")
        if not (monday <= od <= friday):
            continue
        res = oe._norm_result(r.get("result"), r.get("side"))  # noqa: SLF001
        if res not in strong:
            continue
        side = "콜" if str(r.get("side")).upper() == "CALL" else "풋"
        ko = oe.RESULT_KO.get(res, res)
        lines.append(f"{od} {_fmt(r.get('strike'))} {side} → {ko}")
        if len(lines) >= 5:
            break
    return lines


def invalidation_lines(facts: dict) -> list[str]:
    out: list[str] = []
    ma = facts.get("end_ma")
    regime = facts.get("end_regime_code")
    dd = facts.get("end_drawdown_pct")
    if regime in ("downtrend", "crash") and ma:
        out.append(f"무효화: 종가가 20일 평균({_fmt(ma)}) 위에서 마감")
    if regime == "uptrend" and ma:
        out.append(f"무효화: 종가가 20일 평균({_fmt(ma)}) 아래 마감")
    if dd is not None and dd <= -10:
        out.append(f"주의 지속: 고점 대비 낙폭이 {dd:.0f}%에서 더 확대")
    if not out:
        out.append("무효화: 주간 고저 바깥으로 추세 전환 시 재평가")
    return out[:2]


def format_weekly_direction(
    ticker: str,
    week_ending: str,
    monday: str,
    friday: str,
    *,
    spy_regime: dict | None = None,
    partial: bool = False,
) -> tuple[str, dict]:
    """주간 방향 본문 + 구조화 dict."""
    facts = _week_price_facts(ticker, monday, friday)
    L: list[str] = []
    L.append(f"🧭 {ticker} 주간 방향 · {week_ending}")
    if partial:
        L.append("※ 부분 주간 데이터")
    if not facts.get("available"):
        L.append("· 이번 주 가격 스냅 부족 — 방향 판단 보류")
        return "\n".join(L), {"available": False}

    # 1) 가격 사실
    L.append("")
    L.append("① 이번 주 가격")
    L.append(
        f"· {facts['n_days']}일 · "
        f"{_fmt(facts['open'])}→{_fmt(facts['close'])} "
        f"({facts['return_pct']:+.1f}%) · "
        f"고 {_fmt(facts['high'])} / 저 {_fmt(facts['low'])}"
    )
    if facts.get("flipped"):
        L.append(
            f"· 추세 전환: {facts.get('start_regime')} → {facts.get('end_regime')}"
        )
    else:
        L.append(
            f"· 레짐: {facts.get('end_regime')} "
            f"(기울기 {facts.get('end_slope_pct')}% · "
            f"고점대비 {facts.get('end_drawdown_pct')}%)"
        )

    # 2) 시장
    L.append("")
    L.append("② 시장 맥락")
    if spy_regime and spy_regime.get("available"):
        L.append(
            f"· SPY: {spy_regime.get('regime_ko')} "
            f"(기울기 {spy_regime.get('slope_pct')}%)"
        )
        sync = spy_sync_flag(
            {"regime": facts.get("end_regime_code")}, spy_regime
        )
        L.append(f"· {sync}" if sync else "· 동조 특이 없음")
    else:
        L.append("· SPY 레짐 없음 — 개별만 판단")

    # 3) 동조건 빈도
    L.append("")
    L.append("③ 방향 판단 (과거 동조건 빈도)")
    end_reg = facts.get("end_reg") or {}
    freq = empirical_forward(
        ticker,
        asof=friday,
        regime=str(facts.get("end_regime_code") or "sideways"),
        dd_bucket=drawdown_bucket(facts.get("end_drawdown_pct")),
    )
    if facts.get("end_regime_code") == "sideways":
        L.append("· 횡보 레짐 — 방향 판단 보류 (억지 방향 없음)")
        L.append("· 다음 주 추세 기울기·낙폭 변화만 추적")
    elif not freq.get("available"):
        L.append(
            f"· 표본 부족 (n={freq.get('n', 0)}) — 방향 판단 보류"
        )
        if end_reg.get("regime") in ("downtrend", "crash"):
            L.append("· 다만 현재 가격 레짐은 하락 쪽 — 반등 단정 금지")
        elif end_reg.get("regime") == "uptrend":
            L.append("· 다만 현재 가격 레짐은 상승 쪽 — 과열 단정 금지")
    else:
        L.append(
            f"· 동일 레짐·낙폭 버킷 이후 {freq['fwd_days']}거래일: "
            f"하락 {freq['pct_down']}% / 상승 {freq['pct_up']}% "
            f"(표본 {freq['n']})"
        )
        L.append(
            f"· 평균 수익률 {freq['avg_return_pct']:+.1f}% → {freq['direction_hint']}"
        )
        L.append("· ※ 과거 빈도이며 미래 확률이 아님")

    # 4) 무효화
    L.append("")
    L.append("④ 무효화 조건")
    for line in invalidation_lines(facts):
        L.append(f"· {line}")

    # 5) 옵션 학습
    L.append("")
    L.append("⑤ 이번 주 옵션에서 확인된 것")
    lessons = strong_option_lessons(ticker, monday, friday)
    if lessons:
        for x in lessons:
            L.append(f"· {x}")
    else:
        L.append("· 강한 옵션↔가격 매칭 기록 없음")

    L.append("")
    L.append("⚠️ 관측·빈도 기록 · 투자 조언 아님")
    meta = {
        "available": True,
        "facts": facts,
        "freq": freq,
        "spy": spy_regime,
        "lessons": lessons,
    }
    return "\n".join(L), meta
