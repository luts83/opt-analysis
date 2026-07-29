"""yfinance 로 옵션 체인 + 현재가 수집.

검증 단계 주의사항:
- ticker.info 는 타임아웃이 잦아 사용하지 않는다.
- 현재가는 fast_info['lastPrice'] → history() 종가 순으로 폴백.
- previous_close 는 fast_info 를 신뢰하지 않고 history[-2] 로 확정한다.
  (리포트 실행 시각이 ET 새벽이면 yfinance previousClose 가 밀릴 수 있음)
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import config
from expiry_selector import select_expiries

_ET = ZoneInfo("America/New_York")


def _fi_float(fi, *keys: str) -> float | None:
    """fast_info 에서 키를 item/attr 양쪽 방식으로 안전하게 읽는다."""
    for k in keys:
        v = None
        try:
            v = fi[k]
        except Exception:
            try:
                v = getattr(fi, k, None)
            except Exception:
                v = None
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _last_trade_date() -> dt.date:
    """ET 기준 가장 최근 거래일(오늘이 장중이면 오늘, 아니면 직전 영업일)."""
    now_et = dt.datetime.now(_ET)
    d = now_et.date()
    h = now_et.hour
    # 아직 정규장(09:30) 전이면 전 거래일 데이터가 최신
    if h < 4:
        d -= dt.timedelta(days=1)
    # 주말 보정
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _get_price_context(t: yf.Ticker) -> dict:
    """정규장 종가 + 프리마켓 최근가를 함께 반환.

    핵심 원칙:
    - regular_close = history 마지막 Close (가장 최근 거래일 종가)
    - previous_close = history 끝에서 2번째 Close (전전 거래일)
    - pre_market_price = 오늘(ET) 프리마켓 구간(04:00~09:30) 최근가만
    - after_market_price = 사용하지 않음 (리포트가 다음날 새벽에 발행되므로 무의미)
    """
    regular_close = None
    prev_close = None
    extended = None
    pre_market = None
    session = "regular"

    # 1) fast_info 로 정규장가 빠르게 취득 (history NaN 버그 대비)
    try:
        fi = t.fast_info
        regular_close = _fi_float(fi, "lastPrice", "last_price")
    except Exception:
        pass

    # 2) 일봉 history 로 regular_close 보완 + previous_close 확정
    hist_closes: list[float] = []
    try:
        hist = t.history(period="10d")
        closes = hist["Close"].dropna()
        if not closes.empty:
            hist_closes = [float(c) for c in closes]
            hist_last = hist_closes[-1]
            if regular_close is None:
                regular_close = hist_last
            elif abs(hist_last - regular_close) / max(regular_close, 1e-9) < 0.005:
                pass  # fast_info 와 history 일치
            # previous_close 는 항상 history[-2] (밀리지 않는 확실한 값)
            if len(hist_closes) >= 2:
                prev_close = hist_closes[-2]
    except Exception:
        pass

    # 3) 프리마켓 최근가만 추출 (오늘 ET 날짜에 한정)
    today_et = dt.datetime.now(_ET).date()
    try:
        ext = t.history(period="5d", interval="1h", prepost=True)
        if ext is not None and not ext.empty:
            df = ext.copy().dropna(subset=["Close"])
            if not df.empty:
                idx = pd.DatetimeIndex(df.index)
                if idx.tz is None:
                    idx = idx.tz_localize("UTC").tz_convert("America/New_York")
                else:
                    idx = idx.tz_convert("America/New_York")
                df.index = idx

                # extended = prepost 전체 마지막 (spot 결정용)
                last_val = float(df["Close"].iloc[-1])
                if last_val > 0:
                    extended = last_val

                # 오늘(ET) 프리마켓만 (04:00~09:30)
                today_mask = df.index.date == today_et
                mins = df.index.hour * 60 + df.index.minute
                pre_mask = today_mask & (mins >= 4 * 60) & (mins < 9 * 60 + 30)
                pre_rows = df.loc[pre_mask, "Close"].dropna()
                if not pre_rows.empty:
                    pre_market = float(pre_rows.iloc[-1])
    except Exception:
        pass

    if regular_close is None and extended is None:
        raise RuntimeError("현재가를 가져오지 못했습니다.")

    import market_clock

    market_session = market_clock.get_market_session()

    # 분석 기준가(spot) 결정
    if market_session in ("premarket", "afterhours") and extended is not None:
        if regular_close is not None:
            gap_pct = abs(extended - regular_close) / max(regular_close, 1e-9) * 100
            if gap_pct >= 0.05:
                spot = extended
                session = "extended"
                note = f"extended({market_session})"
            else:
                spot = regular_close
                session = "regular"
                note = "regular.close"
        else:
            spot = extended
            session = "extended"
            note = f"extended({market_session})"
    elif market_session == "regular":
        spot = regular_close if regular_close is not None else extended
        session = "regular"
        note = "regular.live"
    else:  # closed
        spot = regular_close if regular_close is not None else (
            extended if extended is not None else prev_close
        )
        session = "regular"
        note = "regular.close"

    if spot is None:
        raise RuntimeError("현재가를 가져오지 못했습니다.")

    vs_regular = None
    if extended is not None and regular_close is not None:
        vs_regular = round((extended - regular_close) / regular_close * 100, 2)

    return {
        "spot": round(float(spot), 2),
        "previous_close": round(prev_close, 2) if prev_close else None,
        "regular_close": round(regular_close, 2) if regular_close else None,
        "extended_price": round(extended, 2) if extended else None,
        "extended_vs_regular_pct": vs_regular,
        "session": session,
        "market_session": market_session,
        "spot_source": note,
        "pre_market_price": round(float(pre_market), 2) if pre_market else None,
        "after_market_price": None,  # 리포트 시점에 무의미 → 항상 None
    }


def _extract_rows(df: pd.DataFrame) -> list[dict]:
    """옵션 체인 DataFrame 에서 필요한 필드만 dict 리스트로 추출."""
    if df is None or df.empty:
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        row = {}
        for f in config.OPTION_FIELDS:
            v = r.get(f)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                v = 0 if f in ("volume", "openInterest") else None
            elif f in ("volume", "openInterest"):
                v = int(v)
            else:
                v = float(v)
            row[f] = v
        rows.append(row)
    return rows


def fetch_ticker(ticker: str) -> dict:
    """한 종목의 3개 만기 옵션 체인 + 현재가를 dict 로 반환한다."""
    t = yf.Ticker(ticker)
    px = _get_price_context(t)
    spot = px["spot"]
    expiries = select_expiries(ticker)

    expiry_data: dict[str, dict] = {}
    for role, exp in expiries.items():
        oc = t.option_chain(exp)
        expiry_data[role] = {
            "date": exp,
            "calls": _extract_rows(oc.calls),
            "puts": _extract_rows(oc.puts),
        }

    # 리포트 날짜 = ET 기준 가장 최근 거래일
    report_date = _last_trade_date().isoformat()

    return {
        "ticker": ticker,
        "date": report_date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "spot": spot,
        "previous_close": px["previous_close"],
        "regular_close": px["regular_close"],
        "extended_price": px["extended_price"],
        "extended_vs_regular_pct": px["extended_vs_regular_pct"],
        "session": px["session"],
        "market_session": px["market_session"],
        "spot_source": px["spot_source"],
        "pre_market_price": px.get("pre_market_price"),
        "after_market_price": None,
        "expiries": expiry_data,
    }
