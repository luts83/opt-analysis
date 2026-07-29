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
    if actual_high >= resistance:
        label = f"저항선 돌파! (실제 고가 ${actual_high:g} ≥ 예상 ${resistance:g})"
        score = 100
    elif actual_high >= resistance * 0.98:
        label = f"저항선 근접 (고가가 예상의 {actual_high/resistance:.0%} 도달)"
        score = 70
    else:
        gap = (resistance - actual_high) / resistance * 100
        label = f"저항선 미달 ({gap:.1f}% 못 미침)"
        # 미달은 실패로 취급 — 근접 점수 상한 30
        score = min(_proximity_score(resistance, actual_high), 30)
    return {"predicted": resistance, "actual_high": actual_high, "score": score, "label": label}


def support_result(support, actual_low) -> dict | None:
    if support is None:
        return None
    if actual_low <= support:
        label = f"지지선 이탈 (실제 저가 ${actual_low:g} ≤ 예상 ${support:g})"
        score = 0  # 이탈 = 실패
    elif actual_low <= support * 1.02:
        label = f"지지선 근접 (저가가 예상 지지 부근에서 방어)"
        score = 85
    else:
        gap = (actual_low - support) / support * 100
        label = f"지지선 여유 ({gap:.1f}% 위에서 마감)"
        score = max(70, _proximity_score(support, actual_low))
    return {"predicted": support, "actual_low": actual_low, "score": score, "label": label}


def direction_result(sentiment, weekly_return) -> dict:
    # 특수 라벨은 방향 단정으로 보지 않음
    if sentiment in ("반등 시도 국면", "양방향 극단 베팅", "차익실현/헤지 국면"):
        match = abs(weekly_return) >= 2  # 큰 움직임이 있으면 '국면 인식' 성공 쪽
        # 학습용: 특수 라벨은 방향 PASS/FAIL보다 중립에 가깝게
        score = 60 if match else 40
        verdict = "특수국면 인식" if match else "특수국면(움직임 작음)"
    elif sentiment == "강세":
        match = weekly_return > 0
        score = 100 if match else 0
        verdict = "방향 일치" if match else "방향 불일치"
    elif sentiment == "약세":
        match = weekly_return < 0
        score = 100 if match else 0
        verdict = "방향 일치" if match else "방향 불일치"
    else:  # 중립
        match = abs(weekly_return) < 2
        score = 100 if match else 40
        verdict = "방향 일치" if match else "방향 불일치"
    return {
        "predicted_sentiment": sentiment,
        "weekly_return_pct": weekly_return,
        "match": match if sentiment in ("강세", "약세", "중립") else True,
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
    if score >= 75:
        return "C+"
    if score >= 70:
        return "C"
    if score >= 65:
        return "C-"
    if score >= 50:
        return "D"
    return "F"


def _item_passed(key: str, res: dict | None) -> bool | None:
    if res is None:
        return None
    if key == "band":
        return bool(res.get("contained"))
    if key == "direction":
        return bool(res.get("match"))
    if key == "support":
        al, ps = res.get("actual_low"), res.get("predicted")
        if al is None or ps is None:
            return None
        return al > ps  # 이탈하지 않으면 PASS
    if key == "resistance":
        ah, pr = res.get("actual_high"), res.get("predicted")
        if ah is None or pr is None:
            return None
        return ah >= pr  # 도달/돌파만 PASS (미달은 FAIL)
    return None


def composite_grade(band, direction, resistance, support) -> dict:
    """가중 점수 + 실패 개수 상한(전부 실패면 F≤45)."""
    parts = {
        "band": band,
        "direction": direction,
        "resistance": resistance,
        "support": support,
    }
    total_w = 0.0
    acc = 0.0
    passes = 0
    fails = 0
    band_passed = False
    for key, res in parts.items():
        if res is None:
            continue
        w = _WEIGHTS[key]
        # 밴드 실패 시 부분점수 상한
        sc = res["score"]
        if key == "band" and not res.get("contained"):
            sc = min(sc, 35)
        acc += sc * w
        total_w += w
        ok = _item_passed(key, res)
        if ok is True:
            passes += 1
            if key == "band":
                band_passed = True
        elif ok is False:
            fails += 1
    score = round(acc / total_w) if total_w else 0

    # 루브릭: 전부 실패 ≤45(F), 3실패+밴드성공 ≈75(C+), 3실패 그 외 60대(C-)
    if fails >= 4 or (passes == 0 and fails >= 3):
        score = min(score, 45)
    elif fails == 3:
        if band_passed:
            score = 75  # C+ 부근
        else:
            score = min(max(score, 60), 68)

    return {"score": score, "grade": _grade_letter(score), "passes": passes, "fails": fails}


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
