"""프로젝트 설정.

설정값은 settings.json 에서 읽어온다(종목/임계값을 코드와 분리).
파일이 없으면 아래 기본값을 사용한다.

종목 추가: settings.json 의 "tickers" 배열에 티커만 넣으면 된다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
_SETTINGS_FILE = ROOT_DIR / "settings.json"

# .env 로드 (있으면). OPENAI_API_KEY 등을 환경변수로 읽어온다.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except Exception:  # dotenv 미설치여도 동작하도록
    pass

# 옵션 체인에서 추출할 필드
OPTION_FIELDS = [
    "strike",
    "lastPrice",
    "change",
    "percentChange",
    "bid",
    "ask",
    "volume",
    "openInterest",
]

# 스냅샷 저장 폴더
SNAPSHOTS_DIR = "snapshots"

# ---- 기본값 (settings.json 이 없을 때) ----
_DEFAULTS = {
    "tickers": ["IREN"],
    "expiries": ["this_week", "next_week", "monthly"],
    "anomaly_thresholds": {
        "oi_surge_pct": 100,
        "oi_drop_pct": -80,
        "min_volume": 500,
        "volume_anomaly_mult": 3.0,
    },
    "voi_top_n": 5,
    "voi_min_oi": 50,
    "top_volume_n": 5,
    "oi_cluster_top_n": 3,
    "oi_alert_min_oi": 100,
    "far_otm_call_mult": 1.4,
    "far_otm_min_oi": 5000,
    "oi_stale_zero_fraction": 0.9,
    "llm": {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "max_output_tokens": 1600,
        "temperature": 0.5,
    },
    "email": {
        "enabled": True,
        "attach_json": True,
        "subject_prefix": "[옵션리포트]",
    },
    "events": {
        "enabled": True,
        "earnings_window_days": 3,
        "news_count": 5,
        "price_move_alert_pct": 8.0,
    },
}


def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        with _SETTINGS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = {**_DEFAULTS, **data}
        merged["anomaly_thresholds"] = {
            **_DEFAULTS["anomaly_thresholds"],
            **data.get("anomaly_thresholds", {}),
        }
        merged["llm"] = {**_DEFAULTS["llm"], **data.get("llm", {})}
        merged["email"] = {**_DEFAULTS["email"], **data.get("email", {})}
        merged["events"] = {**_DEFAULTS["events"], **data.get("events", {})}
        return merged
    return _DEFAULTS


_S = _load_settings()

# ---- 모듈 레벨로 노출 ----
TICKERS: list[str] = [t.strip().upper() for t in _S["tickers"] if t.strip()]
EXPIRY_ROLES: list[str] = _S["expiries"]

_TH = _S["anomaly_thresholds"]
OI_SURGE_PCT: float = _TH["oi_surge_pct"]          # +100% 이상 → 신규 유입
OI_DROP_PCT: float = _TH["oi_drop_pct"]            # -80% 이하 → 대량 청산
VOI_MIN_VOLUME: int = _TH["min_volume"]            # top_voi 노이즈 제거용 최소 볼륨
VOLUME_ANOMALY_MULT: float = _TH["volume_anomaly_mult"]

# 비율(%) → 소수 변환
OI_SURGE_UP: float = OI_SURGE_PCT / 100.0
OI_SURGE_DOWN: float = OI_DROP_PCT / 100.0

VOI_TOP_N: int = _S["voi_top_n"]
VOI_MIN_OI: int = _S["voi_min_oi"]
TOP_VOLUME_N: int = _S["top_volume_n"]
OI_CLUSTER_TOP_N: int = _S["oi_cluster_top_n"]
OI_ALERT_MIN_OI: int = _S["oi_alert_min_oi"]
FAR_OTM_CALL_MULT: float = _S["far_otm_call_mult"]
FAR_OTM_MIN_OI: int = _S["far_otm_min_oi"]
OI_STALE_ZERO_FRACTION: float = _S["oi_stale_zero_fraction"]

# ---- LLM(ChatGPT) 설정 ----
_LLM = _S["llm"]
LLM_ENABLED: bool = bool(_LLM.get("enabled", True))
LLM_PROVIDER: str = _LLM.get("provider", "openai")
# .env 의 OPENAI_MODEL 이 있으면 우선 적용
LLM_MODEL: str = os.getenv("OPENAI_MODEL") or _LLM.get("model", "gpt-4o-mini")
LLM_MAX_TOKENS: int = int(_LLM.get("max_output_tokens", 600))
LLM_TEMPERATURE: float = float(_LLM.get("temperature", 0.3))
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")

# ---- 이메일(Gmail SMTP) 설정 ----
_EMAIL = _S["email"]
EMAIL_ENABLED: bool = bool(_EMAIL.get("enabled", True))
EMAIL_ATTACH_JSON: bool = bool(_EMAIL.get("attach_json", True))
EMAIL_SUBJECT_PREFIX: str = _EMAIL.get("subject_prefix", "[옵션리포트]")
EMAIL_WEEKLY_SUBJECT_PREFIX: str = _EMAIL.get(
    "weekly_subject_prefix", "[옵션주간검증]"
)
# 인증정보는 환경변수(.env / Actions Secrets)에서만 읽는다
EMAIL_SENDER: str | None = os.getenv("EMAIL_SENDER")
EMAIL_APP_PASSWORD: str | None = os.getenv("EMAIL_APP_PASSWORD")
_recipients_raw = os.getenv("EMAIL_RECIPIENTS", "")
EMAIL_RECIPIENTS: list[str] = [
    e.strip() for e in _recipients_raw.split(",") if e.strip()
]

# ---- 이벤트/뉴스 설정 ----
_EV = _S["events"]
EVENTS_ENABLED: bool = bool(_EV.get("enabled", True))
EARNINGS_WINDOW_DAYS: int = int(_EV.get("earnings_window_days", 3))
NEWS_COUNT: int = int(_EV.get("news_count", 5))
PRICE_MOVE_ALERT_PCT: float = float(_EV.get("price_move_alert_pct", 8.0))
