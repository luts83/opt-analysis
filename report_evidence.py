"""리포트 각 섹션의 '왜/근거' 문장 생성 (초보자용).

LLM이 근거를 빼먹어도 시스템이 이 블록으로 교체한다.
"""
from __future__ import annotations

import re


def _fmt_px(v) -> str:
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _rel(spot: float, strike: float) -> str:
    try:
        pct = (float(strike) - float(spot)) / float(spot) * 100
        return f"{pct:+.0f}%"
    except Exception:
        return ""


# ------------------------------------------------------------------ #
# 한 줄 제목 (위치 논리 체크)
# ------------------------------------------------------------------ #

def _nearest(levels: dict, spot: float, side: str) -> float | None:
    keys = ("near_support", "strong_support") if side == "sup" else (
        "near_resistance", "strong_resistance"
    )
    cands = []
    for k in keys:
        for it in levels.get(k) or []:
            try:
                s = float(it["strike"])
            except (TypeError, ValueError, KeyError):
                continue
            if side == "sup" and s < spot * 0.995:
                cands.append(s)
            elif side == "res" and s > spot * 1.005:
                cands.append(s)
    if not cands:
        return None
    cands.sort(key=lambda x: abs(x - spot))
    return cands[0]


def plain_talk_block(data: dict, base: dict, eventinfo: dict | None = None) -> str:
    """초보자용 2~3줄. 비유는 1~2문장만."""
    spot = data.get("spot")
    if spot is None:
        return ""
    spot = float(spot)
    levels = base.get("levels") or {}
    if levels.get("low_confidence") or base.get("low_confidence"):
        return (
            "💡 쉽게 말하면\n"
            "오늘은 옵션 대기물량(OI) 숫자가 거의 없어, 지지·저항이라고 단정하지 않습니다.\n"
            "거래량이 많아 보여도 '누가 샀는지/팔았는지'를 알 수 없어 방향은 열어 둡니다."
        )
    nxt = (eventinfo or {}).get("next_session") or {}
    exp = levels.get("expansion_up") or nxt.get("expansion_up")
    sup = _nearest(levels, spot, "sup")
    res = _nearest(levels, spot, "res")
    key = res or (exp or {}).get("break_level")
    ticker = data.get("ticker", "")

    L = ["💡 쉽게 말하면"]
    if key and sup:
        L.append(
            f"오늘 {ticker}는 {_fmt_px(sup)}~{_fmt_px(key)} 사이에서 방향을 가늠하는 구간입니다."
        )
        if exp and exp.get("zone"):
            z0, z1 = exp["zone"]
            mag = exp.get("magnet")
            extra = f" → {_fmt_px(mag)}까지 열어볼 수 있어요" if mag else ""
            L.append(
                f"{_fmt_px(key)}를 거래량과 함께 돌파·유지하면 "
                f"${z0}~${z1}로 길이 넓어지고{extra}. "
                f"{_fmt_px(sup)} 아래로 빠지면 상승 이야기는 힘이 빠집니다."
            )
        else:
            L.append(
                f"{_fmt_px(key)} 위는 아직 '관심 가격'일 뿐 천장도 바닥도 아닙니다. "
                f"{_fmt_px(sup)}을 지키면 숨 고르기, 이탈하면 조심."
            )
    elif key:
        L.append(
            f"눈여겨볼 가격은 {_fmt_px(key)}입니다. "
            "옵션이 몰린 자리라 반응이 나오기 쉽지만, 그 자체가 저항은 아닙니다."
        )
        L.append("돌파가 거래량과 함께 유지되는지를 먼저 보세요.")
    else:
        L.append(
            "가까운 옵션 관심 가격이 뚜렷하지 않습니다. "
            "방향보다 변동 폭이 커질 수 있는지부터 봅니다."
        )
    return "\n".join(L[:4])


def signals_block(data: dict, base: dict, eventinfo: dict | None = None) -> str:
    spot = data.get("spot")
    if spot is None:
        return ""
    spot = float(spot)
    levels = base.get("levels") or {}
    nxt = (eventinfo or {}).get("next_session") or {}
    exp = levels.get("expansion_up") or nxt.get("expansion_up")
    res = _nearest(levels, spot, "res")
    sup = _nearest(levels, spot, "sup")
    key = (exp or {}).get("break_level") or res
    L = ["🚦 오늘의 신호"]
    if levels.get("low_confidence") or base.get("low_confidence"):
        L.append("🟡 중립 — 옵션 데이터 신뢰도가 낮아 방향 신호를 내지 않습니다.")
        return "\n".join(L)
    if key and exp and exp.get("zone"):
        z0, z1 = exp["zone"]
        mag = exp.get("magnet")
        up = f"{_fmt_px(key)} 돌파 + 거래량 증가 + 유지 → ${z0}~${z1} 확장"
        if mag:
            up += f" / 지속 시 {_fmt_px(mag)} 테스트"
        L.append(f"🟢 상승 조건: {up}")
    elif key:
        L.append(f"🟢 상승 조건: {_fmt_px(key)} 돌파가 유지되면 위쪽 관심")
    else:
        L.append("🟢 상승 조건: 가까운 관심 가격 돌파·유지")
    if sup and key:
        L.append(f"🟡 중립 구간: {_fmt_px(sup)}~{_fmt_px(key)}")
    elif sup:
        L.append(f"🟡 중립 구간: {_fmt_px(sup)} 위 소화")
    else:
        L.append("🟡 중립 구간: 뚜렷한 박스 없음 — 레벨 반응 대기")
    if sup:
        L.append(f"🔴 하락 조건: {_fmt_px(sup)} 이탈 시 상승 시나리오 약화")
    else:
        L.append("🔴 하락 조건: 최근 저점 이탈")
    bb = None
    try:
        import price_levels as pl

        bb = pl.band_breakout_signal(base, spot)
    except Exception:
        bb = None
    if bb:
        L.append(bb["text"])
    return "\n".join(L)


