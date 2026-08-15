"""리포트 본문 오케스트레이션 — 실험·학습형 일일 리포트.

순서: 오늘 결과 → 옵션 변화 → 주가 반응 → 관심 가격 → 교훈 → 누적 → 다음 관찰 → 한계
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

    본문 골격은 항상 시스템 실험형 조립. LLM은 초보자용 2~3줄만 덧붙인다.
    """
    import llm
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

    blurb = llm.generate_experiment_blurb(
        data, base, day_over_day, fb, ctx, eventinfo
    )
    if blurb:
        src = "openai"
        # 제목 다음, 가격(①) 앞에 삽입
        marker = "① 오늘 결과"
        if marker in body:
            body = body.replace(
                marker,
                f"💡 쉽게 말하면\n{blurb.strip()}\n\n{marker}",
                1,
            )
        else:
            body = f"💡 쉽게 말하면\n{blurb.strip()}\n\n{body}"

    body = report_polish.polish_narrative(body)
    body = events.with_linked_news(body, eventinfo)
    return body, src
