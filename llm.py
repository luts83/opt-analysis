"""ChatGPT(OpenAI) 자연어 해설 생성.

- API 키(.env 의 OPENAI_API_KEY)가 없거나 호출 실패 시 None 을 반환한다.
  → 호출부(insights)에서 규칙 기반 요약으로 자동 폴백.
"""
from __future__ import annotations

import json

import config

_SYSTEM_PROMPT = (
    "너는 주식 옵션 데이터를 해설하는 애널리스트다. "
    "제공되는 계산된 지표(현재가, 콜/풋 볼륨비, 저항/지지 OI, 예상 밴드, "
    "이상 신호 등)만 근거로, 한국어로 간결하고 사실에 충실한 일일 요약을 쓴다. "
    "숫자를 지어내지 말고 제공된 값만 사용한다. 5~8개의 불릿으로 정리하고, "
    "마지막에 '주의' 한 줄을 덧붙인다. 투자 권유가 아니라 관찰 위주로 서술한다."
)


def _build_payload(data: dict, base: dict, anomalies: list[dict],
                   volume_anomaly: dict | None) -> dict:
    """LLM 에 넘길 핵심 지표만 추린 dict (원본 체인 전체는 보내지 않음)."""
    return {
        "ticker": data["ticker"],
        "date": data["date"],
        "spot": data["spot"],
        "previous_close": data.get("previous_close"),
        "oi_data_stale": base.get("oi_data_stale"),
        "sentiment": base.get("sentiment"),
        "call_put_volume_ratio": base.get("call_put_volume_ratio"),
        "expiry_bands": {
            role: {
                "date": em["date"],
                "band": em["straddle"] and [em["straddle"]["lower"], em["straddle"]["upper"]],
                "band_pct": em["straddle"] and em["straddle"]["band_pct"],
                "resistance": em["call_oi_clusters"][:3],
                "support": em["put_oi_clusters"][:3],
            }
            for role, em in base.get("expiry_metrics", {}).items()
        },
        "top_voi": base.get("top_voi", [])[:5],
        "top_volume": base.get("top_volume", [])[:5],
        "anomalies": anomalies[:8],
        "volume_anomaly": volume_anomaly,
    }


def generate_summary(data: dict, base: dict, anomalies: list[dict],
                     volume_anomaly: dict | None) -> str | None:
    """ChatGPT 로 자연어 요약 생성. 실패/키없음 → None."""
    if not config.LLM_ENABLED or config.LLM_PROVIDER != "openai":
        return None
    if not config.OPENAI_API_KEY:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        payload = _build_payload(data, base, anomalies, volume_anomaly)
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "다음 지표로 오늘의 옵션 시장 요약을 작성해줘:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2),
                },
            ],
        )
        text = resp.choices[0].message.content
        return text.strip() if text else None
    except Exception as e:  # noqa: BLE001
        # 조용히 실패시키지 않고 사유를 반환값 대신 로그로 남긴다.
        print(f"[llm] OpenAI 호출 실패 → 규칙기반 폴백: {e}")
        return None
