from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.payments.repository import PaymentRepository
from app.payments.service import PaymentService
from app.reports.repository import ReportRepository
from app.reports.service import ReportService


def get_payment_service(db: DbSession) -> PaymentService:
    return PaymentService(PaymentRepository(db), ReportService(ReportRepository(db)))


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