def price_map_block(data: dict, base: dict) -> str:
    """현재가 중심 가격 지도 (가까운 것 우선)."""
    spot = data.get("spot")
    if spot is None:
        return ""
    spot = float(spot)
    levels = base.get("levels") or {}
    exp = levels.get("expansion_up")
    def _stars(it: dict) -> str:
        if it.get("role") == "testing":
            return "⭐⭐⭐⭐⭐"
        sc = it.get("score") or 0
        d = abs(float(it["strike"]) - spot) / spot * 100
        if d <= 2 and sc >= 0.5:
            return "⭐⭐⭐⭐⭐"
        if d <= 5:
            return "⭐⭐⭐"
        return "⭐"

    rows: list[tuple[float, str]] = [(spot, f"{_fmt_px(spot)} 현재가")]
    seen = {round(spot, 2)}
    for it in levels.get("interest_all") or []:
        s = float(it["strike"])
        if abs(s - spot) / spot > 0.12 and not (
            exp and exp.get("magnet") and abs(s - float(exp["magnet"])) < 0.05
        ):
            continue
        rs = round(s, 2)
        if rs in seen:
            continue
        seen.add(rs)
        rows.append((s, f"{_fmt_px(s)}  {_stars(it)} {it.get('kind') or '관심 가격'}"))
    if exp and exp.get("zone"):
        z0, z1 = exp["zone"]
        mid = (z0 + z1) / 2
        if round(mid, 2) not in seen:
            rows.append((mid, f"${z0}~${z1} 🚀 돌파 시 확장 구간"))
    if exp and exp.get("magnet"):
        m = float(exp["magnet"])
        if round(m, 2) not in seen:
            rows.append((m, f"{_fmt_px(m)}  ⭐ 주요 관심 가격"))
        else:
            # 이미 있으면 라벨만 관심으로 유지 (1순위 아님)
            pass
    rows.sort(key=lambda x: -x[0])
    L = ["📍 오늘의 가격 지도"]
    if levels.get("low_confidence"):
        L.append("⚠️ 옵션 데이터 신뢰도 낮음 — 아래는 참고용 관심 가격입니다.")
    for _, line in rows[:9]:
        L.append(line)
    return "\n".join(L)


def why_block(data: dict, base: dict, eventinfo: dict | None = None) -> str:
    levels = base.get("levels") or {}
    cpr = base.get("call_put_volume_ratio")
    senti = base.get("sentiment") or ""
    chg = base.get("price_change_pct")
    L = ["🔍 왜 이렇게 보나?"]
    if levels.get("low_confidence") or base.get("low_confidence"):
        L.append(
            "OI 데이터가 없어 지지/저항 해석이 제한됩니다. "
            "거래량만으로 방향을 판단하지 않습니다. V/OI는 계산하지 않았습니다."
        )
        return "\n".join(L)
    ranked = (levels.get("ranked") or [])[:3]
    if ranked:
        L.append("가까운 관심 가격부터:")
        for r in ranked:
            L.append(
                f"- {_fmt_px(r['strike'])} ({r.get('label')}, "
                f"현재가 대비 {r.get('dist_pct')}%, 중요도 {r.get('score')})"
            )
    exp = levels.get("expansion_up")
    if exp and exp.get("note"):
        L.append(f"상단 확장: {exp['note']}")
    if cpr is not None:
        L.append(
            f"C/P {cpr} → 콜 거래량이 풋보다 약 {cpr:.1f}배 "
            f"({'구성비일 뿐 방향 신호 아님'})"
        )
        if chg is not None and chg <= -3 and cpr >= 1.2:
            L.append(
                "콜 우세지만 주가는 하락 — 반등 베팅/콜 매도/헤지가 섞일 수 있어 방향성은 불확실."
            )
        elif senti == "변동성 확대 가능성":
            L.append("콜·풋 극단이 동시에 있어 강세/약세를 억지로 고르지 않습니다.")
    zd = base.get("zero_dte_date")
    if zd:
        L.append(
            f"⚠️ 오늘({zd}) 만기 옵션이 있어 밴드가 장중 급변할 수 있습니다. "
            "주간 밴드는 만기일(0DTE)을 빼고 계산했습니다."
        )
    return "\n".join(L)


def low_confidence_banner(base: dict) -> str:
    if not (base.get("low_confidence") or (base.get("levels") or {}).get("low_confidence")):
        return ""
    return (
        "⚠️ 옵션 데이터 신뢰도 낮음\n"
        "OI 데이터가 없어 지지/저항 해석이 제한됩니다. "
        "거래량만으로 방향을 판단하지 않습니다."
    )


