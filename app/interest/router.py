from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import RateLimiter
from app.core.logger import get_logger
from app.interest.schemas import InterestCreate
from app.interest.service import notify_interest

logger = get_logger(__name__)

router = APIRouter(prefix="/interest", tags=["interés"])

# Un endpoint abierto que manda correo es un vector de spam: sin límite, cualquiera
# puede usarlo para inundar el buzón desde el que enviamos los informes, y eso acaba
# costando la reputación del dominio.
interest_rate_limit = RateLimiter("interest", limit=10, window_seconds=3600)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Aviso de interés desde la lista de espera",
    dependencies=[Depends(interest_rate_limit)],
)
async def create_interest(interest: InterestCreate) -> dict:
    """Notifica por correo que alguien quiere el informe de una parcela.

    Responde 502 si el correo no sale, en vez de un 202 optimista: la web solo lleva
    a la pantalla de gracias con una respuesta correcta, y dar las gracias por algo
    que no ha llegado es peor que enseñar un error con una dirección alternativa.
    """
    if not await notify_interest(interest):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "No hemos podido registrar la solicitud. Escríbenos a contacto@informefinca.es.",
        )
    return {"ok": True}
