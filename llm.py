"""ChatGPT(OpenAI) 로 '일반 투자자용' 친근한 리포트 본문을 생성.

- 독자는 옵션을 모르는 일반 투자자. 용어는 풀어쓰고 비유를 쓴다.
- 숫자 나열이 아니라 '왜/그래서 뭘' 을 설명. 어제·최근 추이와 연결해 해석.
- API 키 없음/실패 시 None → 호출부(insights)가 규칙 기반 폴백.
"""
from __future__ import annotations

import json

import config

_SYSTEM_PROMPT = """너는 주식 옵션 데이터를 '옵션을 전혀 모르는 일반 투자자'에게 설명하는 애널리스트다.

작성 원칙 (반드시):
- 결론 → 가격 → 근거 → 상세 데이터 순서.
- 맨 위 '💡 쉽게 말하면' 2~3줄. 비유는 1~2문장만. 옵션 용어 강의 금지.
- 그 다음 주가표시문을 그대로.
- OI 많음 = 저항/지지라고 쓰지 마라. '관심 가격 / 저항 후보 / 지지 후보'.
- '팔겠다' '사겠다' 금지. '해당 가격에 옵션 포지션이 많이 쌓여 있음'.
- 거래량 많음 = 바로 저항/지지 금지. 먼저 '옵션 관심 가격'.
- 옵션 밴드 상단=저항, 하단=지지로 쓰지 마라. 밴드는 '예상 변동 범위'.
- 멀리 있는 큰 OI($50 등)를 현재가보다 중요하다고 1순위로 쓰지 마라.
- 위쪽에 콜 관심이 연속이면 '한 칸 다음 저항'이 아니라 돌파 후 확장 구간을 제시.
- C/P는 방향이 아니라 거래 구성비. 급락+콜우세면 방향 불확실.
- 콜 극단+풋 극단이면 강세/약세를 고르지 말고 변동성 확대.
- 저신뢰(OI 없음)면 강한 지지/저항 금지.
- 📰 뉴스 쓰지 마라. 섹션 번호/'제목:' 금지.

출력 구조:
📊 오늘의 {티커} 옵션 시장 이야기 - {날짜}
💡 쉽게 말하면 (입력 초보자요약 우선)
💰 가격 (주가표시문 그대로)
🚦 오늘의 신호
📍 가격 지도
📈 오늘의 시나리오 (상단 확장 시나리오 포함)
🔍 왜 이렇게 보나?
📚 어제 예측 vs 오늘 결과 (있으면)
⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다.

금지: 뉴스, 마크다운 링크, 근거 없는 숫자, OI=매도세 단정."""




def _oi_freshness_text(base: dict) -> str:
    src = base.get("oi_source")
    if src == "실시간":
        return "실시간(오늘 장 기준)"
    if src and "전일" in src:
        return "전일 종가 기준 (오늘 아직 미갱신 — 큰 흐름 참고용)"
    return "데이터 없음"


def _expiry_block(em: dict) -> dict:
    st = em.get("straddle")
    return {
        "만기": em["date"],
        "예상밴드": [st["lower"], st["upper"]] if st else None,
        "변동폭_퍼센트": st["band_pct"] if st else None,
        "저항선_콜OI밀집": em.get("call_oi_clusters", [])[:2],
        "지지선_풋OI밀집": em.get("put_oi_clusters", [])[:2],
    }


def _prev_summary(prev: dict | None) -> dict | None:
    if not prev:
        return None
    m = prev.get("metrics", {})
    return {
        "날짜": prev.get("date"),
        "주가": prev.get("spot"),
        "심리": m.get("sentiment"),
        "콜풋볼륨비": m.get("call_put_volume_ratio"),
    }