def one_liner(data: dict, base: dict, eventinfo: dict | None = None) -> str:
    ticker = data.get("ticker", "")
    levels = base.get("levels") or {}
    spot = data.get("spot")
    chg = ((eventinfo or {}).get("price") or {}).get("change_pct")
    if chg is None:
        chg = base.get("price_change_pct")
    earn = (eventinfo or {}).get("earnings") or {}
    if earn.get("phase") in ("임박", "직후"):
        return f"{ticker} 실적 {earn.get('phase')} — 변동성·레벨 반응 우선"

    # 현재가 아래 지지 / 위 저항만 후보
    supports_below = []
    for key in ("near_support", "strong_support"):
        for it in levels.get(key) or []:
            s = it.get("strike")
            if s is not None and spot is not None and float(s) < float(spot) * 0.995:
                supports_below.append(float(s))
    supports_below = sorted(set(supports_below), reverse=True)
    resists_above = []
    for key in ("near_resistance", "strong_resistance"):
        for it in levels.get(key) or []:
            s = it.get("strike")
            if s is not None and spot is not None and float(s) > float(spot) * 1.005:
                resists_above.append(float(s))
    resists_above = sorted(set(resists_above))

    # 이미 뚫린(위쪽) 옛 지지
    broken = []
    for it in (levels.get("flipped_to_resist") or []) + (levels.get("strong_resistance") or []):
        if it.get("flipped_from_support") or "뚫린" in str(it.get("note") or ""):
            broken.append(float(it["strike"]))
    for it in levels.get("strong_support") or []:
        s = it.get("strike")
        if s is not None and spot is not None and float(s) > float(spot) * 1.005:
            broken.append(float(s))

    next_sup = supports_below[0] if supports_below else None
    next_res = resists_above[0] if resists_above else None
    broken_sup = max(broken) if broken else None

    if spot is None:
        return f"{ticker} 옵션 시장 요약"

    # 급락
    if chg is not None and chg <= -3:
        parts = [f"{ticker} {chg:.1f}% 급락"]
        if broken_sup:
            parts.append(f"{_fmt_px(broken_sup)} 지지 이탈 후")
        if next_sup:
            parts.append(f"다음 관심(아래) {_fmt_px(next_sup)}")
        elif next_res:
            parts.append(f"{_fmt_px(spot)} 마감, 반등 시 {_fmt_px(next_res)} 주시")
        else:
            parts.append(f"{_fmt_px(spot)} 마감")
        return ", ".join(parts)

    # 급등
    if chg is not None and chg >= 3:
        exp = (levels.get("expansion_up") or {})
        if exp.get("zone"):
            z0, z1 = exp["zone"]
            return (
                f"{ticker} {chg:.1f}% 급등 — "
                f"관심가 돌파 시 ${z0}~${z1} 확장 주시"
            )
        if next_res and spot < next_res:
            return f"{ticker} {chg:.1f}% 급등, {_fmt_px(next_res)} 관심 가격 테스트"
        if next_res and spot >= next_res:
            return f"{ticker} {chg:.1f}% 급등 — {_fmt_px(next_res)} 돌파, 확장 여부 주시"
        return f"{ticker} {chg:.1f}% 급등, {_fmt_px(spot)} 마감"

    # 현재가가 레벨과 거의 같음
    for it in (levels.get("near_support") or []) + (levels.get("strong_support") or []):
        s = it.get("strike")
        if s is not None and abs(float(spot) - float(s)) / float(spot) < 0.005:
            return f"{ticker} {_fmt_px(spot)} 핵심 가격 테스트 중"

    if next_sup and chg is not None and chg < 0:
        return f"{ticker} {_fmt_px(spot)} — 아래 관심 {_fmt_px(next_sup)} 주시"
    if next_res:
        return f"{ticker} {_fmt_px(spot)} — {_fmt_px(next_res)} 관심 가격 주시"
    return f"{ticker} {_fmt_px(spot)} — 옵션 시장 요약"


# ------------------------------------------------------------------ #
# 오늘의 핵심 3가지 (초보자용 맨 위 요약)
# ------------------------------------------------------------------ #

def _pick_level(items: list[dict], spot: float, *, above: bool) -> dict | None:
    """현재가 위/아래에서 가장 가까운 레벨 1개."""
    cands = []
    for it in items or []:
        try:
            s = float(it["strike"])
        except (TypeError, ValueError, KeyError):
            continue
        if above and s > spot * 1.005:
            cands.append((s - spot, it))
        elif not above and s < spot * 0.995:
            cands.append((spot - s, it))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


def _level_why(it: dict, *, side: str) -> str:
    """초보자용 짧은 근거. 사겠다/팔겠다 단정 금지."""
    oi = it.get("oi")
    vol = it.get("volume")
    bits = ["해당 가격에 옵션 포지션이 많이 쌓여 있음"]
    if oi:
        bits.append(f"OI {oi:,}")
    if vol:
        bits.append(f"거래 {vol:,}")
    if it.get("role") == "broken_resist_now_support":
        return "위 관심가를 넘어 지금은 지지 후보 (돌파 확인)"
    if it.get("role") == "failed_breakout":
        return "돌파 후 다시 내려와 실패 가능성"
    return " · ".join(bits)


