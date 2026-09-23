"""숏 관심·스퀴즈 체크 (옵션 분석과 분리).

규칙:
- 주가 급등만으로 숏스퀴즈 판정 금지
- 독립 증거 2개 이상일 때만 '가능성 있음' 이상
- Call volume ≠ short covering

데이터:
- 1차: yfinance (shortPercentOfFloat, shortRatio, sharesShort …)
- 2차(선택): ORTEX_API_KEY 가 있으면 cost-to-borrow 시도
- Borrow Fee/Availability 없으면 미연동으로 명시
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


# ---- 임계값 (단정용 아님 · 증거 플래그) ----
_HIGH_SHORT_PCT = 0.15          # float 대비 15%+
_HIGH_DAYS_TO_COVER = 4.0       # shortRatio ≈ days to cover
_SI_CHANGE_PCT = 5.0            # 전월/전회 대비 ±5%
_PRICE_SURGE_PCT = 8.0
_VOL_SURGE_MULT = 1.8


def _root() -> Path:
    import config

    here = Path(__file__).resolve().parent
    base = Path(config.SNAPSHOTS_DIR)
    base = base if base.is_absolute() else here / base
    d = base / "_short"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hist_path(ticker: str) -> Path:
    return _root() / f"{ticker.upper()}.json"


def load_short_history(ticker: str, limit: int = 30) -> list[dict]:
    p = _hist_path(ticker)
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("history") or []
        return rows[-limit:]
    except Exception:
        return []


def save_short_snapshot(ticker: str, row: dict) -> Path:
    p = _hist_path(ticker)
    hist = load_short_history(ticker, limit=200)
    date = str(row.get("date") or "")
    hist = [h for h in hist if str(h.get("date")) != date]
    hist.append(row)
    payload = {"ticker": ticker.upper(), "history": hist[-90:]}
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p


def _fetch_yfinance_short(ticker: str) -> dict[str, Any]:
    """yfinance info — 타임아웃 가능하므로 실패 시 available=False."""
    import yfinance as yf

    out: dict[str, Any] = {"source": "yfinance", "available": False}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    def _f(key: str):
        v = info.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    short_pct = _f("shortPercentOfFloat")  # 0.2463 = 24.63%
    short_ratio = _f("shortRatio")
    shares_short = _f("sharesShort")
    shares_prior = _f("sharesShortPriorMonth")
    float_shares = _f("floatShares")
    outstanding = _f("sharesOutstanding")
    date_si = info.get("dateShortInterest")
    as_of = None
    if date_si:
        try:
            as_of = dt.datetime.utcfromtimestamp(int(date_si)).date().isoformat()
        except (TypeError, ValueError, OSError):
            as_of = str(date_si)

    si_chg_pct = None
    if shares_short and shares_prior and shares_prior > 0:
        si_chg_pct = round((shares_short - shares_prior) / shares_prior * 100, 2)

    out.update(
        {
            "available": short_pct is not None or shares_short is not None,
            "short_pct_float": round(short_pct * 100, 2) if short_pct is not None else None,
            "short_ratio_days": round(short_ratio, 2) if short_ratio is not None else None,
            "shares_short": int(shares_short) if shares_short else None,
            "shares_short_prior": int(shares_prior) if shares_prior else None,
            "si_change_pct": si_chg_pct,
            "float_shares": int(float_shares) if float_shares else None,
            "shares_outstanding": int(outstanding) if outstanding else None,
            "short_interest_as_of": as_of,
        }
    )
    return out


def _fetch_ortex_borrow(ticker: str) -> dict[str, Any]:
    """선택: ORTEX_API_KEY 있을 때만. 실패/미설정 → available=False."""
    key = os.getenv("ORTEX_API_KEY") or os.getenv("ORTEX_KEY")
    out: dict[str, Any] = {
        "source": "ortex",
        "available": False,
        "borrow_fee_pct": None,
        "borrow_availability": None,
        "note": "ORTEX_API_KEY 미설정 — Borrow Fee 미연동",
    }
    if not key:
        return out
    try:
        import ortex  # type: ignore

        # 거래소 추정: NASDAQ 우선
        for exch in ("NASDAQ", "NYSE", "AMEX"):
            try:
                resp = ortex.get_cost_to_borrow(exch, ticker.upper(), api_key=key)
                # SDK 응답 형태가 버전마다 다를 수 있어 방어적으로 파싱
                fee = None
                if hasattr(resp, "data"):
                    rows = resp.data
                elif isinstance(resp, dict):
                    rows = resp.get("data") or resp
                else:
                    rows = resp
                if isinstance(rows, list) and rows:
                    row0 = rows[0] if isinstance(rows[0], dict) else {}
                    fee = row0.get("fee") or row0.get("cost") or row0.get("rate")
                elif isinstance(rows, dict):
                    fee = rows.get("fee") or rows.get("cost")
                if fee is not None:
                    out["available"] = True
                    out["borrow_fee_pct"] = float(fee)
                    out["note"] = f"ORTEX {exch}"
                    return out
            except Exception:
                continue
        out["note"] = "ORTEX 호출 실패 — Borrow Fee 미연동"
    except ImportError:
        out["note"] = "ortex 패키지 없음 — Borrow Fee 미연동"
    except Exception as e:  # noqa: BLE001
        out["note"] = f"ORTEX 오류: {type(e).__name__}"
    return out


def collect_short_metrics(
    ticker: str,
    *,
    date: str | None = None,
    price_change_pct: float | None = None,
    volume_mult: float | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """숏 메트릭 수집 + (가능하면) 7일 변화."""
    date = date or dt.date.today().isoformat()
    yf_row = _fetch_yfinance_short(ticker)
    borrow = _fetch_ortex_borrow(ticker)

    row = {
        "date": date,
        "ticker": ticker.upper(),
        **{k: v for k, v in yf_row.items() if k != "error"},
        "borrow_fee_pct": borrow.get("borrow_fee_pct"),
        "borrow_availability": borrow.get("borrow_availability"),
        "borrow_note": borrow.get("note"),
        "borrow_available": bool(borrow.get("available")),
        "price_change_pct": price_change_pct,
        "volume_mult": volume_mult,
    }
    if yf_row.get("error"):
        row["yf_error"] = yf_row["error"]

    hist = load_short_history(ticker)
    # 7거래일 전후 비교 (히스토리에 저장된 스냅샷 기준)
    prev7 = hist[-7] if len(hist) >= 7 else (hist[0] if hist else None)
    fee_chg = None
    avail_chg = None
    if prev7 and row.get("borrow_fee_pct") is not None and prev7.get("borrow_fee_pct") is not None:
        try:
            fee_chg = round(float(row["borrow_fee_pct"]) - float(prev7["borrow_fee_pct"]), 2)
        except (TypeError, ValueError):
            fee_chg = None
    row["borrow_fee_7d_change"] = fee_chg
    row["borrow_availability_7d_change"] = avail_chg

    if save and row.get("available"):
        save_short_snapshot(ticker, {
            "date": date,
            "short_pct_float": row.get("short_pct_float"),
            "short_ratio_days": row.get("short_ratio_days"),
            "shares_short": row.get("shares_short"),
            "si_change_pct": row.get("si_change_pct"),
            "borrow_fee_pct": row.get("borrow_fee_pct"),
            "price_change_pct": price_change_pct,
        })

    return evaluate_squeeze(row)


def evaluate_squeeze(row: dict[str, Any]) -> dict[str, Any]:
    """독립 증거 집계 → 판정. 급등만으로 confirmed 금지."""
    evidence: list[dict[str, str]] = []

    sp = row.get("short_pct_float")
    if sp is not None and sp >= _HIGH_SHORT_PCT * 100:
        evidence.append(
            {"id": "high_si_pct", "label": f"Short % Float 높음 ({sp}%)"}
        )

    dtc = row.get("short_ratio_days")
    if dtc is not None and dtc >= _HIGH_DAYS_TO_COVER:
        evidence.append(
            {"id": "high_dtc", "label": f"Days to Cover 높음 ({dtc})"}
        )

    si_chg = row.get("si_change_pct")
    if si_chg is not None and si_chg >= _SI_CHANGE_PCT:
        evidence.append(
            {"id": "si_rising", "label": f"Short Interest 증가 ({si_chg:+.1f}%)"}
        )
    if si_chg is not None and si_chg <= -_SI_CHANGE_PCT:
        evidence.append(
            {
                "id": "si_covering",
                "label": f"Short Interest 감소·커버 흔적 ({si_chg:+.1f}%)",
            }
        )

    px = row.get("price_change_pct")
    if px is not None and px >= _PRICE_SURGE_PCT:
        evidence.append(
            {"id": "price_surge", "label": f"주가 급등 ({px:+.1f}%)"}
        )

    vm = row.get("volume_mult")
    if vm is not None and vm >= _VOL_SURGE_MULT:
        evidence.append(
            {"id": "vol_surge", "label": f"거래량 급증 ({vm:.1f}배)"}
        )

    fee = row.get("borrow_fee_pct")
    fee_chg = row.get("borrow_fee_7d_change")
    if fee is not None and fee >= 20:
        evidence.append(
            {"id": "high_borrow", "label": f"Borrow Fee 높음 ({fee}%)"}
        )
    if fee_chg is not None and fee_chg >= 5:
        evidence.append(
            {"id": "borrow_spike", "label": f"Borrow Fee 7D 상승 (+{fee_chg}%p)"}
        )

    n = len(evidence)
    ids = {e["id"] for e in evidence}

    # 판정 규칙: 급등만(price_surge alone)으로는 안 됨
    strong_si = bool(ids & {"high_si_pct", "high_dtc", "si_rising", "si_covering"})
    borrow_ev = bool(ids & {"high_borrow", "borrow_spike"})
    flow_ev = bool(ids & {"price_surge", "vol_surge"})

    if not row.get("available") and not row.get("borrow_available"):
        verdict = "판단 보류"
        verdict_code = "deferred"
    elif n >= 3 and strong_si and (flow_ev or borrow_ev):
        verdict = "가능성 높음"
        verdict_code = "likely"
    elif n >= 2 and strong_si and flow_ev:
        verdict = "가능성 있음"
        verdict_code = "possible"
    elif n >= 2 and strong_si:
        verdict = "가능성 있음"
        verdict_code = "possible"
    elif n == 1 and "price_surge" in ids:
        verdict = "근거 부족"
        verdict_code = "insufficient"
        evidence.append(
            {
                "id": "rule",
                "label": "주가 급등만으로는 숏스퀴즈로 판정하지 않음",
            }
        )
    elif n >= 1:
        verdict = "근거 부족"
        verdict_code = "insufficient"
    else:
        verdict = "근거 부족"
        verdict_code = "insufficient"

    # '확인됨'은 SI 감소(커버)+급등+높은 SI/차입 부담이 같이 있을 때만
    if (
        "si_covering" in ids
        and flow_ev
        and (strong_si or borrow_ev)
        and n >= 3
    ):
        verdict = "확인됨(커버 동반)"
        verdict_code = "confirmed"

    out = dict(row)
    out["evidence"] = evidence
    out["evidence_count"] = n
    out["verdict"] = verdict
    out["verdict_code"] = verdict_code
    out["rule"] = (
        "독립 증거 2개 이상 필요 · 주가 급등만으로 확정 금지 · "
        "옵션 거래량과 숏커버를 동일시하지 않음"
    )
    return out


def format_squeeze_section(sq: dict | None) -> str:
    """리포트용 Short Squeeze 블록."""
    L = ["🔥 Short Squeeze Check"]
    if not sq:
        L.append("· 데이터 없음 → 판단 보류")
        return "\n".join(L)

    def _v(key: str, fmt: str = "{}") -> str:
        v = sq.get(key)
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except Exception:
            return str(v)

    L.append(f"· Short % Float     : {_v('short_pct_float', '{}%')}")
    L.append(f"· Days to Cover     : {_v('short_ratio_days')}")
    L.append(f"· Shares Short      : {_v('shares_short', '{:,}')}")
    L.append(f"· SI 변화(전월비)   : {_v('si_change_pct', '{:+.1f}%')}")
    L.append(f"· SI as-of          : {_v('short_interest_as_of')}")
    L.append(
        f"· Borrow Fee        : {_v('borrow_fee_pct', '{}%')} "
        f"({sq.get('borrow_note') or '미연동'})"
    )
    L.append(f"· Borrow Fee 7D     : {_v('borrow_fee_7d_change', '{:+.1f}%p')}")
    L.append(f"· 주가 변화         : {_v('price_change_pct', '{:+.1f}%')}")
    L.append(f"· 거래량 배수       : {_v('volume_mult', '{:.1f}x')}")
    L.append("")
    L.append(f"· 판정: {sq.get('verdict')} (증거 {sq.get('evidence_count', 0)}개)")
    for e in sq.get("evidence") or []:
        L.append(f"  - {e.get('label')}")
    L.append(f"· 규칙: {sq.get('rule')}")
    # 체크리스트
    code = sq.get("verdict_code")
    boxes = [
        ("confirmed", "확인됨"),
        ("likely", "가능성 높음"),
        ("possible", "가능성 있음"),
        ("insufficient", "근거 부족"),
        ("deferred", "판단 보류"),
    ]
    marks = []
    for c, lab in boxes:
        marks.append(f"{'☑' if code == c else '☐'} {lab}")
    L.append("· " + " · ".join(marks))
    return "\n".join(L)
