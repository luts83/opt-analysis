"""Telegram Bot API 헬퍼 (발송 + long polling).

환경변수:
  TELEGRAM_BOT_TOKEN   BotFather 에서 발급
  TELEGRAM_CHAT_ID     허용된 채팅 ID (수동 명령도 이 ID만)
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import config

_API = "https://api.telegram.org"
_MAX_LEN = 3900


class TelegramError(Exception):
    pass


def is_configured() -> bool:
    return bool(
        getattr(config, "TELEGRAM_ENABLED", True)
        and config.TELEGRAM_BOT_TOKEN
        and config.TELEGRAM_CHAT_ID
    )


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def api_call(method: str, payload: dict | None = None, *, timeout: int = 60) -> dict:
    """Telegram Bot API 호출. payload 없으면 GET."""
    if not config.TELEGRAM_BOT_TOKEN:
        raise TelegramError("TELEGRAM_BOT_TOKEN 없음")
    url = f"{_API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TelegramError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise TelegramError(str(e)) from e
    if not body.get("ok"):
        raise TelegramError(f"Telegram API 거부: {body}")
    return body


def _split_chunks(text: str, limit: int = _MAX_LEN) -> list[str]:
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


def _post_send_message(text: str, chat_id: str | None = None) -> None:
    api_call(
        "sendMessage",
        {
            "chat_id": chat_id or config.TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def send_text(text: str, chat_id: str | None = None) -> int:
    """텍스트를 보내며, 필요 시 여러 메시지로 분할. 보낸 메시지 수 반환."""
    if not is_configured() and not (config.TELEGRAM_BOT_TOKEN and chat_id):
        raise TelegramError(
            "텔레그램 미설정: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 확인하세요."
        )
    chunks = _split_chunks(text)
    if not chunks:
        return 0
    for i, chunk in enumerate(chunks):
        prefix = f"({i + 1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        _post_send_message(prefix + chunk, chat_id=chat_id)
    return len(chunks)


def send_reports(title: str, reports: list[str], chat_id: str | None = None) -> int:
    n = 0
    n += send_text(title, chat_id=chat_id)
    for report in reports:
        n += send_text(report, chat_id=chat_id)
    return n


def get_updates(offset: int | None = None, timeout: int = 25) -> list[dict]:
    """long polling. timeout 초 동안 서버가 기다림."""
    payload: dict = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    # long poll 여유
    body = api_call("getUpdates", payload, timeout=timeout + 15)
    return list(body.get("result") or [])


def set_my_commands(commands: list[tuple[str, str]]) -> None:
    api_call(
        "setMyCommands",
        {"commands": [{"command": c, "description": d} for c, d in commands]},
    )


def authorized_chat(chat_id) -> bool:
    return str(chat_id) == str(config.TELEGRAM_CHAT_ID)