def _lesson_takeaway(fb: dict | None, ctx: dict | None = None) -> str:
    """세 번째 핵심: 과거 교훈을 초보자 문장으로."""
    fb = fb or {}
    missed = fb.get("missed_signals") or []
    lesson = fb.get("lesson") or ""
    acc = fb.get("accuracy") or {}
    pred = fb.get("predicted") or {}
    act = fb.get("actual") or {}
    ret = act.get("return_pct")
    senti = pred.get("sentiment")

    # 약세/풋 신호였는데 상승
    if (
        (senti == "약세" or any("PUT" in m.upper() or "풋" in m for m in missed))
        and ret is not None
        and float(ret) > 2
    ):
        return (
            "과거에는 '풋이 늘었으니 하락'으로만 읽었지만 실제로는 상승한 적이 있어요. "
            "풋이 정말 하락 베팅인지, 기관 보험(헤지)·풋 매도인지도 함께 봅니다."
        )
    # 강세/콜 신호였는데 급락
    if (
        (senti == "강세" or any("CALL" in m.upper() and "급락" in m for m in missed))
        and ret is not None
        and float(ret) < -2
    ) or ("콜 V/OI" in lesson and "급락" in lesson):
        return (
            "과거에는 '콜이 몰렸으니 상승'으로 읽었지만 급락한 적이 있어요. "
            "급락 때 콜 몰림은 매도자가 프리미엄을 받으려는 것일 수 있어, 방향만 보지 않습니다."
        )
    if acc.get("direction") == "FAIL" and ret is not None:
        return (
            f"직전 방향 예측이 빗나갔어요(예상 {senti or '?'}, 실제 {float(ret):+.1f}%). "
            "이번엔 한쪽 신호만 보고 단정하지 않고, 콜·풋을 같이 봅니다."
        )
    tips = (ctx or {}).get("개선지시") or []
    if tips:
        t0 = tips[0]
        if "풋" in t0 or "PUT" in t0.upper():
            return (
                "최근 놓친 패턴: 풋 신호를 과대/과소 해석. "
                "풋 증가 = 무조건 하락이 아니라 헤지·매도일 수도 있어요."
            )
        if "콜" in t0 or "CALL" in t0.upper():
            return (
                "최근 놓친 패턴: 콜 신호를 과대 해석. "
                "콜 몰림 = 무조건 상승이 아니라 콜 매도일 수도 있어요."
            )
        return f"이번 반영 교훈: {t0[:80]}"
    if lesson:
        bl = beginner_lesson(lesson, missed)
        for line in bl.split("\n")[1:]:
            if line.strip():
                return line.strip()
    return (
        "옵션 거래가 많다고 방향을 단정하지 않습니다. "
        "'누가 샀는지/팔았는지'와 지지·저항 반응을 같이 봅니다."
    )


def key_summary_block(
    data: dict,
    base: dict,
    eventinfo: dict | None = None,
    feedback: dict | None = None,
    learning_context: dict | None = None,
) -> str:
    """맨 위용 핵심 3가지 — 초보자가 이것만 읽어도 되게."""
    spot = data.get("spot")
    if spot is None:
        return ""
    spot = float(spot)
    levels = enrich_levels(base.get("levels") or {}, spot)

    res_pool = (levels.get("near_resistance") or []) + (levels.get("strong_resistance") or [])
    # 현재가에 가까운 가격 우선, 동률이면 OI/거래
    def _weight(it: dict) -> tuple:
        return (abs(float(it["strike"]) - spot), -(it.get("oi") or 0), -(it.get("volume") or 0))

    res_above = [it for it in res_pool if float(it["strike"]) > spot * 1.005]
    res_above.sort(key=_weight)
    resist = res_above[0] if res_above else _pick_level(res_pool, spot, above=True)

    sup_pool = (levels.get("near_support") or []) + (levels.get("strong_support") or [])
    sup_below = [it for it in sup_pool if float(it["strike"]) < spot * 0.995]
    # 가까운 방어선 우선, 동률이면 OI/거래 큰 쪽
    sup_below.sort(key=lambda it: (abs(float(it["strike"]) - spot), -(it.get("oi") or 0)))
    support = sup_below[0] if sup_below else _pick_level(sup_pool, spot, above=False)

    L = ["⭐ 오늘의 핵심 3가지", "(아래 긴 내용 전에, 이것만 먼저 읽으세요)"]
    n = 1
    if resist:
        rs = float(resist["strike"])
        L.append(f"{n}. {_fmt_px(rs)}가 가까운 관심 가격")
        L.append(f"   {_level_why(resist, side='res')}.")
        L.append("   → 저항 후보이며, 돌파·유지 여부를 확인합니다 (천장 아님).")
        n += 1
    if support:
        ss = float(support["strike"])
        L.append(f"{n}. {_fmt_px(ss)}는 아래쪽 관심 가격")
        L.append(f"   {_level_why(support, side='sup')}.")
        L.append("   → 지지 후보. 이탈하면 상승 시나리오가 약해질 수 있습니다.")
        n += 1
    if n < 3:
        L.append(f"{n}. 이전 분석의 교훈")
        lesson = _lesson_takeaway(feedback, learning_context)
        if len(lesson) > 90:
            cut = lesson.rfind(" ", 0, 90)
            if cut < 40:
                cut = 90
            L.append(f"   {lesson[:cut].strip()}")
            L.append(f"   {lesson[cut:].strip()}")
        else:
            L.append(f"   {lesson}")
        n += 1
    if n < 3:
        L.append(f"{n}. 방향은 C/P만으로 단정하지 않습니다")
        L.append("   주가 반응·거래량·관심 가격을 같이 봅니다.")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 시장 온도
# ------------------------------------------------------------------ #

