"""Celery task that turns a paid order into a delivered PDF.

Owns retries and state transitions; the content is built in pipeline.py. The task is
synchronous (Celery) but the pipeline is async, so each run gets its own event loop and
its own database engine — see core.database.worker_session for why that matters.
"""

import asyncio
from datetime import UTC, datetime

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import worker_session
from app.core.logger import get_logger, setup_logging
from app.core.mailer import send_email
from app.parcels.exceptions import ParcelUnavailable
from app.reports.models import Report, ReportStatus
from app.reports.pipeline import build_payload
from app.reports.renderer import render_report
from app.reports.repository import ReportRepository
from app.reports.service import ReportService

setup_logging()
logger = get_logger(__name__)


@celery_app.task(bind=True, name="reports.generate", max_retries=2, default_retry_delay=120)
def generate_report(self, report_id: int) -> str:
    try:
        return asyncio.run(_generate(report_id))
    except ParcelUnavailable:
        # Not retryable: the parcel simply cannot be processed. Already recorded as
        # REFUND_DUE inside _generate.
        return "refund_due"
    except SoftTimeLimitExceeded:
        logger.error("Report %s hit the soft time limit", report_id)
        asyncio.run(_mark_failed(report_id, "Tiempo de generación agotado"))
        raise
    except Exception as exc:  # noqa: BLE001 — retry, then give up loudly
        logger.exception("Report %s failed", report_id)
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_failed(report_id, str(exc)))
            raise
        raise self.retry(exc=exc) from exc


async def _generate(report_id: int) -> str:
    async with worker_session() as session:
        repository = ReportRepository(session)
        service = ReportService(repository)

        report = await repository.get(report_id)
        if report is None:
            raise ValueError(f"Report {report_id} no existe")
        if report.status == ReportStatus.READY:
            logger.info("Report %s already generated; skipping", report.token)
            return report.token

        await repository.update(report, status=ReportStatus.PROCESSING, error=None)

        try:
            payload, parcel_id = await build_payload(session, report)
        except ParcelUnavailable as exc:
            await repository.update(report, status=ReportStatus.REFUND_DUE, error=str(exc))
            _notify_refund(report)
            raise

        destination = service.pdf_path(report)
        # Rendering is CPU-bound and blocking: keep it off the event loop so the
        # worker's other coroutines (and the soft time limit) still get a look in.
        await asyncio.to_thread(render_report, payload, destination)

        await repository.update(
            report,
            parcel_id=parcel_id,
            payload=payload,
            pdf_path=str(destination),
            status=ReportStatus.READY,
            generated_at=datetime.now(UTC).replace(tzinfo=None),
        )

        if _deliver(report, destination):
            await repository.update(report, delivered_at=datetime.now(UTC).replace(tzinfo=None))

        logger.info("Report %s ready for %s", report.token, report.refcat)
        return report.token


def _deliver(report: Report, pdf_path) -> bool:
    body = (
        f"Hola,\n\n"
        f"Ya está listo el informe de la parcela {report.refcat}.\n\n"
        f"Descarga: {ReportService.download_url(report)}\n\n"
        "El PDF va también adjunto a este correo. Es un informe orientativo elaborado con "
        "fuentes públicas oficiales: no es una tasación ni sustituye a la nota simple del "
        "Registro de la Propiedad ni a una peritación.\n\n"
        "Si algo no cuadra, responde a este correo y lo revisamos.\n\n"
        f"{settings.mail_from_name}\n"
    )
    return send_email(
        report.email,
        f"Tu informe de la finca {report.refcat}",
        body,
        attachment=(pdf_path.name, pdf_path.read_bytes()),
    )


def _notify_refund(report: Report) -> None:
    send_email(
        report.email,
        f"No podemos generar el informe de {report.refcat} — devolución",
        (
            f"Hola,\n\nNo hemos podido generar el informe de la parcela {report.refcat}: "
            "el Catastro del Estado no sirve datos suficientes para esa referencia (es el "
            "caso de los catastros forales de País Vasco y Navarra, y de referencias sin "
            "geometría publicada).\n\n"
            "Te devolvemos el importe íntegro. La devolución la tramitamos a mano en las "
            "próximas horas y verás el abono en tu método de pago.\n\n"
            f"{settings.mail_from_name}\n"
        ),
    )
    # Ops copy: refunds are manual in Phase 1 and someone has to press the button.
    send_email(
        settings.mail_from,
        f"[ACCIÓN] Devolución pendiente · {report.refcat}",
        f"Informe {report.token} marcado REFUND_DUE.\n"
        f"Cliente: {report.email}\nMotivo: {report.error}\n",
    )


async def _mark_failed(report_id: int, message: str) -> None:
    async with worker_session() as session:
        repository = ReportRepository(session)
        report = await repository.get(report_id)
        if report is None:
            return
        await repository.update(report, status=ReportStatus.FAILED, error=message[:2000])
        send_email(
            settings.mail_from,
            f"[ACCIÓN] Informe fallido · {report.refcat}",
            f"Informe {report.token} en estado FAILED.\n"
            f"Cliente: {report.email}\nError: {message}\n",
        )
