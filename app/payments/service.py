import re

from app.core.config import settings
from app.core.logger import get_logger
from app.core.mailer import send_email
from app.payments.models import Payment, PaymentStatus
from app.payments.repository import PaymentRepository
from app.reports.service import ReportService

logger = get_logger(__name__)

REFCAT_RE = re.compile(r"^[A-Z0-9]{14}$|^[A-Z0-9]{20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

HANDLED_EVENTS = {"checkout.session.completed", "charge.refunded"}


class PaymentService:
    """Turns a Stripe event into a queued report — exactly once."""

    def __init__(self, repository: PaymentRepository, reports: ReportService) -> None:
        self.repository = repository
        self.reports = reports

    async def handle_event(self, event: dict) -> str:
        event_type = event.get("type", "")
        if event_type not in HANDLED_EVENTS:
            logger.info("Ignoring Stripe event %s", event_type)
            return "ignored"

        event_id = event.get("id", "")
        if await self.repository.get_by_event_id(event_id):
            # Stripe retries until it gets a 2xx; a retry must not produce a second report.
            logger.info("Stripe event %s already processed", event_id)
            return "duplicate"

        if event_type == "charge.refunded":
            return await self._handle_refund(event)
        return await self._handle_checkout(event)

    async def _handle_checkout(self, event: dict) -> str:
        session = event["data"]["object"]
        refcat = (session.get("client_reference_id") or "").strip().upper().replace(" ", "")
        email = self._extract_email(session)

        payment = Payment(
            stripe_event_id=event["id"],
            stripe_session_id=session.get("id"),
            stripe_payment_intent=session.get("payment_intent"),
            refcat=refcat or None,
            email=email,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency") or "eur",
            raw_event={"type": event["type"], "session_id": session.get("id")},
        )

        problem = self._validate(refcat, email)
        if problem:
            payment.status = PaymentStatus.NEEDS_ATTENTION
            payment.note = problem
            await self.repository.create(payment)
            self._alert_ops(payment, problem)
            return "needs_attention"

        report = await self.reports.create(refcat, email)
        payment.status = PaymentStatus.PAID
        payment.report_id = report.id
        await self.repository.create(payment)

        # Enqueued after the payment row is committed: if the worker is quick, it must
        # not find a report whose payment is still uncommitted.
        self.reports.enqueue(report)
        logger.info("Payment %s → report %s (%s)", payment.stripe_session_id, report.token, refcat)
        return "queued"

    async def _handle_refund(self, event: dict) -> str:
        charge = event["data"]["object"]
        payment = Payment(
            stripe_event_id=event["id"],
            stripe_payment_intent=charge.get("payment_intent"),
            amount_cents=charge.get("amount_refunded") or 0,
            currency=charge.get("currency") or "eur",
            status=PaymentStatus.REFUNDED,
            email=(charge.get("billing_details") or {}).get("email"),
            raw_event={"type": event["type"], "charge_id": charge.get("id")},
        )
        await self.repository.create(payment)
        logger.info("Refund recorded for payment_intent %s", payment.stripe_payment_intent)
        return "refund_recorded"

    @staticmethod
    def _extract_email(session: dict) -> str:
        details = session.get("customer_details") or {}
        return (details.get("email") or session.get("customer_email") or "").strip()

    @staticmethod
    def _validate(refcat: str, email: str) -> str | None:
        if not refcat:
            return "El pago no trae referencia catastral (client_reference_id vacío)."
        if not REFCAT_RE.match(refcat):
            return f"Referencia catastral con formato inválido: {refcat!r}."
        if not email or not EMAIL_RE.match(email):
            return "El pago no trae un correo de entrega válido."
        return None

    @staticmethod
    def _alert_ops(payment: Payment, problem: str) -> None:
        """A paid order we cannot process is the worst failure mode: never silent."""
        logger.error("Payment needs attention (%s): %s", payment.stripe_session_id, problem)
        send_email(
            settings.mail_from,
            "[ACCIÓN] Pago sin datos válidos",
            (
                f"Sesión de Stripe: {payment.stripe_session_id}\n"
                f"Importe: {payment.amount_cents / 100:.2f} {payment.currency.upper()}\n"
                f"Referencia recibida: {payment.refcat}\n"
                f"Correo recibido: {payment.email}\n"
                f"Problema: {problem}\n\n"
                "Hay que contactar con el cliente o devolver el importe.\n"
            ),
        )
