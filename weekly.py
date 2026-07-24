"""주간 검증(백테스트) 리포트 — 매주 금요일 미장 마감 후(토 아침 KST).

흐름:
  그 주 월~금 일일 스냅샷 로드 → '그 주 첫(월요일) 예측' 추출
  → 실제 주간 OHLC 수집 → 항목별 정확도/종합 성적 채점
  → 최근 4주 추이 → ChatGPT 자연어 해석(폴백 규칙기반)
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


def _grade_emoji(grade: str) -> str:
    head = grade[0]
    return {"A": "🏆", "B": "👍", "C": "🙂", "D": "😐", "F": "😢"}.get(head, "📋")


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
# 자연어 해석 (LLM → 규칙기반 폴백)
# ------------------------------------------------------------------ #

def _payload(ticker, week_ending, prediction, ohlc, results, trend, partial) -> dict:
    return {
        "티커": ticker,
        "주간": week_ending,
        "부분데이터": partial,
        "예측기준일": prediction.get("from_date"),
        "예측": {
            "예상밴드": [prediction["band_lower"], prediction["band_upper"]],
            "예상저항": prediction["resistance"],
            "예상지지": prediction["support"],
            "예상심리": prediction["sentiment"],
        },
        "실제OHLC": ohlc,
        "채점": results,
        "최근추이": trend,
    }


def build_fallback(ticker, week_ending, prediction, ohlc, results, trend, partial) -> str:
    g = results["grade"]
    L: list[str] = []
    L.append(f"🧾 이번 주 {ticker} 옵션 예측 성적표 - {week_ending}")
    L.append("")
    L.append(f"{_grade_emoji(g['grade'])} 종합 성적: {g['grade']} ({g['score']}점)")
    if partial:
        L.append("※ 이번 주 데이터가 일부만 있어(주중/부분) 참고용 채점이에요.")
    L.append("")

    L.append(
        f"💰 실제 주가: 시가 ${ohlc['open']} → 종가 ${ohlc['close']} "
        f"(주간 {ohlc['return_pct']:+.1f}%), 고가 ${ohlc['high']} / 저가 ${ohlc['low']}"
    )
    L.append("")

    band = results.get("band")
    if band:
        L.append("📐 예상 범위 vs 실제")
        L.append(
            f"예상 밴드 ${band['predicted'][0]}~${band['predicted'][1]} vs "
            f"실제 ${band['actual'][0]}~${band['actual'][1]} → {band['label']} "
            f"({band['score']}점)"
        )
        L.append("")

    res = results.get("resistance")
    sup = results.get("support")
    if res or sup:
        L.append("🔴 저항 / 🟢 지지 채점")
        if res:
            L.append(f"- 저항: {res['label']} ({res['score']}점)")
        if sup:
            L.append(f"- 지지: {sup['label']} ({sup['score']}점)")
        L.append("")

    d = results["direction"]
    L.append("🧭 방향 예측 채점")
    L.append(f"{d['label']} ({d['score']}점)")
    L.append("")

    L.append("💡 이번 주 배운 것")
    parts = {
        "밴드(범위)": band,
        "방향": d,
        "저항선": res,
        "지지선": sup,
    }
    scored = [(k, v["score"]) for k, v in parts.items() if v]
    if scored:
        best = max(scored, key=lambda x: x[1])
        worst = min(scored, key=lambda x: x[1])
        L.append(f"- 이번 주 가장 잘 맞은 지표: {best[0]} ({best[1]}점)")
        L.append(f"- 가장 빗나간 지표: {worst[0]} ({worst[1]}점) → 다음 주 해석 시 주의")
    L.append("")

    if trend:
        L.append("📅 최근 추이")
        for r in trend:
            L.append(f"- {r['주간']}: {r['등급']} ({r['종합점수']}점)")
        L.append("")

    L.append("⚠️ 이 리포트는 투자 조언이 아니라 예측 검증 기록입니다.")
    return "\n".join(L)


def build_narrative(ticker, week_ending, prediction, ohlc, results, trend, partial):
    """(본문, 출처)."""
    import llm

    payload = _payload(ticker, week_ending, prediction, ohlc, results, trend, partial)
    text = llm.generate_weekly(payload)
    if text:
        return text, "openai"
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

        prediction = wm.extract_prediction(snaps[0])  # 그 주 첫(가장 이른) 스냅샷
        if prediction["band_lower"] is None:
            return (
                f"[{ticker}] {prediction['from_date']} 예측(밴드)이 비어 검증 불가.",
                False,
                None,
            )

        ohlc = wm.weekly_ohlc(ticker, monday, friday)
        if not ohlc:
            return (f"[{ticker}] 실제 주간 OHLC 를 가져오지 못함.", False, None)

        results = wm.build_weekly(ticker, prediction, ohlc)
        trend = build_history_trend(ticker, week_ending)

        # 그 주 첫 스냅샷이 월요일이 아니거나, 거래일이 5일 미만이면 부분 데이터
        partial = (prediction["from_date"] != monday.isoformat()) or ohlc["days"] < 5

        narrative, source = build_narrative(
            ticker, week_ending, prediction, ohlc, results, trend, partial
        )

        report = narrative + "\n" + build_appendix(prediction, ohlc, results)

        snapshot = {
            "ticker": ticker,
            "week_ending": week_ending,
            "week_start": monday.isoformat(),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "partial": partial,
            "prediction": prediction,
            "actual_ohlc": ohlc,
            "results": results,
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
        f"주간검증 - {week_ending}{status}"
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
        f"주간검증 - {week_ending}{status}"
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