def _events_block(eventinfo: dict | None) -> dict | None:
    if not eventinfo:
        return None
    earn = eventinfo.get("earnings")
    react = eventinfo.get("options_reaction")
    nxt = eventinfo.get("next_session")
    return {
        "실적발표": (
            {
                "국면": earn.get("phase"),
                "발표일": earn.get("date"),
                "경고문": earn.get("message"),
                "EPS예상": earn.get("eps_estimate"),
                "EPS실제": earn.get("eps_reported"),
                "서프라이즈_퍼센트": earn.get("surprise_pct"),
            }
            if earn
            else None
        ),
        "옵션반응": react,
        "다음장시나리오": nxt,
        "가격주의": (eventinfo.get("price") or {}).get("note"),
        "뉴스헤드라인": [
            {
                "제목": n.get("title"),
                "매체": n.get("publisher"),
                "URL": n.get("link"),
            }
            for n in (eventinfo.get("news") or [])[:3]
        ],
    }


def _build_payload(data, base, anomalies, volume_anomaly, prev, trend,
                   eventinfo=None, day_over_day=None,
                   feedback=None, learning_context=None) -> dict:
    import market_clock
    import learning

    spot = data["spot"]
    prev_close = data.get("previous_close")
    change_pct = (
        round((spot - prev_close) / prev_close * 100, 2) if prev_close else None
    )
    cpr = base.get("call_put_volume_ratio")
    up_pct = round(cpr / (1 + cpr) * 100) if cpr else None
    ms = data.get("market_session") or market_clock.get_market_session()
    nxt = (eventinfo or {}).get("next_session") or {}
    fb = feedback if feedback is not None else data.get("prediction_feedback")
    ctx = learning_context if learning_context is not None else data.get("learning_context")
    return {
        "티커": data["ticker"],
        "날짜": data["date"],
        "시장세션": ms,
        "시장세션한글": market_clock.session_label_ko(ms),
        "주가표시문": market_clock.format_price_line(data),
        "시나리오섹션제목": nxt.get("section_title")
        or market_clock.scenario_section_title(ms),
        "시나리오시점표현": nxt.get("when_phrase")
        or market_clock.scenario_when_phrase(ms),
        "어제예측검증_본문": learning.format_feedback_section(fb) or None,
        "어제예측검증": fb,
        "학습컨텍스트": ctx,
        "현재가_분석기준": spot,
        "정규장종가": data.get("regular_close"),
        "확장장가_프리또는애프터": data.get("extended_price"),
        "확장장_정규장대비_퍼센트": data.get("extended_vs_regular_pct"),
        "가격소스세션": data.get("session"),
        "전일종가": prev_close,
        "전일대비_퍼센트": change_pct,
        "이벤트": _events_block(eventinfo),
        "OI_데이터_신선도": _oi_freshness_text(base),
        "시장심리": base.get("sentiment"),
        "시장심리태그": base.get("sentiment_tags") or [],
        "시장심리_C/P단독": base.get("sentiment_raw"),
        "한줄요약_시스템초안": __import__("report_evidence").one_liner(data, base, eventinfo),
        "초보자요약": __import__("report_evidence").plain_talk_block(data, base, eventinfo),
        "신호표시문": __import__("report_evidence").signals_block(data, base, eventinfo),
        "가격지도": __import__("report_evidence").price_map_block(data, base),
        "왜이렇게보나": __import__("report_evidence").why_block(data, base, eventinfo),
        "지지저항표시문": __import__("report_evidence").levels_block(base.get("levels"), spot),
        "시장온도표시문": __import__("report_evidence").sentiment_block(base),
        "과거학습표시문": __import__("report_evidence").learning_section(
            data.get("ticker", ""), fb, ctx
        ),
        "콜풋볼륨비": cpr,
        "상승베팅_비율_퍼센트": up_pct,
        "하락베팅_비율_퍼센트": (100 - up_pct) if up_pct is not None else None,
        "총콜볼륨": base.get("total_call_volume"),
        "총풋볼륨": base.get("total_put_volume"),
        "지지저항레벨": base.get("levels"),
        "밴드트렌드": base.get("band_trend"),
        "어제대비": day_over_day,
        "만기별": [_expiry_block(em) for em in base.get("expiry_metrics", {}).values()],
        "거래량이상": volume_anomaly,
        "OI급변_이상신호": anomalies[:6],
        "어제요약": _prev_summary(prev),
        "최근추이": trend,
        "거래집중_콜": (base.get("top_call_volume") or [])[:5],
        "거래집중_풋": (base.get("top_put_volume") or [])[:5],
    }


