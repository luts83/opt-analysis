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


def _get_spot_and_prevclose(t: yf.Ticker) -> tuple[float, float | None, str]:
    """현재가와 전일 종가를 반환한다. (.info 미사용)"""
    spot = None
    prev_close = None
    note = ""

    try:
        fi = t.fast_info
        p = fi["lastPrice"]
        if p and p > 0:
            spot = float(p)
            note = "fast_info.lastPrice"
        try:
            prev_close = float(fi["previousClose"])
        except Exception:
            prev_close = None
    except Exception:
        pass

    if spot is None:
        # 폴백: 최근 종가 2일치
        hist = t.history(period="5d")
        closes = hist["Close"].dropna().tolist()
        if closes:
            spot = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) >= 2 else None
            note = "history.close"

    if spot is None:
        raise RuntimeError("현재가를 가져오지 못했습니다.")

    return spot, prev_close, note


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
    spot, prev_close, note = _get_spot_and_prevclose(t)
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
        "spot": round(spot, 2),
        "previous_close": round(prev_close, 2) if prev_close else None,
        "spot_source": note,
        "expiries": expiry_data,  # role -> {date, calls[], puts[]}
    }
