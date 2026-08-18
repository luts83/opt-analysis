"""리포트 본문 오케스트레이션 — 실험·학습형 일일 리포트.

순서: 한눈에 → 어제옵션→오늘주가 → 반응가격 → 옵션변화 → 해석 → 검증 → 누적 → 한계
"""
from __future__ import annotations

import events
import report_flow


def build_friendly_fallback(
    data, base, anomalies, volume_anomaly, prev, eventinfo=None, day_over_day=None,
    feedback=None, learning_context=None,
) -> str:
    return report_flow.assemble_experiment_report(
        data,
        base,
        anomalies=anomalies,
        volume_anomaly=volume_anomaly,
        day_over_day=day_over_day,
        eventinfo=eventinfo,
        feedback=feedback,
        learning_context=learning_context,
    )


def build_narrative(
    data, base, anomalies, volume_anomaly, prev, trend, eventinfo=None, day_over_day=None,
    feedback=None, learning_context=None,
) -> tuple[str, str]:
    """(본문, 출처). 출처: 'openai' | 'rule'.

    본문 골격은 항상 시스템 실험형 조립(비유 섞은 한눈에 보기 포함).
    LLM은 사용하지 않음 — 숫자와 어긋날 수 있어 규칙 기반이 더 정확함.
    """
    import report_polish

    fb = feedback if feedback is not None else data.get("prediction_feedback")
    ctx = learning_context if learning_context is not None else data.get("learning_context")

    body = report_flow.assemble_experiment_report(
        data,
        base,
        anomalies=anomalies,
        volume_anomaly=volume_anomaly,
        day_over_day=day_over_day,
        eventinfo=eventinfo,
        feedback=fb,
        learning_context=ctx,
    )
    src = "rule"

    body = report_polish.polish_narrative(body)
    body = events.with_linked_news(body, eventinfo)
    return body, src
