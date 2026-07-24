"""엔트리포인트.

config.TICKERS 순회 → 각 종목:
  데이터 수집(만기선택 포함) → 어제/이력 로드 → 개별행 보강 → 지표 계산
  → 이상신호(anomalies) → 인사이트(규칙+ChatGPT) → 스냅샷 저장 → 리포트.

마지막에 텔레그램(우선) / 이메일(선택)로 발송한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from pathlib import Path

import config
import emailer
import events as events_mod
import insights as insights_mod
import metrics
import report_builder
import snapshot_store
import telegram_notify
from data_fetch import fetch_ticker


def process_ticker(ticker: str, save: bool = True) -> tuple[str, bool, Path | None]:
    """(리포트문자열, 성공여부, 스냅샷경로) 반환."""
    try:
        data = fetch_ticker(ticker)

        # OI 신선도 판정 + 전일 OI 보간(carry-forward)
        oi_stale_raw = metrics.is_oi_stale(data)
        prev_oi = snapshot_store.load_previous_snapshot(
            ticker, data["date"], valid_oi_only=True
        )
        prev_any = snapshot_store.load_previous_snapshot(
            ticker, data["date"], valid_oi_only=False
        )
        history = snapshot_store.load_history(ticker, data["date"])
        carried = metrics.apply_oi_fallback(data, prev_oi, oi_stale_raw)

        oi_real = not oi_stale_raw           # 오늘 실제 당일 OI 인가
        oi_available = (not oi_stale_raw) or carried  # 표시할 OI 가 있는가
        oi_source = (
            "실시간" if not oi_stale_raw
            else ("전일 기준(오늘 미갱신)" if carried else "데이터 없음")
        )
        data["oi_stale_raw"] = oi_stale_raw
        data["oi_carried_forward"] = carried

        metrics.enrich_contracts(data, prev_oi, history, oi_real=oi_real)
        base = metrics.build_base_metrics(data, prev=prev_oi, oi_available=oi_available)
        base["oi_source"] = oi_source
        base["levels"] = metrics.build_levels(base, data["spot"])
        base["band_trend"] = metrics.build_band_trend(base)
        data["metrics"] = base

        # OI 급변은 당일 OI 가 실측일 때만. 어제 대비(주가·거래량·심리)는 항상.
        anomalies = metrics.build_anomalies(data, prev_oi, oi_real=oi_real)
        vol_anom = metrics.build_volume_anomaly(data, history)
        dod = metrics.build_day_over_day(data, base, prev_any)
        trend = metrics.build_trend(history, data)
        data["anomalies"] = anomalies
        data["volume_anomaly"] = vol_anom
        data["day_over_day"] = dod

        # 이벤트/뉴스(어닝·헤드라인·가격·옵션 반응·다음장 시나리오) 수집
        eventinfo = events_mod.collect_events(
            ticker,
            data["spot"],
            data.get("previous_close"),
            base=base,
            prev=prev_any,
            data=data,
        )
        data["events"] = eventinfo

        narrative, narrative_source = insights_mod.build_narrative(
            data, base, anomalies, vol_anom, prev_any, trend, eventinfo, dod
        )
        data["narrative"] = narrative
        data["narrative_source"] = narrative_source

        path = None
        if save:
            path = snapshot_store.save_snapshot(data)

        report = report_builder.build_report(
            data, base, anomalies, vol_anom, narrative, narrative_source, eventinfo
        )
        if path:
            report += f"\n[저장됨: {path}]"
        return report, True, path
    except Exception as e:  # noqa: BLE001
        return (
            f"[{ticker}] 처리 실패: {e}\n{traceback.format_exc()}",
            False,
            None,
        )


def _send_email(reports: list[str], attachments: list[Path], any_fail: bool,
                tickers: list[str]) -> None:
    """조립한 리포트를 이메일로 발송. 설정 없으면 건너뜀(실패해도 전체 실패로 안 봄)."""
    if not emailer.is_configured():
        print("[email] 이메일 미설정 → 건너뜀")
        return

    date = dt.date.today().isoformat()
    status = " ⚠일부 실패" if any_fail else ""
    subject = f"{config.EMAIL_SUBJECT_PREFIX} {', '.join(tickers)} - {date}{status}"
    body = ("\n\n" + "=" * 60 + "\n\n").join(reports)

    attach = attachments if config.EMAIL_ATTACH_JSON else []
    try:
        emailer.send_email(subject, body, attach)
        print(f"[email] 발송 완료 → {', '.join(config.EMAIL_RECIPIENTS)}")
    except emailer.EmailError as e:
        print(f"[email] 발송 실패(무시): {e}")


def _send_telegram(reports: list[str], any_fail: bool, tickers: list[str]) -> None:
    if not telegram_notify.is_configured():
        print("[telegram] 미설정(TELEGRAM_BOT_TOKEN/CHAT_ID) → 건너뜀")
        return

    date = dt.date.today().isoformat()
    status = " ⚠일부 실패" if any_fail else ""
    title = f"{config.EMAIL_SUBJECT_PREFIX} {', '.join(tickers)} - {date}{status}"
    try:
        n = telegram_notify.send_reports(title, reports)
        print(f"[telegram] 발송 완료 ({n}통) → chat {config.TELEGRAM_CHAT_ID}")
    except telegram_notify.TelegramError as e:
        print(f"[telegram] 발송 실패: {e}")
        raise


def _send_failure_notify(
    error_text: str, *, do_telegram: bool = True, do_email: bool = True
) -> None:
    """전체 실행이 터졌을 때 실패 알림."""
    date = dt.date.today().isoformat()
    msg = f"{config.EMAIL_SUBJECT_PREFIX} 리포트 생성 실패 - {date}\n\n{error_text}"
    if do_telegram and telegram_notify.is_configured():
        try:
            telegram_notify.send_text(msg[:3800])
            print("[telegram] 실패 알림 발송")
        except telegram_notify.TelegramError as e:
            print(f"[telegram] 실패 알림도 실패: {e}")
    if do_email and emailer.is_configured():
        subject = f"{config.EMAIL_SUBJECT_PREFIX} 리포트 생성 실패 - {date}"
        try:
            emailer.send_email(subject, "오늘 리포트 생성에 실패했습니다.\n\n" + error_text, [])
            print("[email] 실패 알림 메일 발송")
        except emailer.EmailError as e:
            print(f"[email] 실패 알림 메일도 실패: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="옵션 분석 일일 리포트")
    parser.add_argument("--no-save", action="store_true", help="스냅샷 저장 없이 미리보기")
    parser.add_argument("--no-email", action="store_true", help="이메일 발송 안 함")
    parser.add_argument("--no-telegram", action="store_true", help="텔레그램 발송 안 함")
    parser.add_argument("--ticker", help="특정 티커 하나만 실행 (config 무시)")
    args = parser.parse_args(argv)

    tickers = [args.ticker.upper()] if args.ticker else config.TICKERS

    try:
        any_fail = False
        reports: list[str] = []
        attachments: list[Path] = []
        for tk in tickers:
            report, ok, path = process_ticker(tk, save=not args.no_save)
            reports.append(report)
            if path:
                attachments.append(path)
            if not ok:
                any_fail = True

        print(("\n\n" + "─" * 60 + "\n\n").join(reports))

        if not args.no_telegram:
            _send_telegram(reports, any_fail, tickers)
        if not args.no_email:
            _send_email(reports, attachments, any_fail, tickers)

        return 1 if any_fail else 0
    except Exception as e:  # noqa: BLE001 — 예상 못한 전체 실패
        err = f"{e}\n{traceback.format_exc()}"
        print(err)
        if not args.no_telegram or not args.no_email:
            _send_failure_notify(
                err,
                do_telegram=not args.no_telegram,
                do_email=not args.no_email,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
