"""Aviso por correo de que alguien quiere el informe de una parcela.

Va al buzón propio, no al del visitante: es una notificación interna, no una
respuesta automática. Al visitante ya le contesta la web con su pantalla de gracias.
"""

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logger import get_logger
from app.core.mailer import send_email
from app.interest.schemas import InterestCreate

logger = get_logger(__name__)


def _body(interest: InterestCreate) -> str:
    lines = [
        "Tiene interés en que el informe sea generado.",
        "",
        f"Referencia catastral: {interest.refcat}",
        f"Correo de contacto:   {interest.email}",
        f"Ficha del Catastro:   https://www1.sedecatastro.gob.es/CYCBienInmueble/OVCListaBienes.aspx?RC={interest.refcat}",
        f"Vista previa:         {settings.site_base_url}/vista-previa/?refcat={interest.refcat}",
    ]
    if interest.message:
        lines += ["", "Mensaje:", interest.message]
    return "\n".join(lines)


async def notify_interest(interest: InterestCreate) -> bool:
    """Manda el aviso. Devuelve False si no se pudo entregar al servidor SMTP."""
    subject = f"Interés en informe · {interest.refcat}"

    try:
        # smtplib es síncrono: fuera del hilo del bucle de eventos, o bloquea la API
        # entera hasta 30 segundos si el SMTP no responde.
        delivered = await run_in_threadpool(
            send_email,
            settings.mail_from,
            subject,
            _body(interest),
            reply_to=str(interest.email),
        )
    except Exception:  # noqa: BLE001 — un SMTP caído no puede tumbar el endpoint
        delivered = False
        logger.exception("SMTP falló al enviar el aviso de interés")

    if not delivered:
        # El lead no se pierde aunque el correo no salga: queda en el log con todo
        # lo necesario para contestar a mano.
        logger.warning(
            "AVISO DE INTERÉS NO ENVIADO — refcat=%s email=%s", interest.refcat, interest.email
        )
    return delivered
