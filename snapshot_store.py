"""스냅샷 JSON 저장/로드.

저장 경로: snapshots/{TICKER}/{YYYY-MM-DD}.json
- 오늘 수집한 원본 데이터 + 계산된 지표를 함께 저장한다.
- 어제(직전) 스냅샷을 불러와 비교 계산에 사용한다. 없으면 None.
"""
from __future__ import annotations

import json
from pathlib import Path

import config

ROOT_DIR = Path(__file__).resolve().parent


def _base_dir() -> Path:
    p = Path(config.SNAPSHOTS_DIR)
    return p if p.is_absolute() else ROOT_DIR / p


def _ticker_dir(ticker: str) -> Path:
    return _base_dir() / ticker.upper()


def save_snapshot(snapshot: dict) -> Path:
    """오늘 스냅샷을 저장하고 경로를 반환한다."""
    d = _ticker_dir(snapshot["ticker"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{snapshot['date']}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_snapshot(ticker: str, date: str) -> dict | None:
    return _load(_ticker_dir(ticker) / f"{date}.json")


def _is_oi_stale(snap: dict | None) -> bool:
    if not snap:
        return False
    return bool(snap.get("metrics", {}).get("oi_data_stale"))


def load_previous_snapshot(
    ticker: str, before_date: str, valid_oi_only: bool = False
) -> dict | None:
    """before_date 이전의 가장 최근 스냅샷. (주말/휴일이면 그 전 영업일)

    valid_oi_only=True 면 OI 가 미갱신(stale)인 스냅샷은 건너뛴다.
    → OI 비교 기준선으로는 마지막 '정상 OI' 스냅샷을 쓰기 위함.
    """
    d = _ticker_dir(ticker)
    if not d.exists():
        return None
    candidates = sorted(
        (p for p in d.glob("*.json") if p.stem < before_date), reverse=True
    )
    for p in candidates:
        snap = _load(p)
        if snap is None:
            continue
        if valid_oi_only and _is_oi_stale(snap):
            continue
        return snap
    return None


def load_history(ticker: str, before_date: str, limit: int = 20) -> list[dict]:
    """before_date 이전의 스냅샷들을 최신순으로 최대 limit 개 반환 (거래량 평균용)."""
    d = _ticker_dir(ticker)
    if not d.exists():
        return []
    candidates = sorted(
        (p for p in d.glob("*.json") if p.stem < before_date), reverse=True
    )[:limit]
    out = []
    for p in candidates:
        snap = _load(p)
        if snap:
            out.append(snap)
    return out
