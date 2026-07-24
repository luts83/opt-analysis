"""텔레그램 수동 리포트 봇 (long polling).

명령:
  /start, /help     — 사용법
  /report           — settings.json 전체 종목 리포트
  /report IREN      — 특정 종목만

Railway: cron 서비스와 별도로 상시 실행 서비스로 배포.
  Start Command: python bot.py
  Variables / Volume 은 daily 와 동일.

로컬:
  python bot.py
"""
from __future__ import annotations

import re
import sys
import threading
import traceback

import config
import main as daily_main
import telegram_notify as tg

_HELP = """옵션 리포트 봇

명령:
/report — 전체 종목 리포트 (조금 걸림)
/report IREN — 특정 종목만
/help — 이 안내

자동 발송은 Railway cron(아침)에서 따로 옵니다."""

_lock = threading.Lock()
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def _parse_command(text: str) -> tuple[str | None, list[str]]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd, parts[1:]


def _run_report(ticker: str | None) -> None:
    argv = ["--no-email"]
    if ticker:
        argv += ["--ticker", ticker.upper()]
    label = ticker.upper() if ticker else ", ".join(config.TICKERS)
    tg.send_text(f"⏳ 리포트 생성 중… ({label})\n끝나면 이어서 보내드릴게요.")
    try:
        code = daily_main.main(argv)
        if code != 0:
            tg.send_text("⚠️ 리포트 생성은 끝났지만 일부 종목에서 오류가 있었습니다.")
    except Exception as e:  # noqa: BLE001
        tg.send_text(f"❌ 리포트 실패: {e}\n{traceback.format_exc()[:1500]}")


def _handle(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or ""
    if chat_id is None:
        return
    if not tg.authorized_chat(chat_id):
        # 다른 사람 무시 (조용히)
        print(f"[bot] ignore chat {chat_id}")
        return

    cmd, args = _parse_command(text)
    if cmd in (None,):
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
        if not _lock.acquire(blocking=False):
            tg.send_text("이미 리포트를 만들고 있어요. 끝나면 다시 시도해 주세요.")
            return
        try:
            _run_report(ticker)
        finally:
            _lock.release()
        return

    tg.send_text("모르는 명령이에요. /help")


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

    print(f"[bot] listening as chat={config.TELEGRAM_CHAT_ID}")
    tg.send_text("✅ 리포트 봇 준비됨. /report 또는 /help")

    offset: int | None = None
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=25)
        except tg.TelegramError as e:
            print(f"[bot] getUpdates 오류: {e}")
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
