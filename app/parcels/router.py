from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse

from app.api.deps import DbSession, preview_rate_limit
from app.datasources import catastro
from app.datasources.exceptions import DataSourceError, ParcelNotCovered, ParcelNotRustic
from app.parcels.dependencies import ParcelServiceDep
from app.parcels.exceptions import ParcelUnavailable
from app.parcels.schemas import REFCAT_PATTERN, ParcelPreview, ParcelRead
from app.reports.pipeline import build_preview_payload
from app.reports.renderer import render_html

router = APIRouter(prefix="/parcels", tags=["parcelas"])


@router.get(
    "/lookup",
    summary="Referencia catastral a partir de coordenadas",
    response_model=dict,
)
async def lookup_refcat(
    lat: float = Query(..., ge=27, le=44, description="Latitud WGS84"),
    lon: float = Query(..., ge=-19, le=5, description="Longitud WGS84"),
) -> dict:
    """Geolocalización inversa del Catastro, para quien no conoce su referencia."""
    try:
        return {"refcat": await catastro.refcat_from_coords(lat, lon)}
    except DataSourceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/{refcat}",
    response_model=ParcelRead,
    summary="Datos catastrales de la parcela",
)
async def get_parcel(
    service: ParcelServiceDep,
    refcat: str = Path(..., pattern=REFCAT_PATTERN),
) -> ParcelRead:
    try:
        parcel = await service.get_or_fetch(refcat.upper())
    except ParcelUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ParcelRead.model_validate(parcel)


@router.get(
    "/{refcat}/preview",
    response_model=ParcelPreview,
    summary="Vista previa gratuita",
    dependencies=[Depends(preview_rate_limit)],
)
async def get_preview(
    service: ParcelServiceDep,
    refcat: str = Path(..., pattern=REFCAT_PATTERN),
) -> ParcelPreview:
    """Gancho SEO: cada parcela consultada es una URL indexable en la web pública.

    Da identificación, coherencia de superficie y qué capas se han podido comprobar.
    El dictamen —hallazgos, severidad y recomendaciones— es el informe de pago.
    """
    try:
        return await service.preview(refcat.upper())
    except (ParcelNotRustic, ParcelNotCovered) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except ParcelUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get(
    "/{refcat}/preview/report",
    response_class=HTMLResponse,
    summary="Vista previa del informe, en HTML",
    dependencies=[Depends(preview_rate_limit)],
)
async def get_preview_report(
    session: DbSession,
    refcat: str = Path(..., pattern=REFCAT_PATTERN),
    checkout_url: str = Query("", description="Enlace de pago al que lleva el botón"),
) -> HTMLResponse:
    """El informe real de esta parcela, con lo caro sin calcular y el detalle reservado.

    Devuelve HTML y no JSON a propósito: lo que convence es ver el documento con la parcela
    propia dentro, no una lista de lo que incluye. Solo consulta Catastro —cacheado— y la
    base de datos de capas, así que un curioso no gasta cuota de ningún servicio externo.
    """
    try:
        payload = await build_preview_payload(session, refcat.upper())
    except (ParcelNotRustic, ParcelNotCovered) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except ParcelUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return HTMLResponse(
        render_html(payload, checkout_url=checkout_url),
        # Estas URLs son un espacio infinito de páginas casi idénticas: indexarlas es la
        # definición de doorway page y arriesga el dominio entero. El SEO va en las guías.
        headers={"X-Robots-Tag": "noindex"},
    )
