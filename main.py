"""엔트리포인트.

config.TICKERS 순회 → 각 종목:
  데이터 수집(만기선택 포함) → 어제/이력 로드 → 개별행 보강 → 지표 계산
  → 이상신호(anomalies) → 자연어 인사이트 → 스냅샷 저장 → 리포트 출력.

Phase 0: 이메일 전송 없음. print() 로만 출력.
"""
from __future__ import annotations

import argparse
import sys
import traceback

import config
import insights as insights_mod
import metrics
import report_builder
import snapshot_store
from data_fetch import fetch_ticker


def process_ticker(ticker: str, save: bool = True) -> tuple[str, bool]:
    try:
        # 1) 데이터 수집
        data = fetch_ticker(ticker)

        # 2) OI 데이터 유효성 판정 (장 시작 전이면 전 계약 0으로 내려옴)
        oi_stale = metrics.is_oi_stale(data)

        # 3) 비교 기준선 로드: OI 비교는 마지막 '정상 OI' 스냅샷 기준
        prev = snapshot_store.load_previous_snapshot(
            ticker, data["date"], valid_oi_only=True
        )
        history = snapshot_store.load_history(ticker, data["date"])

        # 4) 개별 옵션 행 보강 (voi / OI 변화율 / 볼륨 대비 평균)
        metrics.enrich_contracts(data, prev, history, oi_stale=oi_stale)

        # 5) 기본 지표 (OI 미갱신이면 클러스터는 전일 폴백)
        base = metrics.build_base_metrics(data, prev=prev, oi_stale=oi_stale)
        data["metrics"] = base

        # 6) 이상 신호 + 거래량 이상 (OI 미갱신이면 OI 이상신호는 스킵)
        anomalies = metrics.build_anomalies(data, prev, oi_stale=oi_stale)
        vol_anom = metrics.build_volume_anomaly(data, history)
        data["anomalies"] = anomalies
        data["volume_anomaly"] = vol_anom

        # 7) 인사이트: 규칙 기반(항상) + ChatGPT 자연어 해설(가능하면)
        rule_insights = insights_mod.build_insights(data, base, anomalies, vol_anom)
        data["insights"] = rule_insights
        ai_text, ai_source = insights_mod.build_ai_narrative(
            data, base, anomalies, vol_anom
        )
        data["ai_narrative"] = ai_text
        data["ai_source"] = ai_source

        # 8) 저장
        if save:
            path = snapshot_store.save_snapshot(data)
            note = f"\n[저장됨: {path}]"
        else:
            note = "\n[미저장 모드]"

        # 9) 리포트
        report = report_builder.build_report(
            data, base, anomalies, vol_anom, rule_insights, ai_text, ai_source
        )
        return report + note, True
    except Exception as e:  # noqa: BLE001
        return f"[{ticker}] 처리 실패: {e}\n{traceback.format_exc()}", False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="옵션 분석 일일 리포트 (Phase 0)")
    parser.add_argument("--no-save", action="store_true", help="스냅샷 저장 없이 미리보기")
    parser.add_argument("--ticker", help="특정 티커 하나만 실행 (config 무시)")
    args = parser.parse_args(argv)

    tickers = [args.ticker.upper()] if args.ticker else config.TICKERS

    any_fail = False
    reports: list[str] = []
    for tk in tickers:
        report, ok = process_ticker(tk, save=not args.no_save)
        reports.append(report)
        if not ok:
            any_fail = True

    print(("\n\n" + "─" * 60 + "\n\n").join(reports))
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
