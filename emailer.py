"""Gmail SMTP 이메일 발송.

인증정보는 .env / GitHub Actions Secrets 의 환경변수로 읽는다 (코드에 노출 금지):
  EMAIL_SENDER         발신 Gmail 주소
  EMAIL_APP_PASSWORD   Gmail 앱 비밀번호 (일반 비밀번호 아님)
  EMAIL_RECIPIENTS     수신자(쉼표로 여러 명)

본문은 plain text(이모지/마크다운 그대로), 필요 시 JSON 파일 첨부.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import config

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465  # SSL


class EmailError(Exception):
    pass


def is_configured() -> bool:
    """발송에 필요한 값이 모두 있는지."""
    return bool(
        config.EMAIL_ENABLED
        and config.EMAIL_SENDER
        and config.EMAIL_APP_PASSWORD
        and config.EMAIL_RECIPIENTS
    )


def send_email(
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
) -> None:
    """plain text 본문 + (선택) 첨부파일로 메일 발송."""
    if not is_configured():
        raise EmailError(
            "이메일 설정 누락: EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECIPIENTS "
            "환경변수를 확인하세요."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = ", ".join(config.EMAIL_RECIPIENTS)
    msg.set_content(body)

    for path in attachments or []:
        p = Path(path)
        if not p.exists():
            continue
        data = p.read_bytes()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="json" if p.suffix == ".json" else "octet-stream",
            filename=p.name,
        )

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, context=context) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except Exception as e:  # noqa: BLE001
        raise EmailError(f"Gmail SMTP 발송 실패: {e}") from e
