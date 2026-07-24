"""Telegram Bot API 로 리포트 발송 (HTTPS — Railway Hobby에서도 동작).

환경변수:
  TELEGRAM_BOT_TOKEN   BotFather 에서 발급
  TELEGRAM_CHAT_ID     받을 채팅(본인) ID
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import config

_API = "https://api.telegram.org"
# Telegram 한도 4096. 여유 두고 자름.
_MAX_LEN = 3900


class TelegramError(Exception):
    pass


def is_configured() -> bool:
    return bool(
        getattr(config, "TELEGRAM_ENABLED", True)
        and config.TELEGRAM_BOT_TOKEN
        and config.TELEGRAM_CHAT_ID
    )


def _split_chunks(text: str, limit: int = _MAX_LEN) -> list[str]:
    """긴 본문을 문단 경계 위주로 잘라 여러 메시지로 만든다."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return chunks


def _post_send_message(text: str) -> None:
    url = f"{_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        import certifi
        import ssl

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # certifi 없으면 시스템 기본
        context = None
    try:
        with urllib.request.urlopen(req, timeout=60, context=context) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TelegramError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise TelegramError(str(e)) from e

    if not body.get("ok"):
        raise TelegramError(f"Telegram API 거부: {body}")


def send_text(text: str) -> int:
    """텍스트를 보내며, 필요 시 여러 메시지로 분할. 보낸 메시지 수 반환."""
    if not is_configured():
        raise TelegramError(
            "텔레그램 미설정: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 확인하세요."
        )
    chunks = _split_chunks(text)
    if not chunks:
        return 0
    for i, chunk in enumerate(chunks):
        prefix = f"({i + 1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        _post_send_message(prefix + chunk)
    return len(chunks)


def send_reports(title: str, reports: list[str]) -> int:
    """제목 1통 + 티커별 리포트(분할 가능). 총 메시지 수 반환."""
    n = 0
    n += send_text(title)
    for report in reports:
        n += send_text(report)
    return n