def sentiment_block(base: dict, *, in_earnings: bool = False) -> str:
    cpr = base.get("call_put_volume_ratio")
    senti = base.get("sentiment") or "중립"
    chg = base.get("price_change_pct")
    tags = base.get("sentiment_tags") or []
    if cpr is None:
        return ""

    label = senti
    L = ["🌡️ 시장 온도", f"옵션 거래 구성: {label} (C/P {cpr})"]
    if tags:
        L.append(f"태그: {', '.join(tags)}")
    L.append(f"C/P {cpr} → 콜 거래량이 풋보다 약 {float(cpr):.1f}배 많음 (방향 신호 아님)")

    if base.get("low_confidence"):
        L.append("해석: 저신뢰 모드 — 옵션 심리는 참고만.")
    elif senti == "변동성 확대 가능성":
        L.append("해석: 콜·풋 극단이 동시에 있어 강세/약세를 고르지 않습니다.")
    elif chg is not None and chg <= -5:
        L.append(
            f"해석: 주가 {chg:+.1f}% 급락 + 콜 우세 → "
            "반등 베팅/콜 매도/헤지 가능. 방향성은 불확실."
        )
    elif chg is not None and chg >= 5:
        L.append(
            f"해석: 주가 {chg:+.1f}% 급등 + C/P {cpr}. "
            "차익실현·헤지가 섞였을 수 있어요."
        )
    else:
        L.append(
            "해석: 주가 방향·V/OI·OI 변화·행사가 위치·실제 가격 반응을 같이 봅니다."
            + (" (실적 전후라 참고용)" if in_earnings else "")
        )
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 지지/저항 (현재가 기준 + 근거)
# ------------------------------------------------------------------ #

def enrich_levels(levels: dict | None, spot: float | None) -> dict:
    """현재가 위 풋OI → 저항 전환, 아래 콜OI → 지지 전환. note/meaning 채움."""
    if not levels or spot is None:
        return levels or {}
    spot = float(spot)
    out = {
        "strong_support": [],
        "near_support": [],
        "near_resistance": [],
        "strong_resistance": [],
        "flipped_to_resist": [],
        "flipped_to_support": [],
        "has_oi_levels": levels.get("has_oi_levels"),
    }

    def _copy(it: dict, **extra) -> dict:
        d = dict(it)
        d.update(extra)
        return d

    for it in levels.get("strong_support") or []:
        s = float(it["strike"])
        oi = it.get("oi")
        meaning = f"{_fmt_px(s)}에 풋 포지션이 많이 쌓여 있음 (지지 후보, 단정 아님)"
        if oi:
            meaning += f" (OI {oi:,}개)"
        if s > spot * 1.005:
            # 이미 위로 뚫림 → 저항 역할
            out["flipped_to_resist"].append(
                _copy(
                    it,
                    kind="전환저항",
                    basis=it.get("basis") or "풋 OI 밀집",
                    flipped_from_support=True,
                    note=f"현재가({_fmt_px(spot)}) 위 → 이미 뚫린 지지, 이제 저항 역할",
                    meaning=f"예전 아래 관심가. 반등 시 {_fmt_px(s)} 저항 후보",
                )
            )
        else:
            out["strong_support"].append(
                _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
            )

    for it in levels.get("strong_resistance") or []:
        s = float(it["strike"])
        oi = it.get("oi")
        meaning = f"{_fmt_px(s)}에 옵션 포지션이 많이 쌓여 있음 (저항 후보, 단정 아님)"
        if oi:
            meaning += f" (OI {oi:,}개)"
        if s < spot * 0.995:
            out["flipped_to_support"].append(
                _copy(
                    it,
                    kind="전환지지",
                    flipped_from_resist=True,
                    note=f"현재가({_fmt_px(spot)}) 아래 → 뚫린 저항, 이제 지지 역할",
                    meaning=meaning,
                )
            )
        else:
            out["strong_resistance"].append(
                _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
            )

    for it in levels.get("near_support") or []:
        s = float(it["strike"])
        if s > spot * 1.01:
            continue  # 위쪽은 단기지지로 안 씀
        vol = it.get("volume")
        meaning = f"현재가 근처 풋 거래 집중 → {_fmt_px(s)} 옵션 관심 가격"
        if vol:
            meaning += f" (거래 {vol:,}계약, 지지로 단정하지 않음)"
        out["near_support"].append(
            _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
        )

    for it in levels.get("near_resistance") or []:
        s = float(it["strike"])
        if s < spot * 0.99:
            continue
        vol = it.get("volume")
        meaning = f"현재가 근처 콜 거래 집중 → {_fmt_px(s)} 옵션 관심 가격"
        if vol:
            meaning += f" (거래 {vol:,}계약, 저항으로 단정하지 않음)"
        out["near_resistance"].append(
            _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
        )

    # 전환된 것을 강한 저항/지지 목록에도 합침 (표시용)
    out["strong_resistance"] = out["flipped_to_resist"] + out["strong_resistance"]
    out["strong_support"] = out["strong_support"] + out["flipped_to_support"]
    return out


