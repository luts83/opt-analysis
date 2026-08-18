"""ChatGPT(OpenAI) 헬퍼.

- 일일: 실험형 리포트용 초보자 2~3줄(blurb)만 생성.
- 주간: 검증 성적표 해설.
"""
from __future__ import annotations

import json

import config

_SYSTEM_PROMPT = """너는 옵션 관측 일지를 초보 투자자에게 쉽게 풀어 쓰는 기록 도우미다.

목표: 매일 그럴듯한 '예측'이 아니라, 실험 결과를 쌓아 실제로 통하는 신호를 찾는다.
흐름: 어제 옵션 → 오늘 옵션 변화 → 오늘 주가 반응 → 패턴 기록 → 과거 비교.

금지:
- OI/거래량 = 지지·저항·매수·매도 단정
- '팔겠다/사겠다', '$50까지 간다' 식 목표가 확정
- 단일 사례로 새 예측 규칙 선언
- 뉴스, 마크다운 링크, 섹션 번호 지시문

할 일: '💡 오늘 한눈에 보기'용 3~4줄. 어제 옵션 집중 → 오늘 주가 반응 → 결론(단정 금지).
숫자 나열 금지. "그래서 무슨 일이 있었는지"만. 불릿(·) 사용.
비유는 최대 1개만 (예: '$46 문' 앞에 거래 몰림 → 갔다가 넘지 못하고 되돌림). 전문용어·저항선·스트라이크 금지."""


def generate_experiment_blurb(
    data, base, day_over_day=None, feedback=None, learning_context=None, eventinfo=None
) -> str | None:
    """실험형 리포트용 초보자 2~3줄. 실패 시 None."""
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        dod = day_over_day or {}
        fb = feedback or {}
        payload = {
            "티커": data.get("ticker"),
            "날짜": data.get("date"),
            "현재가": data.get("spot"),
            "전일대비_퍼센트": dod.get("spot_change_pct") or base.get("price_change_pct"),
            "옵션거래량배": dod.get("volume_mult"),
            "C/P오늘": dod.get("cpr_today") or base.get("call_put_volume_ratio"),
            "어제관심_콜": (dod.get("prev_top_calls") or [])[:3],
            "채점요약": (fb.get("accuracy") or {}).get("summary"),
            "교훈후보": fb.get("lesson"),
            "저신뢰": bool(base.get("low_confidence")),
            "학습패턴": (learning_context or {}).get("학습패턴"),
        }
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=min(float(config.LLM_TEMPERATURE), 0.4),
            max_tokens=280,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "아래 관측으로 '오늘 한눈에 보기' 3~4줄만 써줘. "
                        "· 불릿 사용. 예측·목표가 단정 금지:\n"
                        + json.dumps(payload, ensure_ascii=False, indent=2)
                    ),
                },
            ],
        )
        text = resp.choices[0].message.content
        if not text:
            return None
        t = text.strip()
        for prefix in ("💡 오늘 한눈에 보기", "💡 쉽게 말하면", "쉽게 말하면", "💡"):
            if t.startswith(prefix):
                t = t[len(prefix) :].lstrip("\n: ：")
        return t.strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"[llm] experiment blurb 실패 → 생략: {e}")
        return None


def generate_report(data, base, anomalies, volume_anomaly, prev, trend,
                    eventinfo=None, day_over_day=None,
                    feedback=None, learning_context=None) -> str | None:
    """하위 호환 — blurb만 반환."""
    return generate_experiment_blurb(
        data, base, day_over_day, feedback, learning_context, eventinfo
    )


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
