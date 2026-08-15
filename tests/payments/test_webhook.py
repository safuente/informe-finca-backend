"""The money path. Tested without Stripe, without Postgres and without Celery.

Three failure modes are worth guarding: a replayed webhook must not produce a second
report, a paid order with no usable cadastral reference must never be dropped silently,
and a forged request must not reach the service at all.
"""

import pytest

from app.main import app
from app.payments.dependencies import get_payment_service
from app.payments.models import Payment, PaymentStatus
from app.payments.service import PaymentService
from app.reports.models import Report


class FakePaymentRepository:
    def __init__(self) -> None:
        self.saved: list[Payment] = []

    async def get_by_event_id(self, event_id: str) -> Payment | None:
        return next((p for p in self.saved if p.stripe_event_id == event_id), None)

    async def create(self, payment: Payment) -> Payment:
        payment.id = len(self.saved) + 1
        self.saved.append(payment)
        return payment


class FakeReportService:
    def __init__(self) -> None:
        self.created: list[Report] = []
        self.enqueued: list[int] = []

    async def create(self, refcat: str, email: str) -> Report:
        report = Report(
            id=len(self.created) + 1,
            token=f"tok{len(self.created)}",
            refcat=refcat,
            email=email,
        )
        self.created.append(report)
        return report

    def enqueue(self, report: Report) -> None:
        self.enqueued.append(report.id)


def checkout_event(
    event_id: str = "evt_1",
    refcat: str | None = "24145A00500123",
    email: str | None = "comprador@example.es",
) -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "payment_intent": "pi_1",
                "client_reference_id": refcat,
                "customer_details": {"email": email},
                "amount_total": 3900,
                "currency": "eur",
            }
        },
    }


@pytest.fixture
def service() -> tuple[PaymentService, FakePaymentRepository, FakeReportService]:
    repository, reports = FakePaymentRepository(), FakeReportService()
    return PaymentService(repository, reports), repository, reports


async def test_paid_checkout_queues_one_report(service):
    payment_service, repository, reports = service

    assert await payment_service.handle_event(checkout_event()) == "queued"

    assert [r.refcat for r in reports.created] == ["24145A00500123"]
    assert reports.enqueued == [1]
    assert repository.saved[0].status == PaymentStatus.PAID
    assert repository.saved[0].report_id == 1


async def test_replayed_event_does_not_generate_a_second_report(service):
    payment_service, _, reports = service

    await payment_service.handle_event(checkout_event())
    assert await payment_service.handle_event(checkout_event()) == "duplicate"

    assert len(reports.created) == 1
    assert reports.enqueued == [1]


async def test_lowercase_reference_is_normalised_not_rejected(service):
    payment_service, _, reports = service

    await payment_service.handle_event(checkout_event(refcat=" 24145a00500123 "))

    assert reports.created[0].refcat == "24145A00500123"


@pytest.mark.parametrize(
    "refcat,email",
    [
        (None, "comprador@example.es"),
        ("NOPE", "comprador@example.es"),
        ("24145A00500123", None),
    ],
)
async def test_unusable_order_is_kept_for_a_human(service, refcat, email, monkeypatch):
    payment_service, repository, reports = service
    monkeypatch.setattr("app.payments.service.send_email", lambda *a, **k: True)

    result = await payment_service.handle_event(checkout_event(refcat=refcat, email=email))

    assert result == "needs_attention"
    assert repository.saved[0].status == PaymentStatus.NEEDS_ATTENTION
    assert repository.saved[0].note  # says why, so ops can act
    assert not reports.created  # nothing generated for an order we cannot fulfil


async def test_unknown_event_type_is_ignored(service):
    payment_service, repository, _ = service
    result = await payment_service.handle_event({"id": "evt_x", "type": "invoice.paid"})
    assert result == "ignored"
    assert not repository.saved


async def test_webhook_rejects_a_bad_signature(client):
    """No signature, no processing: the endpoint is public and creates paid work.

    The service dependency is still *constructed* (FastAPI resolves dependencies before
    the handler runs); what must not happen is the event being handled.
    """
    handled: list[dict] = []

    class SpyService:
        async def handle_event(self, event: dict) -> str:  # pragma: no cover
            handled.append(event)
            return "queued"

    app.dependency_overrides[get_payment_service] = SpyService
    try:
        response = await client.post(
            "/api/v1/payments/stripe/webhook",
            content=b'{"id":"evt_forged","type":"checkout.session.completed"}',
            headers={"Stripe-Signature": "t=1,v1=forged"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code in (400, 503)
    assert not handled