def levels_block(levels: dict | None, spot: float | None = None) -> str:
    if not levels:
        return "🟢 지지선\n- (데이터 없음)\n🔴 저항선\n- (데이터 없음)"
    if spot is not None:
        levels = enrich_levels(levels, spot)

    def _fmt_item(it: dict) -> list[str]:
        s = it["strike"]
        lines = [f"- {_fmt_px(s)}"]
        bits = []
        if it.get("oi"):
            bits.append(f"OI {it['oi']:,}개")
        if it.get("volume"):
            bits.append(f"거래 {it['volume']:,}")
        if it.get("basis"):
            bits.append(str(it["basis"]))
        if bits:
            lines[0] += f"  근거: {', '.join(bits)}"
        if it.get("note"):
            lines.append(f"  ({it['note']})")
        if it.get("meaning"):
            lines.append(f"  의미: {it['meaning']}")
        return lines

    L = ["🟢 아래 관심 가격 (지지 후보 — OI만으로 지지 단정 안 함)"]
    ss = levels.get("strong_support") or []
    ns = levels.get("near_support") or []
    if spot is not None:
        ss = [x for x in ss if float(x["strike"]) <= float(spot) * 1.005]
        ns = [x for x in ns if float(x["strike"]) <= float(spot) * 1.005]
    if ss:
        L.append("확인된 지지" if any(x.get("confirmed") for x in ss) else "지지 후보")
        for it in ss[:2]:
            L.extend(_fmt_item(it))
    if ns:
        L.append("가까운 관심 (아래)")
        for it in ns[:2]:
            L.extend(_fmt_item(it))
    if not ss and not ns:
        L.append("- (현재가 아래 뚜렷한 관심 가격 없음)")

    L.append("🔴 위 관심 가격 (저항 후보 — OI만으로 저항 단정 안 함)")
    nr = levels.get("near_resistance") or []
    sr = levels.get("strong_resistance") or []
    if spot is not None:
        nr = [x for x in nr if float(x["strike"]) >= float(spot) * 0.995]
        sr = [x for x in sr if float(x["strike"]) >= float(spot) * 0.995]
    if nr:
        L.append("가까운 관심 (위)")
        for it in nr[:2]:
            L.extend(_fmt_item(it))
    if sr:
        L.append("확인된 저항" if any(x.get("confirmed") for x in sr) else "저항 후보")
        for it in sr[:2]:
            L.extend(_fmt_item(it))
    if not nr and not sr:
        L.append("- (현재가 위 뚜렷한 관심 가격 없음)")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 예상 범위
# ------------------------------------------------------------------ #

def band_block(base: dict) -> str:
    bt = base.get("band_trend") or {}
    rows = bt.get("rows") or []
    if not rows:
        import metrics as _m

        bt = _m.build_band_trend(base) or {}
        rows = bt.get("rows") or []
    if not rows:
        near = (base.get("expiry_metrics") or {}).get("this_week") or {}
        st = near.get("straddle")
        if not st:
            return ""
        rows = [
            {
                "label": "이번주",
                "date": near.get("date"),
                "lower": round(float(st["lower"])),
                "upper": round(float(st["upper"])),
                "band_pct": round(float(st["band_pct"])),
            }
        ]

    L = ["📈 예상 범위 (옵션 시장이 가격으로 반영한 예상 변동 범위)"]
    for r in rows:
        date_bit = f"(~{r['date']})" if r.get("date") else ""
        label = r["label"]
        if r.get("role") == "zero_dte" or "오늘" in str(label):
            label = "오늘(만기일)"
        L.append(
            f"{label}{date_bit}: ${_fmt_num(r['lower'])} ~ ${_fmt_num(r['upper'])} "
            f"(±{r.get('band_pct')}%)"
        )
    L.append("계산: 현재가 ± ATM 스트래들(같은 행사가 콜+풋 가격 합)")
    L.append("의미: 천장/바닥이 아님. 흔히 움직일 수 있다고 옵션이 매긴 대략 범위.")
    if base.get("zero_dte_date"):
        L.append(
            f"⚠️ 오늘 만기({base['zero_dte_date']}) 옵션이 포함될 수 있어 "
            "밴드가 장중 급변할 수 있습니다. 위 이번주는 0DTE를 제외한 값입니다."
        )
    tip = bt.get("interpretation") or "→ 만기가 멀수록 불확실성(밴드) 확대"
    if not tip.startswith("→"):
        tip = f"→ {tip}"
    L.append(tip)
    return "\n".join(L)


def _fmt_num(v) -> str:
    try:
        return f"{float(v):g}"
    except Exception:
        return str(v)


# ------------------------------------------------------------------ #
# 시나리오 / 체크포인트 보강 텍스트
# ------------------------------------------------------------------ #

def format_scenarios(nxt: dict | None) -> str:
    if not nxt or not nxt.get("scenarios"):
        return ""
    L = [nxt.get("section_title") or "🔮 시나리오 (가능성 순)"]
    if nxt.get("gap_note"):
        L.append(nxt["gap_note"])
    for s in nxt["scenarios"]:
        L.append(f"- {s['name']}: {s['condition']}")
        if s.get("watch"):
            L.append(f"  → {s['watch']}")
        if s.get("evidence"):
            L.append(f"  근거: {s['evidence']}")
    return "\n".join(L)


def format_checkpoints(nxt: dict | None) -> str:
    L = ["🎯 오늘 체크포인트"]
    checks = (nxt or {}).get("checkpoints") or []
    if checks:
        for c in checks:
            if isinstance(c, dict):
                L.append(f"- {c.get('text')}")
                if c.get("why"):
                    L.append(f"  ({c['why']})")
            else:
                L.append(f"- {c}")
    elif (nxt or {}).get("action_hint"):
        for part in str(nxt["action_hint"]).split(" / "):
            L.append(f"- {part.strip()}")
    else:
        L.append("- 지지·저항 반응을 먼저 확인하세요.")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 채점 원인 분석
