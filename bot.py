"""텔레그램 봇 — 수동 /report + 아침 자동 리포트 (단일 프로세스).

명령:
  /start, /help     — 사용법
  /report           — 전체 종목
  /report IREN      — 특정 종목

Railway: 서비스 하나만.
  Start Command: python bot.py
  Cron Schedule: 비움 (안에서 스케줄)
  Variables + Volume(/data) 설정

로컬: python bot.py
"""
from __future__ import annotations

import re
import sys
import threading
import time
import traceback

import config
import main as daily_main
import telegram_notify as tg

# 영국 여름 화~토 06:07 = UTC 05:07 (railway.toml 과 동일 의도)
_UTC_HOUR = 5
_UTC_MINUTE = 7
# Mon=0 … Sun=6 → Tue–Sat = 1..5
_UTC_WEEKDAYS = {1, 2, 3, 4, 5}

_HELP = """옵션 리포트 봇

명령:
/report — 전체 종목 리포트 (조금 걸림)
/report IREN — 특정 종목만
/help — 이 안내

자동 발송: 화~토 아침(영국 여름 06:07)에 이 봇이 보냅니다."""

_lock = threading.Lock()
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def _parse_command(text: str) -> tuple[str | None, list[str]]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def _run_report(ticker: str | None, *, reason: str) -> None:
    argv = ["--no-email"]
    if ticker:
        argv += ["--ticker", ticker.upper()]
    label = ticker.upper() if ticker else ", ".join(config.TICKERS)
    tg.send_text(f"⏳ 리포트 생성 중… ({label})\n사유: {reason}")
    try:
        code = daily_main.main(argv)
        if code != 0:
            tg.send_text("⚠️ 리포트 생성은 끝났지만 일부 종목에서 오류가 있었습니다.")
    except Exception as e:  # noqa: BLE001
        tg.send_text(f"❌ 리포트 실패: {e}\n{traceback.format_exc()[:1500]}")


def _start_report_async(ticker: str | None, *, reason: str) -> None:
    if not _lock.acquire(blocking=False):
        tg.send_text("이미 리포트를 만들고 있어요. 끝나면 다시 시도해 주세요.")
        return

    def job() -> None:
        try:
            _run_report(ticker, reason=reason)
        finally:
            _lock.release()

    threading.Thread(target=job, name="report-job", daemon=True).start()


def _handle(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or ""
    if chat_id is None:
        return
    if not tg.authorized_chat(chat_id):
        print(f"[bot] ignore unauthorized chat {chat_id} (expected {config.TELEGRAM_CHAT_ID})")
        return

    cmd, args = _parse_command(text)
    if cmd is None:
        tg.send_text("명령어를 보내 주세요. /help")
        return
    if cmd in ("/start", "/help"):
        tg.send_text(_HELP)
        return
    if cmd == "/report":
        ticker = args[0] if args else None
        if ticker and not _TICKER_RE.match(ticker):
            tg.send_text("티커 형식이 이상해요. 예: /report IREN")
            return
        _start_report_async(ticker, reason="수동 /report")
        return

    tg.send_text("모르는 명령이에요. /help")


def _scheduler_loop() -> None:
    """UTC 기준 화~토 05:07 에 일일 리포트 1회."""
    last_key: str | None = None
    print(
        f"[bot] scheduler on: UTC {_UTC_HOUR:02d}:{_UTC_MINUTE:02d} "
        f"weekdays={sorted(_UTC_WEEKDAYS)} (Tue-Sat)"
    )
    while True:
        try:
            now = dt.datetime.now(dt.timezone.utc)
            key = now.strftime("%Y-%m-%d")
            due = (
                now.weekday() in _UTC_WEEKDAYS
                and now.hour == _UTC_HOUR
                and now.minute == _UTC_MINUTE
                and last_key != key
            )
            if due:
                last_key = key
                print(f"[bot] scheduled daily report {key}")
                _start_report_async(None, reason="자동 스케줄")
        except Exception as e:  # noqa: BLE001
            print(f"[bot] scheduler 오류: {e}")
        time.sleep(20)


def run_forever() -> int:
    if not tg.is_configured():
        print("[bot] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 필요")
        return 1

    try:
        tg.set_my_commands(
            [
                ("report", "전체 종목 리포트"),
                ("help", "사용법"),
            ]
        )
    except tg.TelegramError as e:
        print(f"[bot] setMyCommands 실패(무시): {e}")

    threading.Thread(target=_scheduler_loop, name="scheduler", daemon=True).start()

    print(f"[bot] listening chat={config.TELEGRAM_CHAT_ID}")

    offset: int | None = None
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=25)
        except tg.TelegramError as e:
            print(f"[bot] getUpdates 오류: {e}")
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message")
            if not msg:
                continue
            try:
                _handle(msg)
            except Exception as e:  # noqa: BLE001
                print(f"[bot] handle 실패: {e}\n{traceback.format_exc()}")
                try:
                    tg.send_text(f"❌ 처리 오류: {e}")
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(run_forever())
