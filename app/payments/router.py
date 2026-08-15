from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.logger import get_logger
from app.payments import stripe_client
from app.payments.dependencies import PaymentServiceDep
from app.payments.exceptions import InvalidWebhookSignature, StripeNotConfigured
from app.payments.schemas import CheckoutSessionCreate, CheckoutSessionRead, WebhookAck

logger = get_logger(__name__)

router = APIRouter(prefix="/payments", tags=["pagos"])


@router.post(
    "/stripe/webhook",
    response_model=WebhookAck,
    summary="Webhook de Stripe",
)
async def stripe_webhook(
    request: Request,
    service: PaymentServiceDep,
    stripe_signature: str = Header("", alias="Stripe-Signature"),
) -> WebhookAck:
    """Punto de entrada del dinero: pago confirmado → informe en cola.

    Responde rápido y siempre 200 cuando el evento es auténtico: la generación va a
    Celery. Un 5xx aquí solo consigue que Stripe reintente y que el cliente espere más.
    """
    payload = await request.body()

    try:
        event = stripe_client.verify_event(payload, stripe_signature)
    except InvalidWebhookSignature as exc:
        logger.warning("Rejected Stripe webhook: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Firma no válida") from exc
    except StripeNotConfigured as exc:
        logger.error("Stripe webhook received but not configured: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    handled = await service.handle_event(event)
    return WebhookAck(handled=handled)


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear sesión de pago",
)
async def create_checkout_session(data: CheckoutSessionCreate) -> CheckoutSessionRead:
    """Alternativa al Payment Link cuando el formulario deba validar antes de cobrar."""
    try:
        session = stripe_client.create_checkout_session(data.refcat, data.email)
    except StripeNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return CheckoutSessionRead(checkout_url=session.url, session_id=session.id)
