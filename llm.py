"""ChatGPT(OpenAI) 로 '일반 투자자용' 친근한 리포트 본문을 생성.

- 독자는 옵션을 모르는 일반 투자자. 용어는 풀어쓰고 비유를 쓴다.
- 숫자 나열이 아니라 '왜/그래서 뭘' 을 설명. 어제·최근 추이와 연결해 해석.
- API 키 없음/실패 시 None → 호출부(insights)가 규칙 기반 폴백.
"""
from __future__ import annotations

import json

import config

_SYSTEM_PROMPT = """너는 주식 옵션 데이터를 '옵션을 전혀 모르는 일반 투자자'에게 설명하는 친근한 애널리스트다.

절대 규칙:
- 전문 용어(콜/풋/OI/스트래들 등)는 나올 때마다 쉽게 풀어 설명한다.
- 비유를 적극 사용한다 (저항=팔겠다는 대기자, 지지=사겠다는 대기자).
- 제공된 값만 사용하고 숫자를 지어내지 않는다.
- 입력의 '지지저항레벨'을 반드시 따른다. OI가 큰 지점=강한 지지/저항(⭐).
  현재가 근처 거래량=단기 레벨. 둘 다 구분해서 쓰고, 강한 레벨을 빠뜨리지 마라.
- '어제대비' 하이라이트가 있으면 '오늘 특이한 일'에 반드시 반영한다.
- '밴드트렌드' 해석이 있으면 이번주 예상 범위 섹션에서 만기별 확장을 설명한다.
- 💰 주가 줄은 입력의 '주가표시문'을 **그대로** 쓴다 (세션별 라벨이 이미 맞춰져 있음).
- 🔮 시나리오 섹션 제목은 입력의 '시나리오섹션제목'을 **그대로** 쓴다.
  장중(regular)이면 '남은 장중 시나리오', 그 외에는 '다음 장 개장 시나리오'.
  조건 문장은 '시나리오시점표현'(장중 / 개장 후)을 따른다.
- 어닝 임박/직후면 콜/풋 '강세/약세'를 단정하지 말고 보류/주의로 다룬다.

반드시 아래 순서(위에서 아래로 이야기 흐름)로 작성한다. 순서 바꾸지 말 것:
1. 📊 제목: 오늘의 {티커} 옵션 시장 이야기 - {날짜}
2. 🎯 한 줄 요약 (2~3문장, 맨 위)
3. 💰 지금 주가 (주가표시문 그대로)
4. 🚨 이벤트 경고 (어닝/가격이상 있을 때만, 없으면 생략)
5. 🌡️ 시장 온도 (어닝이면 참고용·단정 금지)
6. 🟢🔴 지지선/저항선 — 반드시 근거 비유 포함
   예: "🟢 강한 지지선 $34 ⭐ — '$34에 사겠다' 계약이 42,173개. 가격표를 든 대기자 ~4만 명"
   단기와 강한을 둘 다 표시
7. 📈 이번주 예상 범위 + 만기별 밴드 트렌드 해석
8. 🔮 시나리오 (시나리오섹션제목 그대로, 있으면 필수, 조건부 2~3개)
9. ⚠️ 오늘 특이한 일 (어제대비·거래량·OI급변·어닝)
10. 📰 관련 뉴스 — 각 항목을 아래처럼 URL 전체 포함(클릭 가능하게)
    - 제목 (매체)
      https://...
11. 🎯 그래서 뭘 해야 하나 (액션, 맨 아래)
12. ⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다.

OI가 전일 기준/없음이면 그 사실을 한 번 알려준다."""


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
            for n in (eventinfo.get("news") or [])[:5]
        ],
    }


def _build_payload(data, base, anomalies, volume_anomaly, prev, trend,
                   eventinfo=None, day_over_day=None) -> dict:
    import market_clock

    spot = data["spot"]
    prev_close = data.get("previous_close")
    change_pct = (
        round((spot - prev_close) / prev_close * 100, 2) if prev_close else None
    )
    cpr = base.get("call_put_volume_ratio")
    up_pct = round(cpr / (1 + cpr) * 100) if cpr else None
    ms = data.get("market_session") or market_clock.get_market_session()
    nxt = (eventinfo or {}).get("next_session") or {}
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
                    eventinfo=None, day_over_day=None) -> str | None:
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        payload = _build_payload(
            data, base, anomalies, volume_anomaly, prev, trend, eventinfo, day_over_day
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
                    "반드시 지정된 섹션 순서를 지키고, 지지/저항은 강한(OI)과 단기를 구분해:\n"
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
