"""Thin wrapper over the Stripe SDK.

Isolated so the webhook path can be tested without the network and without a real
signing secret.
"""

import stripe

from app.core.config import settings
from app.payments.exceptions import InvalidWebhookSignature, StripeNotConfigured

stripe.api_key = settings.stripe_secret_key


def verify_event(payload: bytes, signature: str) -> dict:
    """Parse and authenticate a webhook delivery.

    Signature verification is the whole security model of this endpoint: it is public,
    unauthenticated, and it creates paid work. Never parse the body before this passes.
    """
    if not settings.stripe_webhook_secret:
        raise StripeNotConfigured("Falta STRIPE_WEBHOOK_SECRET")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise InvalidWebhookSignature(str(exc)) from exc
    return dict(event)


def create_checkout_session(refcat: str, email: str) -> stripe.checkout.Session:
    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise StripeNotConfigured("Faltan STRIPE_SECRET_KEY o STRIPE_PRICE_ID")

    return stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        client_reference_id=refcat,
        customer_email=email,
        success_url=f"{settings.site_base_url}/pedido-confirmado/?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.site_base_url}/#pedido",
        locale="es",
        metadata={"refcat": refcat},
    )


def refund(payment_intent_id: str) -> stripe.Refund:
    return stripe.Refund.create(payment_intent=payment_intent_id)
