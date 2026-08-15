from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, HTTPException, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from app.reports.dependencies import ReportServiceDep
from app.reports.models import ReportStatus
from app.reports.schemas import ReportStatusRead

router = APIRouter(prefix="/reports", tags=["informes"])


@router.get(
    "/{token}",
    response_model=ReportStatusRead,
    summary="Estado del informe",
)
async def get_report_status(
    service: ReportServiceDep,
    token: str = PathParam(..., min_length=16, max_length=64),
) -> ReportStatusRead:
    """El comprador consulta aquí mientras el worker trabaja.

    El token es el único credencial: no hay cuentas en el MVP, así que quien tiene el
    enlace tiene el informe. Por eso son 24 bytes aleatorios y no un id incremental.
    """
    report = await service.get_by_token(token)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese informe")
    return service.status_of(report)


@router.get(
    "/{token}/download",
    summary="Descargar el informe en PDF",
    response_class=FileResponse,
)
async def download_report(
    service: ReportServiceDep,
    token: str = PathParam(..., min_length=16, max_length=64),
) -> FileResponse:
    report = await service.get_by_token(token)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese informe")

    if report.status != ReportStatus.READY or not report.pdf_path:
        raise HTTPException(status.HTTP_409_CONFLICT, service.status_of(report).message)

    path = Path(report.pdf_path)
    # Off the event loop: reports live on a mounted volume, and a stat against a stalled
    # mount would otherwise block every other request in this worker.
    if not await anyio.to_thread.run_sync(path.exists):
        raise HTTPException(
            status.HTTP_410_GONE,
            "El fichero del informe ya no está disponible. Escríbenos y lo regeneramos.",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"informe-finca-{report.refcat}.pdf",
    )
