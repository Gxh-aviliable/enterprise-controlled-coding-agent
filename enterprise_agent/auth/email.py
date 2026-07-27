"""Email helpers for authentication flows.

SMTP configuration is optional in local development. When SMTP is not
configured, password reset codes are written to the backend log so the
flow can be tested without an external email provider.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from enterprise_agent.config.settings import settings

logger = logging.getLogger(__name__)


def is_smtp_configured() -> bool:
    """Return True when all required SMTP settings are present."""
    return bool(
        settings.SMTP_HOST
        and settings.SMTP_USERNAME
        and settings.SMTP_PASSWORD
        and settings.SMTP_FROM_EMAIL
    )


def _send_smtp_message(to_email: str, code: str) -> None:
    """Send the reset code email via configured SMTP server."""
    message = EmailMessage()
    message["Subject"] = "Your Mini Claude Code password reset code"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "Use this verification code to reset your password:\n\n"
        f"{code}\n\n"
        f"This code expires in {settings.PASSWORD_RESET_CODE_TTL_SECONDS // 60} minutes."
    )

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(message)


async def send_password_reset_code(to_email: str, code: str) -> bool:
    """Send or log a password reset code.

    Returns:
        True if SMTP was used, False if development logging was used.
    """
    if not is_smtp_configured():
        logger.warning("Password reset code for %s: %s", to_email, code)
        return False

    await asyncio.to_thread(_send_smtp_message, to_email, code)
    return True
