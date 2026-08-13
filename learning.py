"""일일 자기 학습 피드백 루프.

어제 스냅샷의 예측(밴드/지지/저항/심리)을 오늘 실제 OHLC와 비교해 채점하고,
빗나간 경우 어제 데이터에서 놓친 시그널을 찾아 lesson 을 남긴다.
누적 통계는 LLM 프롬프트에 주입해 다음 리포트를 개선한다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import weekly_metrics as wm

# OI 급변을 '놓친 시그널'로 볼 임계치
_EXTREME_OI_PCT = 1000.0
_HOT_VOI = 5.0


def extract_daily_prediction(snap: dict) -> dict:
    """어제 스냅샷에서 리포트가 강조한 예측값을 뽑는다.

    단기 지지/저항(거래량)을 우선하고, 없으면 강한 OI 레벨 → 클러스터 순.
    """
    base = wm.extract_prediction(snap)
    levels = (snap.get("metrics") or {}).get("levels") or {}

    def _first_strike(*keys: str) -> float | None:
        for k in keys:
            rows = levels.get(k) or []
            if rows and rows[0].get("strike") is not None:
                return float(rows[0]["strike"])
        return None

    support = _first_strike("near_support", "strong_support") or base.get("support")
    resistance = _first_strike("near_resistance", "strong_resistance") or base.get(
        "resistance"
    )
    return {
        **base,
        "support": support,
        "resistance": resistance,
        "near_support": _first_strike("near_support"),
        "near_resistance": _first_strike("near_resistance"),
        "strong_support": _first_strike("strong_support"),
        "strong_resistance": _first_strike("strong_resistance"),
    }


def daily_ohlc(ticker: str, day: dt.date | None = None) -> dict | None:
    """해당일 OHLC. Close=NaN 이면 fast_info / 전일 종가 폴백."""
    import yfinance as yf

    day = day or dt.date.today()
    t = yf.Ticker(ticker)
    hist = t.history(
        start=day.isoformat(),
        end=(day + dt.timedelta(days=1)).isoformat(),
    )
    o = h = l = c = None
    if hist is not None and not hist.empty:
        try:
            o = float(hist["Open"].dropna().iloc[0]) if not hist["Open"].dropna().empty else None
        except Exception:
            o = None
        try:
            h = float(hist["High"].dropna().max()) if not hist["High"].dropna().empty else None
        except Exception:
            h = None
        try:
            l = float(hist["Low"].dropna().min()) if not hist["Low"].dropna().empty else None
        except Exception:
            l = None
        try:
            c = float(hist["Close"].dropna().iloc[-1]) if not hist["Close"].dropna().empty else None
        except Exception:
            c = None

    if c is None:
        try:
            fi = t.fast_info
            for k in ("lastPrice", "last_price"):
                try:
                    v = fi[k]
                    if v and float(v) > 0:
                        c = float(v)
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if c is None and o is None:
        return None

    # 장중이면 high/low 가 부분적일 수 있음
    if h is None and c is not None:
        h = c
    if l is None and c is not None:
        l = c
    if o is None and c is not None:
        o = c

    ret = None
    if o and c and o != 0:
        ret = round((c - o) / o * 100, 2)

    return {
        "date": day.isoformat(),
        "open": round(o, 2) if o is not None else None,
        "high": round(h, 2) if h is not None else None,
        "low": round(l, 2) if l is not None else None,
        "close": round(c, 2) if c is not None else None,
        "return_pct": ret,
    }


def find_missed_signals(prev_snap: dict, results: dict) -> list[str]:
    """빗나간 항목이 있을 때 어제 스냅샷에서 놓친 강한 시그널을 찾는다.

    결과는 '놓친 강세 신호'가 아니라 '주목했어야 할 이상치 + 실제 결과'로 표기한다.
    """
    missed: list[str] = []
    band = results.get("band") or {}
    support = results.get("support") or {}
    direction = results.get("direction") or {}

    failed = False
    if band and not band.get("contained"):
        failed = True
    if support and support.get("actual_low") is not None and support.get("predicted"):
        if support["actual_low"] <= support["predicted"]:
            failed = True
    if direction and not direction.get("match"):
        failed = True
    if not failed:
        return missed

    ret = direction.get("weekly_return_pct")
    prefer_put = False
    if support and support.get("actual_low") is not None and support.get("predicted"):
        if support["actual_low"] <= support["predicted"]:
            prefer_put = True
    if ret is not None and float(ret) < -1:
        prefer_put = True

    def _outcome_note(opt_type: str) -> str:
        if ret is None:
            return ""
        r = float(ret)
        t = str(opt_type).upper()
        if r <= -2 and t in ("CALL", "C", "콜"):
            return f" → 결과: 상승 아닌 급락({r:+.1f}%) · 콜 매도/헤지 물량 가능"
        if r >= 2 and t in ("PUT", "P", "풋"):
            return f" → 결과: 하락 아닌 상승({r:+.1f}%) · 풋 매도/헤지 가능"
        if r <= -2:
            return f" → 결과: 급락({r:+.1f}%)"
        if r >= 2:
            return f" → 결과: 상승({r:+.1f}%)"
        return f" → 결과: {r:+.1f}%"

    for a in prev_snap.get("anomalies") or []:
        chg = a.get("change_pct")
        if chg is None:
            continue
        if abs(float(chg)) >= _EXTREME_OI_PCT:
            msg = a.get("message") or f"OI 급변 {chg:+.0f}%"
            missed.append(msg + _outcome_note(a.get("option_type") or ""))

    voi_puts: list[str] = []
    voi_calls: list[str] = []
    for r in (prev_snap.get("metrics") or {}).get("top_voi") or []:
        voi = r.get("voi")
        if voi is None or float(voi) < _HOT_VOI:
            continue
        line = (
            f"{r.get('expiry')} {r.get('type')} ${r.get('strike'):g} "
            f"V/OI {voi} ({r.get('class')})"
            + _outcome_note(r.get("type") or "")
        )
        if str(r.get("type", "")).upper() in ("PUT", "P", "풋"):
            voi_puts.append(line)
        else:
            voi_calls.append(line)
    if prefer_put:
        missed.extend(voi_puts)
        missed.extend(voi_calls)
    else:
        missed.extend(voi_calls)
        missed.extend(voi_puts)

    seen: set[str] = set()
    out: list[str] = []
    for m in missed:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
        if len(out) >= 5:
            break
    return out


def build_lesson(missed: list[str], results: dict) -> str | None:
    if not missed and not results:
        return None
    band = results.get("band") or {}
    support = results.get("support") or {}
    direction = results.get("direction") or {}
    ret = direction.get("weekly_return_pct")
    tips: list[str] = []
    if any("OI" in m and ("+" in m or "유입" in m) for m in missed):
        tips.append("미결제약정(OI)이 하루 만에 크게 늘면 그 가격대를 최우선으로 언급")
    call_extreme_but_drop = any(
        "CALL" in m.upper() and "V/OI" in m and "급락" in m for m in missed
    )
    if call_extreme_but_drop:
        tips.append("콜 V/OI 극단≠강세 — 급락 시 콜 매도 물량 가능성부터 검토")
    elif any("V/OI" in m for m in missed):
        tips.append("거래량/미결제 비율이 극단인 계약은 '샀는지/팔았는지' 해석과 함께 언급")
    if band and not band.get("contained"):
        tips.append(
            "밴드를 천장/바닥으로 쓰지 말 것. "
            "상단 돌파 + 거래량 폭증 시 상단 확장 가능성을 더 높게 평가"
        )
        ah = (results.get("resistance") or {}).get("actual_high")
        pu = (band.get("predicted") or [None, None])[1]
        if ah is not None and pu is not None and float(ah) > float(pu):
            tips.append("예측 실패 원인: 다음 저항만 제시하고 확장 구간을 빠뜨림")
    if support and support.get("actual_low") is not None and support.get("predicted"):
        if support["actual_low"] <= support["predicted"]:
            tips.append("지지가 뚫리면 그 아래 다음 지지(풋 OI)를 같이 제시")
    if ret is not None and float(ret) <= -5:
        tips.append("급락일엔 C/P 상승을 강세로 읽지 말 것(반등 시도/양방향 베팅)")
    if not tips and missed:
        tips.append("어제 강한 이상신호가 있었는데 본문에서 비중을 키울 것")
    return " / ".join(tips) if tips else None


def grade_yesterday(
    ticker: str,
    prev_snap: dict | None,
    today_ohlc: dict | None = None,
) -> dict | None:
    """어제 예측 vs 오늘 실제 채점 결과 dict. 어제 스냅샷 없으면 None."""
    if not prev_snap:
        return None
    pred = extract_daily_prediction(prev_snap)
    ohlc = today_ohlc or daily_ohlc(ticker)
    if not ohlc or ohlc.get("close") is None:
        return {
            "available": False,
            "prediction_date": prev_snap.get("date"),
            "note": "오늘 실제 OHLC를 아직 가져오지 못했어요.",
            "predicted": pred,
        }

    band = wm.band_result(
        pred.get("band_lower"), pred.get("band_upper"), ohlc["low"], ohlc["high"]
    )
    # 일일 방향: open→close, 없으면 previous_close→close 근사
    ret = ohlc.get("return_pct")
    if ret is None and prev_snap.get("spot") and ohlc.get("close"):
        ps = float(prev_snap["spot"])
        ret = round((float(ohlc["close"]) - ps) / ps * 100, 2)
        ohlc = {**ohlc, "return_pct": ret}

    resistance = wm.resistance_result(pred.get("resistance"), ohlc["high"])
    support = wm.support_result(pred.get("support"), ohlc["low"])
    direction = wm.direction_result(pred.get("sentiment"), ret if ret is not None else 0.0)
    # direction 라벨을 일일 표현으로 살짝 수정
    if direction:
        direction = {
            **direction,
            "label": direction["label"].replace("주간 ", "일일 "),
        }
    grade = wm.composite_grade(band, direction, resistance, support)
    results = {
        "band": band,
        "resistance": resistance,
        "support": support,
        "direction": direction,
        "grade": grade,
    }
    missed = find_missed_signals(prev_snap, results)
    lesson = build_lesson(missed, results)

    accuracy_summary = []
    if band:
        accuracy_summary.append(
            "밴드 성공" if band.get("contained") else f"밴드 실패 ({band.get('label')})"
        )
    if support:
        failed_sup = (
            support.get("actual_low") is not None
            and support.get("predicted") is not None
            and support["actual_low"] <= support["predicted"]
        )
        accuracy_summary.append("지지 실패" if failed_sup else "지지 참고 OK")
    if resistance:
        accuracy_summary.append(resistance.get("label", "저항")[:40])
    if direction:
        accuracy_summary.append(
            "방향 일치" if direction.get("match") else "방향 불일치"
        )

    return {
        "available": True,
        "ticker": ticker.upper(),
        "date": ohlc["date"],  # 평가일(오늘)
        "prediction_date": prev_snap.get("date"),
        "predicted": {
            "support": pred.get("support"),
            "resistance": pred.get("resistance"),
            "band": [pred.get("band_lower"), pred.get("band_upper")],
            "band_pct": pred.get("band_pct"),
            "sentiment": pred.get("sentiment"),
            "spot": pred.get("spot"),
            "expiry": pred.get("expiry"),
        },
        "actual": {
            "open": ohlc.get("open"),
            "high": ohlc.get("high"),
            "low": ohlc.get("low"),
            "close": ohlc.get("close"),
            "return_pct": ohlc.get("return_pct"),
        },
        "accuracy": {
            "band": (
                "PASS"
                if band and band.get("contained")
                else ("FAIL" if band else "N/A")
            ),
            "support": (
                "FAIL"
                if support
                and support.get("actual_low") is not None
                and support.get("predicted") is not None
                and support["actual_low"] <= support["predicted"]
                else ("PASS" if support else "N/A")
            ),
            "resistance": (
                "HIT"
                if resistance
                and resistance.get("actual_high") is not None
                and resistance.get("predicted") is not None
                and resistance["actual_high"] >= resistance["predicted"]
                else ("FAIL" if resistance else "N/A")
            ),
            "direction": "PASS" if direction and direction.get("match") else "FAIL",
            "summary": " / ".join(accuracy_summary),
            "grade": grade,
        },
        "results": results,
        "missed_signals": missed,
        "lesson": lesson,
    }


def format_feedback_section(fb: dict | None) -> str:
    """리포트 상단용 채점 블록 — 실패 시 원인·개선(초보자 문장) 포함."""
    import report_evidence as ev

    if not fb:
        return ""
    if not fb.get("available"):
        note = fb.get("note") or "직전 리포트 채점 데이터가 부족해요."
        return f"📊 직전 리포트 채점\n- {note}\n"

    pred = fb.get("predicted") or {}
    act = fb.get("actual") or {}
    acc = fb.get("accuracy") or {}
    results = fb.get("results") or {}
    g = acc.get("grade") or {}
    grade = g.get("grade", "?")
    score = g.get("score", "?")
    pdate = fb.get("prediction_date") or "?"
    edate = fb.get("date") or "?"

    L: list[str] = []
    L.append(f"📚 어제 예측 → 오늘 결과 ({grade} {score}점) · {pdate}→{edate}")

    band = results.get("band") or {}
    if acc.get("band") == "PASS":
        L.append("✅ 밴드 예측 성공")
    elif acc.get("band") == "FAIL":
        L.append("❌ 밴드 예측 실패")
    if band.get("predicted") and act.get("low") is not None:
        pl, pu = band["predicted"]
        L.append(f"   예상 ${pl}~${pu} / 실제 저·고 ${act.get('low')}~${act.get('high')}")

    support = results.get("support") or {}
    if acc.get("support") == "FAIL":
        ps = support.get("predicted")
        al = support.get("actual_low")
        L.append(
            f"❌ 지지선 실패 (${ps:g} → 저가 ${al:g})"
            if ps is not None and al is not None
            else "❌ 지지선 실패"
        )
    elif acc.get("support") == "PASS":
        L.append("✅ 지지선 참고 OK")

    resistance = results.get("resistance") or {}
    if acc.get("resistance") == "HIT":
        pr = resistance.get("predicted")
        ah = act.get("high")
        if pr is not None and ah is not None and float(ah) > float(pr) * 1.005:
            L.append(f"${pr:g} 저항 후보 → ✅ 돌파 (고가 ${ah:g})")
        else:
            L.append(f"${pr:g} 저항 후보 → ✅ 도달" if pr is not None else "✅ 저항 후보 도달")
    elif acc.get("resistance") == "FAIL" or resistance.get("predicted") is not None:
        L.append(
            f"${resistance['predicted']:g} 저항 후보 → ❌ 미달"
            if resistance.get("predicted") is not None
            else "❌ 저항 후보 미달"
        )

    direction = results.get("direction") or {}
    senti = pred.get("sentiment") or direction.get("predicted_sentiment")
    ret = act.get("return_pct")
    if senti == "중립":
        if ret is not None and abs(ret) >= 2:
            L.append(f"⚪ 방향: 중립 예상이었는데 큰 움직임 ({ret:+.1f}%)")
        else:
            L.append("✅ 방향: 중립 구간 유지")
    elif acc.get("direction") == "PASS":
        L.append("✅ 방향 예측 성공")
    elif acc.get("direction") == "FAIL":
        L.append(
            f"❌ 방향 예측 실패 ({senti}, 실제 {ret:+.1f}%)"
            if ret is not None
            else "❌ 방향 예측 실패"
        )

    L.extend(ev.feedback_cause_lines(fb))

    missed = fb.get("missed_signals") or []
    lesson = fb.get("lesson")
    bl = ev.beginner_lesson(lesson, missed)
    if bl:
        L.append("")
        L.append(bl)
    elif missed:
        top = missed[0]
        short = top if len(top) <= 110 else top[:107] + "..."
        L.append(f"💡 주목 신호: {short}")
    L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 저장 / 누적 통계
# ------------------------------------------------------------------ #

def _pred_dir(ticker: str) -> Path:
    import config
    from pathlib import Path as P

    root = P(__file__).resolve().parent
    base = P(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else root / base
    return base / ticker.upper() / "predictions"


def save_prediction_record(fb: dict) -> Path | None:
    if not fb or not fb.get("available"):
        return None
    ticker = fb.get("ticker")
    date = fb.get("date")
    if not ticker or not date:
        return None
    d = _pred_dir(ticker)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date}.json"
    # 저장용 슬림 카피
    payload = {
        "date": date,
        "prediction_date": fb.get("prediction_date"),
        "ticker": ticker,
        "predicted": fb.get("predicted"),
        "actual": fb.get("actual"),
        "accuracy": fb.get("accuracy"),
        "missed_signals": fb.get("missed_signals") or [],
        "lesson": fb.get("lesson"),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_prediction_history(ticker: str, limit: int = 30) -> list[dict]:
    d = _pred_dir(ticker)
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), reverse=True)[:limit]
    out = []
    for p in files:
        try:
            with p.open("r", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def cumulative_stats(ticker: str, limit: int = 30) -> dict:
    """최근 N일 채점 누적 통계 + 자주 놓친 시그널."""
    hist = load_prediction_history(ticker, limit=limit)
    if not hist:
        return {"available": False, "n": 0, "note": "아직 학습 기록이 없어요."}

    n = len(hist)
    band_ok = sum(1 for h in hist if (h.get("accuracy") or {}).get("band") == "PASS")
    support_ok = sum(
        1 for h in hist if (h.get("accuracy") or {}).get("support") == "PASS"
    )
    direction_ok = sum(
        1 for h in hist if (h.get("accuracy") or {}).get("direction") == "PASS"
    )
    band_n = sum(1 for h in hist if (h.get("accuracy") or {}).get("band") in ("PASS", "FAIL"))
    support_n = sum(
        1 for h in hist if (h.get("accuracy") or {}).get("support") in ("PASS", "FAIL")
    )
    direction_n = sum(
        1 for h in hist if (h.get("accuracy") or {}).get("direction") in ("PASS", "FAIL")
    )

    from collections import Counter

    sig_counter: Counter[str] = Counter()
    lessons: list[str] = []
    for h in hist:
        for s in h.get("missed_signals") or []:
            # 정규화: 긴 메시지는 앞부분만
            key = s if len(s) < 80 else s[:77] + "..."
            sig_counter[key] += 1
        if h.get("lesson"):
            lessons.append(h["lesson"])

    top_missed = [{"signal": s, "count": c} for s, c in sig_counter.most_common(3)]

    def _pct(ok, total):
        return round(ok / total * 100) if total else None

    return {
        "available": True,
        "n": n,
        "window": limit,
        "band_accuracy_pct": _pct(band_ok, band_n),
        "support_accuracy_pct": _pct(support_ok, support_n),
        "direction_accuracy_pct": _pct(direction_ok, direction_n),
        "top_missed_signals": top_missed,
        "recent_lessons": lessons[:5],
    }


def learning_context_for_llm(ticker: str, today_feedback: dict | None = None) -> dict:
    """LLM 에 넣을 최근 학습 컨텍스트."""
    stats = cumulative_stats(ticker, limit=30)
    last7 = cumulative_stats(ticker, limit=7)
    ctx: dict = {
        "최근30일": stats,
        "최근7일": last7,
    }
    if today_feedback and today_feedback.get("available"):
        ctx["오늘채점요약"] = {
            "prediction_date": today_feedback.get("prediction_date"),
            "accuracy": (today_feedback.get("accuracy") or {}).get("summary"),
            "missed_signals": today_feedback.get("missed_signals") or [],
            "lesson": today_feedback.get("lesson"),
            "grade": (today_feedback.get("accuracy") or {}).get("grade"),
        }
    # 개선 지시 한 줄
    tips: list[str] = []
    s7 = last7 if last7.get("available") else stats
    if s7.get("support_accuracy_pct") is not None and s7["support_accuracy_pct"] < 50:
        tips.append("지지선 예측 정확도가 낮음 — 강한 지지(OI)와 단기 지지를 함께 강조")
    if s7.get("band_accuracy_pct") is not None and s7["band_accuracy_pct"] < 50:
        tips.append("밴드 이탈이 잦음 — OI/V·OI 급변을 리스크 신호로 먼저 언급")
    for m in (s7.get("top_missed_signals") or [])[:2]:
        tips.append(f"자주 놓침: {m['signal']}")
    if today_feedback and today_feedback.get("lesson"):
        tips.append(today_feedback["lesson"])
    ctx["개선지시"] = tips
    return ctx
