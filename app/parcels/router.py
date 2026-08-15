from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import preview_rate_limit
from app.datasources import catastro
from app.datasources.exceptions import DataSourceError
from app.parcels.dependencies import ParcelServiceDep
from app.parcels.exceptions import ParcelUnavailable
from app.parcels.schemas import REFCAT_PATTERN, ParcelPreview, ParcelRead

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
    except ParcelUnavailable as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
