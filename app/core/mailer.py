"""Report delivery by email.

Deliberately plain smtplib: the only sender is the Celery task, which is synchronous,
and the volume of Phase 1 does not justify a transactional-email SDK.
"""

import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    attachment: tuple[str, bytes] | None = None,
    reply_to: str = "",
) -> bool:
    """Return True if the message was handed to the SMTP server."""
    if not settings.mail_enabled:
        logger.warning("SMTP not configured; email to %s not sent. Subject: %s", to, subject)
        return False

    message = EmailMessage()
    message["From"] = f"{settings.mail_from_name} <{settings.mail_from}>"
    message["To"] = to
    message["Subject"] = subject
    # Con Reply-To, responder al aviso de interés escribe a quien preguntó y no a
    # nuestro propio buzón, que es de donde sale el mensaje.
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    if attachment is not None:
        filename, content = attachment
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)

    logger.info("Email sent to %s (%s)", to, subject)
    return True