# ------------------------------------------------------------------ #

def feedback_cause_lines(fb: dict) -> list[str]:
    """실패 항목별 원인·개선 (초보자 문장)."""
    if not fb or not fb.get("available"):
        return []
    results = fb.get("results") or {}
    acc = fb.get("accuracy") or {}
    act = fb.get("actual") or {}
    missed = fb.get("missed_signals") or []
    L: list[str] = []

    band = results.get("band") or {}
    if acc.get("band") == "FAIL" and band.get("predicted") and act.get("low") is not None:
        pl, pu = band["predicted"]
        al, ah = act.get("low"), act.get("high")
        undershoot = round(pl - al, 2) if al is not None and al < pl else 0
        L.append("")
        L.append("원인:")
        if undershoot > 0:
            L.append(f"  하단이 ${_fmt_num(undershoot)} 빗나감 (예상 하단 ${_fmt_num(pl)} vs 실제 저가 ${_fmt_num(al)}).")
        putish = [m for m in missed if "PUT" in m.upper() or "풋" in m]
        callish = [m for m in missed if "CALL" in m.upper() or "콜" in m]
        if putish and callish:
            L.append("  어제 콜·풋 양쪽에 극단 신호가 있었는데, 한쪽만 보고 변동성 확대를 과소평가했을 수 있어요.")
        elif putish:
            L.append(f"  놓친 하락 쪽 신호: {putish[0][:90]}")
        elif callish and (act.get("return_pct") or 0) < -2:
            L.append(
                "  콜 쪽 극단만 보고 '상승'으로 읽었을 수 있어요. "
                "급락일에 콜이 몰리면 '콜을 팔아 프리미엄 받으려는 매도'일 수도 있습니다."
            )
        L.append("개선:")
        L.append("  콜/풋 극단이 양쪽에 나오면 '방향'보다 '변동성 확대'로 먼저 판단.")

    if missed and not any("원인:" in x for x in L):
        L.append("")
        L.append(f"주목했던 신호: {missed[0][:100]}")
    return L


def beginner_lesson(lesson: str | None, missed: list[str] | None = None) -> str:
    """전문 용어 교훈 → 초보자 문장."""
    raw = lesson or ""
    missed = missed or []
    # 콜 극단 + 급락 패턴
    if any("CALL" in m.upper() and "급락" in m for m in missed) or "콜 V/OI" in raw:
        return (
            "💡 다음부터 이렇게 봅니다\n"
            "콜 옵션에 사람이 많이 몰렸다고 = 무조건 상승 신호가 아닙니다.\n"
            "누가 '샀는지' vs '팔았는지' 방향이 중요해요.\n"
            "급락 국면에서 콜 몰림 = 오히려 매도자가 콜을 팔아 프리미엄을 챙기려는 것일 수 있어요\n"
            "→ 그 경우엔 하락 지속 신호로 읽는 편이 안전합니다."
        )
    if "C/P" in raw or "강세로 읽지" in raw:
        return (
            "💡 다음부터 이렇게 봅니다\n"
            "주가가 크게 떨어질 때 콜/풋 비율만 보고 '강세'라고 하면 위험합니다.\n"
            "급락 + 콜 많음 = 반등 베팅일 수도, 콜 매도(프리미엄 수취)일 수도 있어요.\n"
            "양쪽 극단이면 '변동성 확대'로 먼저 보세요."
        )
    if "OI" in raw and "급변" in raw:
        return (
            "💡 다음부터 이렇게 봅니다\n"
            "미결제약정(OI)이 하루 만에 크게 늘면, 큰손이 그 가격대에 자리를 잡았다는 뜻입니다.\n"
            "지지/저항보다 이 급변을 먼저 언급하세요."
        )
    if raw:
        # 첫 tip만 부드럽게
        tip = raw.split(" / ")[0]
        return f"💡 다음부터 이렇게 봅니다\n{tip}"
    return ""


# ------------------------------------------------------------------ #
# 과거 학습 섹션
# ------------------------------------------------------------------ #

def learning_section(ticker: str, today_feedback: dict | None = None, ctx: dict | None = None) -> str:
    import learning as learn

    s5 = learn.cumulative_stats(ticker, limit=5)
    s7 = (ctx or {}).get("최근7일") or learn.cumulative_stats(ticker, limit=7)
    if not s5.get("available") and not s7.get("available"):
        return (
            "📚 과거 데이터 학습\n"
            "- 아직 비교할 과거 채점 기록이 부족해요. 리포트가 쌓이면 정확도·놓친 패턴이 여기 표시됩니다."
        )

    stats = s5 if s5.get("available") else s7
    n = stats.get("n") or 0
    L = ["📚 과거 데이터 학습"]
    L.append(
        f"- 지난 {n}일 예측 정확도: "
        f"밴드 {stats.get('band_accuracy_pct')}%, "
        f"지지 {stats.get('support_accuracy_pct')}%, "
        f"방향 {stats.get('direction_accuracy_pct')}%"
    )
    top = stats.get("top_missed_signals") or []
    if top:
        L.append("- 자주 놓친 패턴:")
        for t in top[:3]:
            sig = t.get("signal") or ""
            # 초보자 요약
            if "PUT" in sig.upper() or "풋" in sig:
                L.append(f"  * 풋 쪽 극단/급증 → 하락 위험 신호를 콜보다 우선")
            elif "CALL" in sig.upper() and ("급락" in sig or "결과" in sig):
                L.append(f"  * 콜 극단 ≠ 강세 (급락 시 콜 매도 가능)")
            else:
                short = sig if len(sig) <= 70 else sig[:67] + "..."
                L.append(f"  * {short}")

    tips = (ctx or {}).get("개선지시") or []
    lesson = (today_feedback or {}).get("lesson")
    if lesson or tips:
        L.append("- 이번 예측에 반영한 개선점:")
        if lesson:
            # 초보자 버전 한 줄
            bl = beginner_lesson(lesson, (today_feedback or {}).get("missed_signals"))
            # beginner_lesson 전체 대신 첫 실질 줄
            for line in bl.split("\n")[1:]:
                if line.strip():
                    L.append(f"  * {line.strip()}")
                    break
        for t in tips[:2]:
            if lesson and lesson[:20] in t:
                continue
            L.append(f"  * {t[:90]}")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 섹션 강제 교체
