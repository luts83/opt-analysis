"""yfinance 로 옵션 체인 + 현재가 수집.

검증 단계 주의사항:
- ticker.info 는 타임아웃이 잦아 사용하지 않는다.
- 현재가는 fast_info['lastPrice'] → history() 종가 순으로 폴백.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import yfinance as yf

import config
from expiry_selector import select_expiries


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


def _get_price_context(t: yf.Ticker) -> dict:
    """정규장 종가 + (가능하면) 애프터/프리마켓 최근가를 함께 반환.

    어닝 직후처럼 장외에서 급변한 경우, lastPrice(정규장)만 쓰면
    실제 시세와 옵션 해석이 어긋난다. extended 가 의미 있게 다르면
    분석 기준가(spot)는 extended 를 우선한다.

    주의: yfinance 일봉 history 가 당일 Close=NaN 인 경우가 있어
    (예: SPCX 2026-07-24), 정규장가는 fast_info.lastPrice 를 우선한다.
    """
    regular_close = None
    prev_close = None
    extended = None
    session = "regular"

    # 1) fast_info 정규장가 우선 (일봉 NaN 버그 회피)
    try:
        fi = t.fast_info
        regular_close = _fi_float(fi, "lastPrice", "last_price")
        prev_close = _fi_float(fi, "previousClose", "previous_close")
    except Exception:
        pass

    # 2) 일봉 history 로 보완 + 직전 거래일 종가
    try:
        hist = t.history(period="10d")
        closes = hist["Close"].dropna()
        if not closes.empty:
            hist_last = float(closes.iloc[-1])
            if regular_close is None:
                regular_close = hist_last
            if len(closes) >= 2:
                # history 마지막이 정규장과 거의 같으면 → 그 이전이 previous
                if (
                    regular_close is not None
                    and abs(hist_last - regular_close) / max(regular_close, 1e-9) < 0.005
                ):
                    prev_close = float(closes.iloc[-2])
                else:
                    # 당일 일봉이 NaN 으로 빠져 history 가 하루 늦은 경우
                    # hist_last 가 곧 직전 거래일 종가
                    prev_close = hist_last
            elif prev_close is None:
                prev_close = hist_last
    except Exception:
        pass

    # 3) 확장장(프리/애프터) 최근 체결
    try:
        ext = t.history(period="5d", interval="1h", prepost=True)
        if ext is not None and not ext.empty:
            last = float(ext["Close"].dropna().iloc[-1])
            if last > 0:
                extended = last
    except Exception:
        pass

    if regular_close is None and extended is None:
        raise RuntimeError("현재가를 가져오지 못했습니다.")

    # 분석 기준가: 확장가가 정규장 대비 1% 이상 다르면 확장가 우선
    if extended is not None and regular_close is not None:
        gap_pct = abs(extended - regular_close) / regular_close * 100
        if gap_pct >= 1.0:
            spot = extended
            session = "extended"
            note = "extended(pre/post)"
        else:
            spot = regular_close
            note = "regular.close"
    elif extended is not None:
        spot = extended
        session = "extended"
        note = "extended(pre/post)"
    else:
        spot = regular_close
        note = "regular.close"

    vs_regular = None
    if extended is not None and regular_close is not None:
        vs_regular = round((extended - regular_close) / regular_close * 100, 2)

    return {
        "spot": round(spot, 2),
        "previous_close": round(prev_close, 2) if prev_close else None,
        "regular_close": round(regular_close, 2) if regular_close else None,
        "extended_price": round(extended, 2) if extended else None,
        "extended_vs_regular_pct": vs_regular,
        "session": session,
        "spot_source": note,
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
            # NaN / None 정리
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
    """한 종목의 3개 만기 옵션 체인 + 현재가를 dict 로 반환한다.

    반환 구조는 snapshot_store 에서 그대로 저장 가능.
    """
    t = yf.Ticker(ticker)
    px = _get_price_context(t)
    spot = px["spot"]
    expiries = select_expiries(ticker)  # {"this_week","next_week","monthly"}

    expiry_data: dict[str, dict] = {}
    for role, exp in expiries.items():
        oc = t.option_chain(exp)
        expiry_data[role] = {
            "date": exp,
            "calls": _extract_rows(oc.calls),
            "puts": _extract_rows(oc.puts),
        }

    return {
        "ticker": ticker,
        "date": dt.date.today().isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "spot": spot,
        "previous_close": px["previous_close"],
        "regular_close": px["regular_close"],
        "extended_price": px["extended_price"],
        "extended_vs_regular_pct": px["extended_vs_regular_pct"],
        "session": px["session"],
        "spot_source": px["spot_source"],
        "expiries": expiry_data,  # role -> {date, calls[], puts[]}
    }
