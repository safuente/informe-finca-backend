from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin


class PaymentStatus(StrEnum):
    PAID = "paid"
    # Paid, but the order data is unusable (no cadastral reference in the session, or a
    # malformed one). A human has to reach the customer — never silently dropped.
    NEEDS_ATTENTION = "needs_attention"
    REFUNDED = "refunded"


class Payment(Base, TimestampMixin):
    """A completed Stripe checkout.

    The row exists mostly for idempotency and for the audit trail on the one path that
    touches money: Stripe retries webhooks, and a retry must not generate (or charge for)
    a second report.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    stripe_event_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(120), index=True, default=None)
    stripe_payment_intent: Mapped[str | None] = mapped_column(String(120), default=None)

    refcat: Mapped[str | None] = mapped_column(String(20), index=True, default=None)
    email: Mapped[str | None] = mapped_column(String(255), default=None)

    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="eur")
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PAID, index=True)

    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    raw_event: Mapped[dict] = mapped_column(JSONB, default=dict)
