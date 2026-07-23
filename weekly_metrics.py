"""주간 검증(백테스트) 계산.

그 주 '월요일(또는 그 주 첫) 예측' 을 실제 주간 OHLC 와 비교해
항목별 정확도 점수와 종합 성적(A~F)을 낸다.
"""
from __future__ import annotations

import datetime as dt


# ------------------------------------------------------------------ #
# 예측 추출 / 실제 OHLC
# ------------------------------------------------------------------ #

def extract_prediction(snap: dict) -> dict:
    """일일 스냅샷에서 '이번주(this_week)' 예측값을 뽑는다."""
    m = snap.get("metrics", {})
    em = m.get("expiry_metrics", {}).get("this_week") or {}
    st = em.get("straddle") or {}
    call_clusters = em.get("call_oi_clusters") or []
    put_clusters = em.get("put_oi_clusters") or []
    return {
        "from_date": snap.get("date"),
        "expiry": em.get("date"),
        "spot": snap.get("spot"),
        "band_lower": st.get("lower"),
        "band_upper": st.get("upper"),
        "band_pct": st.get("band_pct"),
        "resistance": call_clusters[0]["strike"] if call_clusters else None,
        "support": put_clusters[0]["strike"] if put_clusters else None,
        "sentiment": m.get("sentiment"),
    }


def weekly_ohlc(ticker: str, monday: dt.date, friday: dt.date) -> dict | None:
    """실제 주간 OHLC (월~금 집계)."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    hist = t.history(
        start=monday.isoformat(),
        end=(friday + dt.timedelta(days=1)).isoformat(),
    )
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return None
    return {
        "open": round(float(hist["Open"].iloc[0]), 2),
        "high": round(float(hist["High"].max()), 2),
        "low": round(float(hist["Low"].min()), 2),
        "close": round(float(hist["Close"].iloc[-1]), 2),
        "days": int(len(hist)),
        "return_pct": round(
            (float(hist["Close"].iloc[-1]) - float(hist["Open"].iloc[0]))
            / float(hist["Open"].iloc[0])
            * 100,
            2,
        ),
    }


# ------------------------------------------------------------------ #
# 항목별 정확도
# ------------------------------------------------------------------ #

def band_result(pl, pu, al, ah) -> dict | None:
    if pl is None or pu is None or pu <= pl:
        return None
    contained = al >= pl and ah <= pu
    prange = pu - pl
    arange = ah - al
    ratio = arange / prange if prange else None
    if contained:
        label = "정확 (실제 범위가 예상 밴드 안)"
        score = 100
    else:
        overshoot = max(0, pl - al) + max(0, ah - pu)
        score = max(0, round(100 - overshoot / prange * 100))
        if ratio and ratio > 1.2:
            label = f"과소평가 (실제 변동폭이 예상의 {ratio:.0%})"
        elif ratio and ratio < 0.8:
            label = f"과대평가 (실제 변동폭이 예상의 {ratio:.0%})"
        else:
            label = f"부분 이탈 (실제/예상 변동폭 {ratio:.0%})"
    return {
        "predicted": [pl, pu],
        "actual": [al, ah],
        "contained": contained,
        "ratio": round(ratio, 2) if ratio else None,
        "score": score,
        "label": label,
    }


def _proximity_score(predicted, actual) -> int:
    """예측가와 실제가가 가까울수록 100에 가깝게 (2%당 4점 감점)."""
    if not predicted:
        return 0
    diff_pct = abs(actual - predicted) / predicted * 100
    return max(0, round(100 - diff_pct * 2))


def resistance_result(resistance, actual_high) -> dict | None:
    if resistance is None:
        return None
    score = _proximity_score(resistance, actual_high)
    if actual_high >= resistance:
        label = f"저항선 돌파! (실제 고가 ${actual_high:g} ≥ 예상 ${resistance:g})"
    elif actual_high >= resistance * 0.98:
        label = f"저항선 근접 (고가가 예상의 {actual_high/resistance:.0%} 도달)"
    else:
        gap = (resistance - actual_high) / resistance * 100
        label = f"저항선 미달 ({gap:.1f}% 못 미침)"
    return {"predicted": resistance, "actual_high": actual_high, "score": score, "label": label}


def support_result(support, actual_low) -> dict | None:
    if support is None:
        return None
    score = _proximity_score(support, actual_low)
    if actual_low <= support:
        label = f"지지선 이탈 (실제 저가 ${actual_low:g} ≤ 예상 ${support:g})"
    elif actual_low <= support * 1.02:
        label = f"지지선 근접 (저가가 예상 지지 부근에서 방어)"
    else:
        gap = (actual_low - support) / support * 100
        label = f"지지선 여유 ({gap:.1f}% 위에서 마감)"
    return {"predicted": support, "actual_low": actual_low, "score": score, "label": label}


def direction_result(sentiment, weekly_return) -> dict:
    if sentiment == "강세":
        match = weekly_return > 0
        score = 100 if match else 0
    elif sentiment == "약세":
        match = weekly_return < 0
        score = 100 if match else 0
    else:  # 중립
        match = abs(weekly_return) < 2
        score = 100 if match else 40
    verdict = "방향 일치" if match else "방향 불일치"
    return {
        "predicted_sentiment": sentiment,
        "weekly_return_pct": weekly_return,
        "match": match,
        "score": score,
        "label": f"{verdict} ({sentiment} 예상, 주간 {weekly_return:+.1f}%)",
    }


# ------------------------------------------------------------------ #
# 종합 성적
# ------------------------------------------------------------------ #

_WEIGHTS = {"band": 0.35, "direction": 0.30, "resistance": 0.175, "support": 0.175}


def _grade_letter(score: float) -> str:
    if score >= 93:
        return "A"
    if score >= 90:
        return "A-"
    if score >= 87:
        return "B+"
    if score >= 83:
        return "B"
    if score >= 80:
        return "B-"
    if score >= 77:
        return "C+"
    if score >= 73:
        return "C"
    if score >= 70:
        return "C-"
    if score >= 60:
        return "D"
    return "F"


def composite_grade(band, direction, resistance, support) -> dict:
    parts = {
        "band": band,
        "direction": direction,
        "resistance": resistance,
        "support": support,
    }
    total_w = 0.0
    acc = 0.0
    for key, res in parts.items():
        if res is None:
            continue
        w = _WEIGHTS[key]
        acc += res["score"] * w
        total_w += w
    score = round(acc / total_w) if total_w else 0
    return {"score": score, "grade": _grade_letter(score)}


def build_weekly(ticker: str, prediction: dict, ohlc: dict) -> dict:
    """항목별 결과 + 종합 성적 dict."""
    band = band_result(
        prediction["band_lower"], prediction["band_upper"], ohlc["low"], ohlc["high"]
    )
    resistance = resistance_result(prediction["resistance"], ohlc["high"])
    support = support_result(prediction["support"], ohlc["low"])
    direction = direction_result(prediction["sentiment"], ohlc["return_pct"])
    grade = composite_grade(band, direction, resistance, support)
    return {
        "band": band,
        "resistance": resistance,
        "support": support,
        "direction": direction,
        "grade": grade,
    }
