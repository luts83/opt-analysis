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
- 전문 용어(콜/풋/OI/스트래들/V-OI 등)는 나올 때마다 즉시 쉽게 풀어서 설명한다.
- 비유를 적극 사용한다 (예: 저항선=그 가격표를 든 대기자/매물, 지지선=사겠다는 대기자).
- 숫자만 나열하지 말고 '왜 그런지', '어제 대비 무엇이 바뀌었는지'를 해석한다.
- 제공된 '최근 추이' 데이터를 활용해 흐름(며칠째 상승/하락, 심리 변화 등)을 짚는다.
- '그래서 어떻게 활용할지' 액션 포인트를 반드시 포함한다(보유 중/매수 고민 중/주의 신호).
- 제공된 값만 사용하고 숫자를 지어내지 않는다.
- 톤: 친한 친구가 편하게 설명하는 말투. 이모지로 섹션을 구분해 스캔하기 쉽게.

[이벤트/뉴스/어닝 옵션 반응 — 매우 중요]
- 입력의 '이벤트'에 '실적발표(어닝)' 경고가 있으면, 리포트 맨 위에 눈에 띄게 경고한다.
- 어닝 임박/직후이면 콜/풋 볼륨 기반 '강세/약세'를 절대 단정하지 말고 '보류/주의'로 다룬다.
  EPS 서프라이즈(상회/하회)가 있으면 그 숫자를 반드시 언급한다.
- '옵션반응'이 있으면 반드시 별도 섹션으로 해석한다:
  밴드(변동성 기대) 확대/축소, 옵션 거래량 변화, 심리 전환 여부를 쉬운 말로.
  어닝 국면에선 밴드·거래량이 방향성(콜/풋)보다 더 중요하다고 분명히 말한다.
- '뉴스헤드라인'은 제공된 사실만 사용한다(없는 내용은 지어내지 말 것).
- '가격주의' 노트가 있으면 현재가를 단정하지 말고 그 주의를 전달한다.

다음 구조(마크다운, 이모지 헤더)로 작성한다:
1. 📊 제목 줄:  "오늘의 {티커} 옵션 시장 이야기 - {날짜}"
2. 🚨 이벤트 경고 (어닝 임박/직후 또는 가격 이상 급변이 있을 때만; 없으면 생략)
3. 💰 지금 주가 + 어제 대비 (한 줄; 가격주의가 있으면 함께)
4. 🎯 한 줄 요약 (어닝 국면이면 '심리 단정 보류' + EPS 결과 한 줄)
5. 🌊 어닝 전후 옵션 반응 (옵션반응이 있을 때만; 밴드/거래량/심리 변화)
6. 🟢 지지선 / 🔴 저항선 (비유로 설명, 각 1~2개)
7. 🌡️ 시장 온도 (콜/풋 비율 — 어닝 국면이면 '참고용, 단정 금지' 명시)
8. 📈 이번주 예상 범위 (스트래들 밴드가 뭔지 풀어서)
9. 📰 관련 뉴스 (제공된 헤드라인 2~4개; 없으면 생략)
10. ⚠️ 오늘 특이한 일 (거래량 급증·OI 변화·어닝 등)
11. 🎯 그래서 뭘 해야 하나 (보유 중 / 매수 고민 중 / 주의 — 어닝이면 변동성 주의 강조)
12. 맨 끝: "⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다."

OI 데이터가 '전일 기준'이라고 표시돼 있으면, 그 사실을 자연스럽게 한 번 알려준다."""


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
        "가격주의": (eventinfo.get("price") or {}).get("note"),
        "뉴스헤드라인": [
            {"제목": n.get("title"), "매체": n.get("publisher")}
            for n in (eventinfo.get("news") or [])[:5]
        ],
    }


def _build_payload(data, base, anomalies, volume_anomaly, prev, trend,
                   eventinfo=None) -> dict:
    spot = data["spot"]
    prev_close = data.get("previous_close")
    change_pct = (
        round((spot - prev_close) / prev_close * 100, 2) if prev_close else None
    )
    cpr = base.get("call_put_volume_ratio")
    up_pct = round(cpr / (1 + cpr) * 100) if cpr else None
    return {
        "티커": data["ticker"],
        "날짜": data["date"],
        "현재가": spot,
        "전일종가": prev_close,
        "전일대비_퍼센트": change_pct,
        "이벤트": _events_block(eventinfo),
        "OI_데이터_신선도": _oi_freshness_text(base),
        "시장심리": base.get("sentiment"),
        "콜풋볼륨비": cpr,
        "상승베팅_비율_퍼센트": up_pct,  # 이 값을 '100명 중 N명 상승'으로 그대로 사용
        "하락베팅_비율_퍼센트": (100 - up_pct) if up_pct is not None else None,
        "총콜볼륨": base.get("total_call_volume"),
        "총풋볼륨": base.get("total_put_volume"),
        "만기별": [_expiry_block(em) for em in base.get("expiry_metrics", {}).values()],
        "거래량이상": volume_anomaly,
        "OI급변_이상신호": anomalies[:6],
        "어제요약": _prev_summary(prev),
        "최근추이": trend,
    }


def generate_report(data, base, anomalies, volume_anomaly, prev, trend,
                    eventinfo=None) -> str | None:
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        payload = _build_payload(
            data, base, anomalies, volume_anomaly, prev, trend, eventinfo
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
                    "일반인이 읽기 쉽게, 비유와 해석 위주로:\n"
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
