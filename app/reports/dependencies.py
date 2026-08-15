from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.reports.repository import ReportRepository
from app.reports.service import ReportService


def get_report_service(db: DbSession) -> ReportService:
    return ReportService(ReportRepository(db))


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
