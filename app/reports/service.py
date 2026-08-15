import secrets
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger
from app.reports.models import Report, ReportStatus
from app.reports.repository import ReportRepository
from app.reports.schemas import ReportStatusRead

logger = get_logger(__name__)

STATUS_MESSAGES = {
    ReportStatus.PENDING: "Pago recibido. El informe está en cola de generación.",
    ReportStatus.PROCESSING: "Generando el informe a partir de las fuentes oficiales.",
    ReportStatus.READY: "Informe disponible para descarga.",
    ReportStatus.FAILED: (
        "No hemos podido generar el informe. Nos hemos avisado y lo revisamos a mano; "
        "recibirás noticias en el correo del pedido."
    ),
    ReportStatus.REFUND_DUE: (
        "Esta parcela no permite generar el informe (catastro foral o datos insuficientes). "
        "Se devuelve el importe íntegro."
    ),
}


class ReportService:
    def __init__(self, repository: ReportRepository) -> None:
        self.repository = repository

    async def create(self, refcat: str, email: str) -> Report:
        report = Report(
            token=secrets.token_urlsafe(24),
            refcat=refcat.strip().upper(),
            email=email.strip(),
            status=ReportStatus.PENDING,
            payload={},
        )
        report = await self.repository.create(report)
        logger.info("Report %s queued for parcel %s", report.token, report.refcat)
        return report

    async def get_by_token(self, token: str) -> Report | None:
        return await self.repository.get_by_token(token)

    def status_of(self, report: Report) -> ReportStatusRead:
        status = ReportStatus(report.status)
        return ReportStatusRead(
            token=report.token,
            refcat=report.refcat,
            status=status,
            message=STATUS_MESSAGES[status],
            download_url=self.download_url(report) if status is ReportStatus.READY else None,
        )

    @staticmethod
    def download_url(report: Report) -> str:
        return f"{settings.public_base_url}/api/v1/reports/{report.token}/download"

    @staticmethod
    def pdf_path(report: Report) -> Path:
        return Path(settings.reports_dir) / f"informe-{report.refcat}-{report.token}.pdf"

    @staticmethod
    def enqueue(report: Report) -> None:
        """Hand the job to Celery.

        Imported here rather than at module import time so the API container never pulls
        in the task module (and through it WeasyPrint) just to answer a webhook.
        """
        from app.reports.tasks import generate_report

        generate_report.delay(report.id)