def generate_report(data, base, anomalies, volume_anomaly, prev, trend,
                    eventinfo=None, day_over_day=None,
                    feedback=None, learning_context=None) -> str | None:
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        payload = _build_payload(
            data, base, anomalies, volume_anomaly, prev, trend, eventinfo, day_over_day,
            feedback=feedback, learning_context=learning_context,
        )
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "아래 데이터로 오늘의 리포트를 작성해줘. "
                    "짧고 리듬 있게, 번호 초안/뉴스 섹션 금지. "
                    "학습컨텍스트.개선지시가 있으면 반영해:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2),
                },
            ],
        )
        text = resp.choices[0].message.content
        return text.strip() if text else None
    except Exception as e:  # noqa: BLE001
        print(f"[llm] OpenAI 호출 실패 → 규칙기반 폴백: {e}")
        return None


# ------------------------------------------------------------------ #
# 주간 검증 리포트 (백테스트 성적표 해설)
# ------------------------------------------------------------------ #

_WEEKLY_SYSTEM_PROMPT = """너는 지난 한 주 '옵션 시장의 예측'이 실제 주가와 얼마나 맞았는지 채점하고,
그 결과를 '옵션을 모르는 일반 투자자'에게 쉽게 설명하는 애널리스트다.

절대 규칙:
- 전문 용어는 나올 때마다 쉽게 풀어 설명하고 비유를 쓴다.
- 제공된 점수/데이터만 사용하고 숫자를 지어내지 않는다.
- '무엇이 잘 맞았고 무엇이 빗나갔는지', 그리고 '그게 무슨 의미인지'를 해석한다.
- 옵션 시장이 미리 신호를 줬는지(예: 특정 방향/저항 근처 집중) 데이터 근거로만 언급.
  뉴스 내용을 모르면 '뉴스 확인 필요'로만 표시.
- 최근 몇 주 추이가 있으면 '어떤 지표가 꾸준히 맞고 못 맞는지'를 짚는다.
- 톤: 친근하고 솔직하게. 이모지로 섹션 구분.

다음 구조(마크다운, 이모지 헤더)로 작성한다:
1. 🧾 제목 줄(그대로): 이번 주 {티커} 옵션 예측 성적표 - {주간}   ← "제목:" 같은 접두어 없이 제목만
2. 🏆 종합 성적 (등급/점수를 한 줄로, 잘했으면 칭찬 못했으면 솔직히)
3. 📐 예상 범위 vs 실제 (밴드가 실제 변동을 담았는지, 비유로)
4. 🔴 저항선 / 🟢 지지선 채점 (예상 대비 실제 고가/저가)
5. 🧭 방향 예측 채점 (강세/약세 예상 vs 실제 주간 수익률)
6. 💡 이번 주 배운 것 (어떤 지표가 잘 맞았나 / 다음 주 참고사항)
7. 📅 최근 추이 (최근 몇 주 정확도 흐름 — 데이터 있을 때만)
8. 맨 끝: "⚠️ 이 리포트는 투자 조언이 아니라 예측 검증 기록입니다."

예측이 그 주 첫 스냅샷 기준이거나 데이터가 일부만 있으면 그 사실을 한 번 알려준다."""


def generate_weekly(payload: dict) -> str | None:
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            messages=[
                {"role": "system", "content": _WEEKLY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "아래 채점 결과로 주간 검증 리포트를 작성해줘. "
                    "일반인이 읽기 쉽게, 비유와 해석 위주로:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2),
                },
            ],
        )
        text = resp.choices[0].message.content
        return text.strip() if text else None
    except Exception as e:  # noqa: BLE001
        print(f"[llm] 주간 OpenAI 호출 실패 → 규칙기반 폴백: {e}")
        return None
