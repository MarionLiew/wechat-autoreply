"""SMTP 邮件发送（用 QQ 邮箱 SSL 端口 465 默认配置）。"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from config import settings

logger = logging.getLogger(__name__)


def send_mail(subject: str, body: str, to_addr: str | None = None) -> bool:
    """发送纯文本邮件。返回 True/False。

    使用 .env 的 SMTP_* 配置：host/port/user/password。
    自动用 SSL（465）或 STARTTLS（587）。
    """
    if not (settings.smtp_user and settings.smtp_password and (to_addr or settings.smtp_to)):
        logger.warning("SMTP 配置不完整，跳过发送：%s", subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_addr or settings.smtp_to
    msg.set_content(body)

    try:
        if settings.smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                   context=ctx, timeout=15) as s:
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        logger.info("📧 邮件已发出：%s → %s", subject, msg["To"])
        return True
    except Exception as exc:
        logger.error("邮件发送失败 (%s)：%s", subject, exc)
        return False