# ------------------------------------------------------------------ #

_SECTION_NEXT = (
    r"(?=^[\U0001F300-\U0001FAFF⭐📊🎯💰🚨🌡️🟢🔴📈🔮⚠️📰📚💡🚦📍🔍]|^⚠️ 이 리포트|\Z)"
)


def replace_section(text: str, header_re: str, block: str) -> str:
    if not block.strip():
        return text
    pat = re.compile(
        rf"(?m)^{header_re}[^\n]*\n(?:.*?\n)*?{_SECTION_NEXT}",
    )
    block = block.strip() + "\n\n"
    if pat.search(text):
        return pat.sub(block, text, count=1)
    return text


def enforce_all(
    narrative: str,
    *,
    title: str | None,
    key_summary: str | None = None,
    temp: str | None,
    levels: str | None,
    band: str | None,
    scenarios: str | None,
    checkpoints: str | None,
    learning: str | None,
    plain_talk: str | None = None,
    signals: str | None = None,
    price_map: str | None = None,
    why: str | None = None,
    banner: str | None = None,
) -> str:
    t = narrative or ""
    # 🎯 한줄요약 중복 제거: 첫 줄만 남김
    titles = list(re.finditer(r"(?m)^🎯\s.*$", t))
    if title and titles:
        first = titles[0]
        t = t[: first.start()] + f"🎯 {title}" + t[first.end():]
        titles = list(re.finditer(r"(?m)^🎯\s.*$", t))
        extra = titles[1:]
        for m in reversed(extra):
            t = t[: m.start()] + t[m.end():]
    elif title:
        t = re.sub(r"(?m)^🎯\s.*$", f"🎯 {title}", t, count=1)

    def _insert_after_title(block: str) -> None:
        nonlocal t
        m = re.search(r"(?m)^📊\s.*$", t)
        if m:
            t = t[: m.end()] + "\n\n" + block.strip() + "\n" + t[m.end():]
        else:
            t = block.strip() + "\n\n" + t

    if banner and "옵션 데이터 신뢰도 낮음" not in t:
        _insert_after_title(banner)

    if plain_talk:
        t = replace_section(t, r"💡", plain_talk)
        if "💡 쉽게 말하면" not in t:
            # 제목 다음, 가격 앞
            if "💰 가격" in t:
                t = t.replace("💰 가격", plain_talk.strip() + "\n\n💰 가격", 1)
            else:
                _insert_after_title(plain_talk)

    if signals:
        t = replace_section(t, r"🚦", signals)
        if "🚦 오늘의 신호" not in t:
            if "📍" in t:
                t = t.replace("📍", signals.strip() + "\n\n📍", 1)
            elif "💰 가격" in t:
                # 가격 뒤에
                t = replace_section(t, r"💰", "💰 가격\n")
                idx = t.find("💰 가격")
                # append after price section via replace
                t = t.replace("📍", signals.strip() + "\n\n📍", 1) if "📍" in t else (
                    t + "\n\n" + signals.strip() + "\n"
                )

    if price_map:
        t = replace_section(t, r"📍", price_map)
        if "📍 오늘의 가격 지도" not in t:
            t = t.rstrip() + "\n\n" + price_map.strip() + "\n"

    if key_summary:
        t = replace_section(t, r"⭐", key_summary)

    if temp:
        t = replace_section(t, r"🌡️", temp)
    if levels:
        pat = re.compile(
            rf"(?m)^🟢[^\n]*\n(?:.*?\n)*?^🔴[^\n]*\n(?:.*?\n)*?{_SECTION_NEXT}",
        )
        block = levels.strip() + "\n\n"
        if pat.search(t):
            t = pat.sub(block, t, count=1)
        else:
            t = replace_section(t, r"🟢", levels)
    if why:
        t = replace_section(t, r"🔍", why)
        if "🔍 왜 이렇게 보나" not in t:
            t = t.rstrip() + "\n\n" + why.strip() + "\n"
    if band:
        t = replace_section(t, r"📈", band)
    if scenarios:
        t = replace_section(t, r"🔮", scenarios)
    if learning:
        t = replace_section(t, r"📚", learning)
        if "📚" not in t:
            t = t.rstrip() + "\n\n" + learning.strip() + "\n"
    if checkpoints:
        t = replace_section(t, r"🎯 오늘 체크포인트", checkpoints)
        if "🎯 오늘 체크포인트" not in t and checkpoints:
            t = replace_section(t, r"🎯", checkpoints)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"
