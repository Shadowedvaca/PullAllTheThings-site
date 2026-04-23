"""Async email sending via aiosmtplib."""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from sv_common.config_cache import SmtpConfig

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    pass


async def send_email(
    smtp_config: SmtpConfig,
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
) -> None:
    """Send an HTML email via SMTP.

    Uses SSL on port 465, STARTTLS on all other ports (default 587).
    Raises EmailSendError on failure.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = to

    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    use_tls = smtp_config.port == 465

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_config.host,
            port=smtp_config.port,
            username=smtp_config.user,
            password=smtp_config.password,
            use_tls=use_tls,
            start_tls=not use_tls,
        )
    except Exception as exc:
        raise EmailSendError(f"SMTP send failed: {exc}") from exc
