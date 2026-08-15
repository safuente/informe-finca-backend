from pydantic import BaseModel, EmailStr

from app.parcels.schemas import RefcatMixin


class CheckoutSessionCreate(RefcatMixin):
    """Server-side Checkout, for when the Payment Link stops being enough.

    Phase 0 sends buyers straight to a Stripe Payment Link with the cadastral reference in
    client_reference_id; this endpoint does the same thing under our control (so the
    reference can be validated before charging anyone).
    """

    refcat: str
    email: EmailStr


class CheckoutSessionRead(BaseModel):
    checkout_url: str
    session_id: str


class WebhookAck(BaseModel):
    received: bool = True
    handled: str | None = None
