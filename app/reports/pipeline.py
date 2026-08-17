"""Builds the data payload of one report: fetch → intersect → interpret.

Split from the Celery task on purpose — the task owns retries and state transitions, this
owns the content. Everything it returns is JSON-serialisable and stored on the report row,
so a report can be re-rendered later without asking the public services again.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.datasources import copernicus, ign, ign_hidrografia, pvgis
from app.layers.catalog import BY_CODE
from app.layers.repository import LayerRepository
from app.layers.service import LayerService
from app.parcels.repository import ParcelRepository
from app.parcels.service import ParcelService
from app.reports import findings as interpret
from app.reports.models import Report
from app.reports.schemas import Finding

logger = get_logger(__name__)


async def build_payload(session: AsyncSession, report: Report) -> tuple[dict, int]:
    """Return (payload, parcel_id). Raises ParcelUnavailable if the parcel is unusable."""
    layer_service = LayerService(LayerRepository(session))
    parcel_service = ParcelService(ParcelRepository(session), layer_service)

    parcel = await parcel_service.get_or_fetch(report.refcat)
    geometry = await parcel_service.geometry_wgs84(parcel)
    area = parcel_service.compare_areas(parcel)
    reference_area = parcel.measured_area_m2 or parcel.cadastral_area_m2

    hits, coverage = await layer_service.hits_for_parcel(parcel.id, reference_area)

    # Three independent public services: fetched concurrently, and each one already
    # degrades to None/[] on its own rather than failing the report.
    orthophotos, solar, ndvi, watercourses, water_bodies = await asyncio.gather(
        ign.fetch_time_series(geometry),
        pvgis.fetch_solar_potential(parcel.lat, parcel.lon),
        copernicus.fetch_ndvi_series(geometry),
        ign_hidrografia.fetch_watercourses(geometry),
        ign_hidrografia.fetch_water_bodies(geometry),
    )

    # Los cauces vienen de un servicio externo, así que la geometría es de fuera pero la
    # medida la hace PostGIS: en 25830 los metros son metros.
    crosses, distance_m, inside_m = await parcel_service.repository.measure_against_lines(
        parcel.id, [course.wkt for course in watercourses]
    )
    watercourse_name = next((c.name for c in watercourses if c.name), None)

    water_inside_m2, water_distance_m = await parcel_service.repository.measure_against_polygons(
        parcel.id, [body.wkt for body in water_bodies]
    )
    water_body_name = next((b.name for b in water_bodies if b.name), None)
    ndvi_series = [{"month": point.month, "value": point.value} for point in ndvi]

    collected: list[Finding] = [interpret.area_finding(area)]
    collected.extend(interpret.layer_findings(hits))
    if built := interpret.built_area_finding(parcel.built_area_m2, bool(orthophotos)):
        collected.append(built)
    if ndvi_finding := interpret.ndvi_finding(ndvi_series, parcel.subplots):
        collected.append(ndvi_finding)
    if water := interpret.watercourse_finding(crosses, distance_m, inside_m, watercourse_name):
        collected.append(water)
    if pond := interpret.water_body_finding(water_inside_m2, water_distance_m, water_body_name):
        collected.append(pond)
    if solar_finding := interpret.solar_finding(solar.kwh_per_kwp_year if solar else None):
        collected.append(solar_finding)

    collected.sort(key=lambda finding: _SEVERITY_ORDER.index(finding.severity))
    dictamen = interpret.build_dictamen(collected)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "reference": f"IF-{datetime.now(UTC):%Y}-{report.id:05d}",
        "parcel": {
            "refcat": parcel.refcat,
            "municipality": parcel.municipality,
            "province": parcel.province,
            "use": parcel.use,
            "cadastral_area_m2": parcel.cadastral_area_m2,
            "built_area_m2": parcel.built_area_m2,
            "measured_area_m2": parcel.measured_area_m2,
            "lat": parcel.lat,
            "lon": parcel.lon,
            "subplots": parcel.subplots or [],
        },
        "area": area.model_dump(),
        "dictamen": {"verdict": dictamen.verdict, "summary": dictamen.summary},
        "findings": [finding.model_dump(mode="json") for finding in collected],
        "layers": [hit.model_dump(mode="json") for hit in hits],
        "caveats": interpret.coverage_caveats(coverage, BY_CODE),
        "orthophotos": [
            {"title": image.title, "png_base64": image.png_base64, "source": image.source}
            for image in orthophotos
        ],
        "ndvi": ndvi_series,
        "watercourses": {
            "count": len(watercourses),
            "crosses": crosses,
            "nearest_m": round(distance_m, 1) if distance_m is not None else None,
            "length_inside_m": round(inside_m, 1) if inside_m else 0.0,
            "name": watercourse_name,
        },
        "water_bodies": {
            "count": len(water_bodies),
            "area_inside_m2": round(water_inside_m2, 1),
            "nearest_m": round(water_distance_m, 1) if water_distance_m is not None else None,
            "name": water_body_name,
        },
        "solar": (
            {
                "kwh_per_kwp_year": solar.kwh_per_kwp_year,
                "optimal_slope_deg": solar.optimal_slope_deg,
            }
            if solar
            else None
        ),
        "recommendations": interpret.recommendations(collected),
        "sources": _sources(
            hits,
            bool(orthophotos),
            solar is not None,
            bool(ndvi_series),
            bool(watercourses) or bool(water_bodies),
        ),
    }

    logger.info(
        "Report payload for %s: %d findings, %d orthophotos, %d NDVI points",
        report.refcat,
        len(collected),
        len(orthophotos),
        len(ndvi_series),
    )
    return payload, parcel.id


_SEVERITY_ORDER = ["INCIDENCIA", "AFECCIÓN", "OBSERVACIÓN", "CONFORME"]


def _sources(
    hits, has_imagery: bool, has_solar: bool, has_ndvi: bool, has_water: bool = False
) -> list[str]:
    """Attribution list. Every source used, cited as its licence requires."""
    sources = ["Dirección General del Catastro (SEC) — datos no protegidos y geometría INSPIRE"]
    if has_imagery:
        sources.append("PNOA © Instituto Geográfico Nacional de España (CC BY 4.0, ign.es)")
    if has_solar:
        sources.append("PVGIS © Unión Europea, 2001-2024 (JRC)")
    if has_ndvi:
        # Fórmula canónica del aviso legal de Copernicus: la atribución debe llevar el
        # año de los datos. El uso comercial está permitido —Reglamento (UE) 377/2014 y
        # Delegado 1159/2013—, pero atribuir así es condición, no cortesía.
        sources.append(
            f"Contiene datos Copernicus Sentinel modificados {datetime.now(UTC):%Y}, "
            "procesados por informefinca.es"
        )
    if has_water:
        sources.append("Hidrografía INSPIRE © Instituto Geográfico Nacional de España (CC BY 4.0)")
    sources.extend(sorted({hit.source for hit in hits}))
    return sources
