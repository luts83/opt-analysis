"""주간 방향 리포트 — 매주 금요일 미장 마감 후(토 아침 KST).

흐름:
  그 주 월~금 스냅샷 → 가격 레짐·낙폭·SPY 동조
  → 과거 동조건 전진 빈도(표본 명시) → 무효화 조건
  → 강한 옵션 라벨 요약 → (부록) 밴드 숫자 채점
  → 주간 스냅샷 저장 → 텔레그램(우선) / 이메일(선택) 발송.

실행:
  python weekly.py                 # 이번 주(오늘 기준) 검증
  python weekly.py --date 2026-07-24   # 해당 날짜가 속한 주 검증
  python weekly.py --no-email --no-telegram --no-save
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from pathlib import Path

import config
import emailer
import snapshot_store
import telegram_notify
import weekly_metrics as wm


# ------------------------------------------------------------------ #
# 주간 경계 / 데이터 수집
# ------------------------------------------------------------------ #

def week_bounds(ref: dt.date) -> tuple[dt.date, dt.date]:
    """ref 가 속한 주의 (월요일, 금요일)."""
    monday = ref - dt.timedelta(days=ref.weekday())
    return monday, monday + dt.timedelta(days=4)


# ------------------------------------------------------------------ #
# 최근 4주 추이
# ------------------------------------------------------------------ #

def build_history_trend(ticker: str, week_ending: str) -> list[dict]:
    hist = snapshot_store.load_weekly_history(ticker, week_ending, limit=4)
    rows = []
    for h in sorted(hist, key=lambda s: s.get("week_ending", "")):
        r = h.get("results", {})
        rows.append(
            {
                "주간": h.get("week_ending"),
                "종합점수": (r.get("grade") or {}).get("score"),
                "등급": (r.get("grade") or {}).get("grade"),
                "밴드점수": (r.get("band") or {}).get("score"),
                "방향점수": (r.get("direction") or {}).get("score"),
                "저항점수": (r.get("resistance") or {}).get("score"),
                "지지점수": (r.get("support") or {}).get("score"),
            }
        )
    return rows


# ------------------------------------------------------------------ #
# 숫자 요약 본문
# ------------------------------------------------------------------ #

def build_fallback(ticker, week_ending, prediction, ohlc, results, trend, partial) -> str:
    """숫자 요약만 — 비유·과장 등급 해설 없음."""
    g = results["grade"]
    L: list[str] = []
    L.append(f"🧾 {ticker} 주간 검증 {week_ending}")
    if partial:
        L.append("※ 부분 데이터")
    L.append(
        f"· 실제: O {ohlc['open']} / H {ohlc['high']} / L {ohlc['low']} / "
        f"C {ohlc['close']} ({ohlc['return_pct']:+.1f}%, {ohlc['days']}일)"
    )
    band = results.get("band")
    if band:
        L.append(
            f"· 밴드: 예상 {band['predicted'][0]}~{band['predicted'][1]} "
            f"vs 실제 {band['actual'][0]}~{band['actual'][1]} "
            f"→ {band.get('score')}점"
        )
    res = results.get("resistance")
    sup = results.get("support")
    if res:
        L.append(
            f"· 저항 예상 {prediction.get('resistance')} / 고가 {ohlc['high']} "
            f"→ {res.get('score')}점"
        )
    if sup:
        L.append(
            f"· 지지 예상 {prediction.get('support')} / 저가 {ohlc['low']} "
            f"→ {sup.get('score')}점"
        )
    d = results.get("direction") or {}
    if d:
        L.append(f"· 방향: {d.get('label')} ({d.get('score')}점)")
    L.append(f"· 종합(참고): {g.get('grade')} {g.get('score')}점 — 밴드·S/R 해석 과신 금지")
    if trend:
        recent = ", ".join(f"{r['주간']} {r['등급']}" for r in trend[-4:])
        L.append(f"· 최근: {recent}")
    L.append("⚠️ 숫자 검증 기록 · 투자 조언 아님")
    return "\n".join(L)


def build_narrative(ticker, week_ending, prediction, ohlc, results, trend, partial):
    """숫자표만 (LLM 비유·성적 연극 중지)."""
    return (
        build_fallback(ticker, week_ending, prediction, ohlc, results, trend, partial),
        "rule",
    )


# ------------------------------------------------------------------ #
# 데이터 요약 부록
# ------------------------------------------------------------------ #

def build_appendix(prediction, ohlc, results) -> str:
    g = results["grade"]
    L = ["", "─" * 40, "📎 데이터 요약 (채점 근거)", ""]
    L.append(f"- 예측 기준일: {prediction.get('from_date')} (만기 {prediction.get('expiry')})")
    L.append(
        f"- 예상 밴드: {prediction['band_lower']} ~ {prediction['band_upper']} "
        f"(±{prediction.get('band_pct')}%)"
    )
    L.append(f"- 예상 저항/지지: {prediction['resistance']} / {prediction['support']}")
    L.append(f"- 예상 심리: {prediction['sentiment']}")
    L.append(
        f"- 실제 OHLC: O {ohlc['open']} / H {ohlc['high']} / L {ohlc['low']} / "
        f"C {ohlc['close']} ({ohlc['days']}거래일)"
    )
    L.append("- 항목별 점수: " + ", ".join(
        f"{k} {v['score']}"
        for k, v in (
            ("밴드", results.get("band")),
            ("방향", results.get("direction")),
            ("저항", results.get("resistance")),
            ("지지", results.get("support")),
        )
        if v
    ))
    L.append(f"- 종합: {g['grade']} ({g['score']}점)")
    return "\n".join(L)


# ------------------------------------------------------------------ #
# 한 종목 처리
# ------------------------------------------------------------------ #

def process_ticker(
    ticker: str, ref_date: dt.date, save: bool = True
) -> tuple[str, bool, dict | None]:
    try:
        monday, friday = week_bounds(ref_date)
        week_ending = friday.isoformat()

        snaps = snapshot_store.list_snapshots_between(
            ticker, monday.isoformat(), friday.isoformat()
        )
        if not snaps:
            return (
                f"[{ticker}] {monday}~{friday} 구간 일일 스냅샷이 없어 검증 불가.",
                False,
                None,
            )

        import regime as regime_mod

        spy_reg = regime_mod.load_spy_regime(friday.isoformat())
        if not spy_reg or not spy_reg.get("available"):
            try:
                spy_reg = regime_mod.fetch_and_save_spy(
                    friday.isoformat(), save=save
                )
            except Exception:  # noqa: BLE001
                spy_reg = None

        partial = len(snaps) < 4
        narrative, dir_meta = regime_mod.format_weekly_direction(
            ticker,
            week_ending,
            monday.isoformat(),
            friday.isoformat(),
            spy_regime=spy_reg if isinstance(spy_reg, dict) else None,
            partial=partial,
        )
        source = "direction"

        # 밴드 성적표는 부록으로만 (없어도 방향 리포트는 발송)
        prediction = wm.extract_prediction(snaps[0])
        ohlc = wm.weekly_ohlc(ticker, monday, friday)
        results = None
        appendix = ""
        if prediction.get("band_lower") is not None and ohlc:
            results = wm.build_weekly(ticker, prediction, ohlc)
            appendix = "\n" + build_appendix(prediction, ohlc, results)
            partial = partial or (
                prediction["from_date"] != monday.isoformat()
            ) or ohlc["days"] < 5

        report = narrative + appendix

        snapshot = {
            "ticker": ticker,
            "week_ending": week_ending,
            "week_start": monday.isoformat(),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "partial": partial,
            "prediction": prediction,
            "actual_ohlc": ohlc,
            "results": results,
            "direction": dir_meta,
            "narrative": narrative,
            "narrative_source": source,
        }
        if save:
            path = snapshot_store.save_weekly(snapshot)
            report += f"\n[저장됨: {path}]"

        return report, True, snapshot
    except Exception as e:  # noqa: BLE001
        return (
            f"[{ticker}] 주간 검증 실패: {e}\n{traceback.format_exc()}",
            False,
            None,
        )


# ------------------------------------------------------------------ #
# 알림
# ------------------------------------------------------------------ #

def _send_email(reports: list[str], any_fail: bool, tickers: list[str],
                week_ending: str) -> None:
    if not emailer.is_configured():
        print("[email] 이메일 미설정 → 건너뜀")
        return
    status = " ⚠일부 실패" if any_fail else ""
    subject = (
        f"{config.EMAIL_WEEKLY_SUBJECT_PREFIX} {', '.join(tickers)} "
        f"주간방향 - {week_ending}{status}"
    )
    body = ("\n\n" + "=" * 60 + "\n\n").join(reports)
    try:
        emailer.send_email(subject, body, [])
        print(f"[email] 주간 리포트 발송 완료 → {', '.join(config.EMAIL_RECIPIENTS)}")
    except emailer.EmailError as e:
        print(f"[email] 주간 리포트 발송 실패(무시): {e}")


def _send_telegram(reports: list[str], any_fail: bool, tickers: list[str],
                   week_ending: str) -> None:
    if not telegram_notify.is_configured():
        print("[telegram] 미설정 → 건너뜀")
        return
    status = " ⚠일부 실패" if any_fail else ""
    title = (
        f"{config.EMAIL_WEEKLY_SUBJECT_PREFIX} {', '.join(tickers)} "
        f"주간방향 - {week_ending}{status}"
    )
    try:
        n = telegram_notify.send_reports(title, reports)
        print(f"[telegram] 주간 리포트 발송 완료 ({n}통)")
    except telegram_notify.TelegramError as e:
        print(f"[telegram] 주간 리포트 발송 실패: {e}")
        raise


# ------------------------------------------------------------------ #
# 엔트리포인트
# ------------------------------------------------------------------ #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="옵션 주간 검증 리포트")
    parser.add_argument("--date", help="검증할 주에 속한 날짜 (YYYY-MM-DD). 기본: 오늘")
    parser.add_argument("--no-save", action="store_true", help="주간 스냅샷 저장 안 함")
    parser.add_argument("--no-email", action="store_true", help="이메일 발송 안 함")
    parser.add_argument("--no-telegram", action="store_true", help="텔레그램 발송 안 함")
    parser.add_argument("--ticker", help="특정 티커 하나만 실행")
    args = parser.parse_args(argv)

    ref = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    _, friday = week_bounds(ref)
    tickers = [args.ticker.upper()] if args.ticker else config.TICKERS

    any_fail = False
    reports: list[str] = []
    for tk in tickers:
        report, ok, _snap = process_ticker(tk, ref, save=not args.no_save)
        reports.append(report)
        if not ok:
            any_fail = True

    print(("\n\n" + "─" * 60 + "\n\n").join(reports))

    if not args.no_telegram:
        _send_telegram(reports, any_fail, tickers, friday.isoformat())
    if not args.no_email:
        _send_email(reports, any_fail, tickers, friday.isoformat())

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
