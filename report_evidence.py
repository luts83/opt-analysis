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
            parts.append(f"다음 지지 {_fmt_px(next_sup)} 향해 하락 중")
        elif next_res:
            parts.append(f"{_fmt_px(spot)} 마감, 반등 시 {_fmt_px(next_res)} 저항 주시")
        else:
            parts.append(f"{_fmt_px(spot)} 마감")
        return ", ".join(parts)

    # 급등
    if chg is not None and chg >= 3:
        if next_res and spot < next_res:
            return f"{ticker} {chg:.1f}% 급등, {_fmt_px(next_res)} 저항 테스트 임박"
        if next_res and spot >= next_res:
            return f"{ticker} {chg:.1f}% 급등 — {_fmt_px(next_res)} 저항 돌파, 추가 상승 여부 주시"
        return f"{ticker} {chg:.1f}% 급등, {_fmt_px(spot)} 마감"

    # 현재가가 레벨과 거의 같음
    for it in (levels.get("near_support") or []) + (levels.get("strong_support") or []):
        s = it.get("strike")
        if s is not None and abs(float(spot) - float(s)) / float(spot) < 0.005:
            return f"{ticker} {_fmt_px(spot)} 지지선 테스트 중"

    if next_sup and chg is not None and chg < 0:
        return f"{ticker} {_fmt_px(spot)} — 다음 지지 {_fmt_px(next_sup)} 주시"
    if next_res:
        return f"{ticker} {_fmt_px(spot)} — {_fmt_px(next_res)} 저항 주시"
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
    """초보자용 짧은 근거."""
    oi = it.get("oi")
    vol = it.get("volume")
    if side == "res":
        if oi and vol:
            return f"콜 거래 {vol:,}·콜 대기(OI) {oi:,}개가 몰린 자리"
        if oi:
            return f"콜 대기(OI) {oi:,}개가 몰린 자리"
        if vol:
            return f"콜 거래 {vol:,}계약이 몰린 자리"
        if it.get("flipped_from_support"):
            return "예전에 지지였다가 뚫려, 지금은 저항으로 바뀐 자리"
        return "옵션 매물이 모여 있는 자리"
    # support
    if oi and vol:
        return f"풋 거래 {vol:,}·풋 대기(OI) {oi:,}개가 몰린 자리"
    if oi:
        return f"풋 대기(OI) {oi:,}개가 몰린 자리"
    if vol:
        return f"풋 거래 {vol:,}계약이 몰린 자리"
    if it.get("flipped_from_resist"):
        return "예전에 저항이었다가 뚫려, 지금은 지지로 바뀐 자리"
    return "옵션 매수 대기가 모여 있는 자리"


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
    # OI/거래 큰 쪽을 우선해 '가장 중요한 자리' 선정
    def _weight(it: dict) -> tuple:
        return (-(it.get("oi") or 0), -(it.get("volume") or 0), abs(float(it["strike"]) - spot))

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
        L.append(f"{n}. {_fmt_px(rs)}가 가장 중요한 자리")
        L.append(f"   {_level_why(resist, side='res')}.")
        L.append(f"   → 단기 저항이자, 돌파 여부를 확인할 가격입니다.")
        n += 1
    if support:
        ss = float(support["strike"])
        L.append(f"{n}. {_fmt_px(ss)}는 첫 번째 방어선")
        L.append(f"   {_level_why(support, side='sup')}.")
        L.append(f"   → 이 가격 아래로 내려가면 하락세가 강해질 수 있어 경계합니다.")
        n += 1
    L.append(f"{n}. 이전 분석의 교훈")
    # 교훈 문장을 2줄로 감싸기
    lesson = _lesson_takeaway(feedback, learning_context)
    # 80자 내외로 줄바꿈
    if len(lesson) > 90:
        cut = lesson.rfind(" ", 0, 90)
        if cut < 40:
            cut = 90
        L.append(f"   {lesson[:cut].strip()}")
        L.append(f"   {lesson[cut:].strip()}")
    else:
        L.append(f"   {lesson}")
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

    # 표시용 라벨
    if senti in ("강세", "약세", "중립"):
        label = senti
    elif senti == "반등 시도 국면":
        label = "혼조 (반등 시도)"
    elif senti == "양방향 극단 베팅":
        label = "혼조 (양방향 극단)"
    else:
        label = senti

    L = ["🌡️ 시장 온도", f"심리: {label} (콜/풋 비율 {cpr})"]
    if tags:
        L.append(f"태그: {', '.join(tags)}")

    if chg is not None and chg <= -5:
        L.append(
            f"해석: 주가는 {chg:+.1f}% 급락했지만 옵션은 콜/풋이 "
            f"{'콜 우세' if cpr >= 1.2 else '비교적 균형' if cpr >= 0.83 else '풋 우세'}."
        )
        L.append("  → ① 급락 후 반등 베팅(콜 매수)  ② 콜 매도자가 프리미엄 수취 중")
        L.append("⚠️ 급락 국면에서 콜/풋 비율만으로 '강세' 판단은 위험.")
    elif chg is not None and chg >= 5:
        L.append(
            f"해석: 주가 {chg:+.1f}% 급등 + 콜/풋 {cpr}. "
            "차익실현·헤지 콜/풋이 섞였을 수 있어요."
        )
    else:
        up = round(cpr / (1 + cpr) * 100)
        L.append(
            f"해석: 옵션 거래량 기준 상승 쪽 ~{up}% / 하락 쪽 ~{100 - up}% "
            f"→ '{label}'"
            + (" (실적 전후라 참고용)" if in_earnings else "")
        )
        L.append("  (콜=상승에 베팅하는 계약, 풋=하락에 베팅하는 계약의 거래량 비)")
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
        meaning = f"\"{_fmt_px(s)}까지 떨어지면 사겠다\"는 대기 매수세"
        if oi:
            meaning += f" (풋 미결제약정 {oi:,}개)"
        if s > spot * 1.005:
            # 이미 위로 뚫림 → 저항 역할
            out["flipped_to_resist"].append(
                _copy(
                    it,
                    kind="전환저항",
                    basis=it.get("basis") or "풋 OI 밀집",
                    flipped_from_support=True,
                    note=f"현재가({_fmt_px(spot)}) 위 → 이미 뚫린 지지, 이제 저항 역할",
                    meaning=f"예전 지지. 반등 시 {_fmt_px(s)} 매물 저항 가능",
                )
            )
        else:
            out["strong_support"].append(
                _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
            )

    for it in levels.get("strong_resistance") or []:
        s = float(it["strike"])
        oi = it.get("oi")
        meaning = f"\"{_fmt_px(s)}에 팔겠다\"는 대기 매도세"
        if oi:
            meaning += f" (콜 미결제약정 {oi:,}개)"
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
        meaning = f"현재가 근처 풋 거래 집중 → {_fmt_px(s)} 하방 관심"
        if vol:
            meaning += f" (거래 {vol:,}계약)"
        out["near_support"].append(
            _copy(it, note=f"현재가 대비 {_rel(spot, s)}", meaning=meaning)
        )

    for it in levels.get("near_resistance") or []:
        s = float(it["strike"])
        if s < spot * 0.99:
            continue
        vol = it.get("volume")
        meaning = f"현재가 근처 콜 거래 집중 → {_fmt_px(s)} 상방 매물"
        if vol:
            meaning += f" (거래 {vol:,}계약)"
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

    L = ["🟢 지지선 (현재가보다 아래 = 하방 방어)"]
    ss = levels.get("strong_support") or []
    ns = levels.get("near_support") or []
    # 진짜 아래만
    if spot is not None:
        ss = [x for x in ss if float(x["strike"]) <= float(spot) * 1.005]
        ns = [x for x in ns if float(x["strike"]) <= float(spot) * 1.005]
    if ss:
        L.append("강한 지지")
        for it in ss[:2]:
            L.extend(_fmt_item(it))
    if ns:
        L.append("단기 지지")
        for it in ns[:2]:
            L.extend(_fmt_item(it))
    if not ss and not ns:
        L.append("- (현재가 아래 뚜렷한 지지 없음)")

    L.append("🔴 저항선 (현재가보다 위 = 상방 저항)")
    nr = levels.get("near_resistance") or []
    sr = levels.get("strong_resistance") or []
    if spot is not None:
        nr = [x for x in nr if float(x["strike"]) >= float(spot) * 0.995]
        sr = [x for x in sr if float(x["strike"]) >= float(spot) * 0.995]
    if nr:
        L.append("단기 저항")
        for it in nr[:2]:
            L.extend(_fmt_item(it))
    if sr:
        L.append("강한 저항")
        for it in sr[:2]:
            L.extend(_fmt_item(it))
    if not nr and not sr:
        L.append("- (현재가 위 뚜렷한 저항 없음)")
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

    L = ["📈 예상 범위 (옵션 시장 기준)"]
    for r in rows:
        date_bit = f"(~{r['date']})" if r.get("date") else ""
        L.append(
            f"{r['label']}{date_bit}: ${_fmt_num(r['lower'])} ~ ${_fmt_num(r['upper'])} "
            f"(±{r.get('band_pct')}%)"
        )
    L.append("계산: 현재가 ± ATM 스트래들(같은 행사가 콜+풋 가격 합)")
    L.append("의미: 옵션 시장이 매긴 '흔히 움직일 수 있는' 대략 범위 (확정 아님)")
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
    r"(?=^[\U0001F300-\U0001FAFF⭐📊🎯💰🚨🌡️🟢🔴📈🔮⚠️📰📚]|^⚠️ 이 리포트|\Z)"
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
) -> str:
    t = narrative or ""
    if title:
        t = re.sub(r"(?m)^🎯\s.*$", f"🎯 {title}", t, count=1)
    if key_summary:
        t = replace_section(t, r"⭐", key_summary)
        if "⭐ 오늘의 핵심" not in t:
            # 🎯 한 줄 다음, 없으면 💰 앞에 삽입
            m = re.search(r"(?m)^🎯\s.*$", t)
            if m:
                insert_at = m.end()
                t = t[:insert_at] + "\n\n" + key_summary.strip() + "\n" + t[insert_at:]
            elif "💰 가격" in t:
                t = t.replace("💰 가격", key_summary.strip() + "\n\n💰 가격", 1)
            else:
                t = key_summary.strip() + "\n\n" + t
    if temp:
        t = replace_section(t, r"🌡️", temp)
    if levels:
        # 🟢부터 🔴까지 한 덩어리로
        pat = re.compile(
            rf"(?m)^🟢[^\n]*\n(?:.*?\n)*?^🔴[^\n]*\n(?:.*?\n)*?{_SECTION_NEXT}",
        )
        block = levels.strip() + "\n\n"
        if pat.search(t):
            t = pat.sub(block, t, count=1)
        else:
            t = replace_section(t, r"🟢", levels)
    if band:
        t = replace_section(t, r"📈", band)
    if scenarios:
        t = replace_section(t, r"🔮", scenarios)
    if learning:
        # 체크포인트 앞에 삽입/교체
        t = replace_section(t, r"📚", learning)
        if "📚 과거 데이터 학습" not in t:
            if "🎯 오늘 체크포인트" in t:
                t = t.replace("🎯 오늘 체크포인트", learning.strip() + "\n\n🎯 오늘 체크포인트", 1)
            else:
                t = t.rstrip() + "\n\n" + learning.strip() + "\n"
    if checkpoints:
        t = replace_section(t, r"🎯 오늘 체크포인트", checkpoints)
        # 헤더가 '🎯 오늘'이 아닐 수도
        if "🎯 오늘 체크포인트" not in t and checkpoints:
            t = replace_section(t, r"🎯", checkpoints)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"
