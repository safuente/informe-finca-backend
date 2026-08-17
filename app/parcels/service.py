import json
from datetime import UTC, datetime, timedelta

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.core.config import settings
from app.core.logger import get_logger
from app.datasources import catastro
from app.datasources.exceptions import DataSourceError
from app.layers.service import LayerService
from app.parcels.exceptions import ParcelUnavailable
from app.parcels.models import Parcel
from app.parcels.repository import ParcelRepository
from app.parcels.schemas import AreaComparison, ParcelPreview, Subplot
from app.shared.geo import SRID_WGS84, as_multipolygon_wkt

logger = get_logger(__name__)

# Below this the difference between declared and measured area is cartographic noise.
AREA_DISCREPANCY_THRESHOLD = 0.05

FULL_REPORT_CONTENTS = [
    "Dictamen de comprador con severidad y confianza por hallazgo",
    "Serie de ortofotos 1956→hoy y detección de edificaciones no declaradas",
    "Afecciones: inundabilidad (T10/T100/T500/ZFP), Red Natura 2000, montes UP y vías pecuarias",
    "Serie NDVI de 8 años con interpretación prudente del uso agrícola",
    "Potencial fotovoltaico y pendiente media",
    "Recomendaciones previas a la transmisión",
]

BASE_SOURCES = ["Dirección General del Catastro (datos no protegidos y geometría INSPIRE)"]

DISCLAIMER = (
    "Vista previa orientativa generada a partir de fuentes públicas oficiales. "
    "No es una tasación ni sustituye a la nota simple del Registro de la Propiedad "
    "ni a una peritación."
)


class ParcelService:
    def __init__(self, repository: ParcelRepository, layers: LayerService) -> None:
        self.repository = repository
        self.layers = layers

    async def get_or_fetch(self, refcat: str, *, force_refresh: bool = False) -> Parcel:
        """Cached parcel, fetching from the Catastro when missing or stale."""
        parcel = await self.repository.get_by_refcat(refcat)
        if parcel and not force_refresh and self._is_fresh(parcel):
            return parcel

        try:
            data = await catastro.fetch_cadastral_data(refcat)
            geometry = await catastro.fetch_geometry(refcat)
        except DataSourceError as exc:
            raise ParcelUnavailable(str(exc)) from exc

        centroid = geometry.centroid
        fields = {
            "municipality": data.municipality,
            "province": data.province,
            "use": data.use,
            "cadastral_area_m2": data.area_m2,
            "built_area_m2": data.built_area_m2,
            "subplots": [
                {"crop": s.crop, "intensity": s.intensity, "area_m2": s.area_m2}
                for s in data.subplots
            ],
            "lat": centroid.y,
            "lon": centroid.x,
            "refreshed_at": datetime.now(UTC).replace(tzinfo=None),
        }

        if parcel is None:
            parcel = await self.repository.create(Parcel(refcat=refcat, **fields))
        else:
            parcel = await self.repository.update(parcel, **fields)

        await self.repository.set_geometry(parcel, as_multipolygon_wkt(geometry))
        logger.info("Parcel %s refreshed from Catastro", refcat)
        return parcel

    async def preview(self, refcat: str) -> ParcelPreview:
        """Free preview. Deliberately withholds the dictamen: that is the product."""
        parcel = await self.get_or_fetch(refcat)
        _, coverage = await self.layers.hits_for_parcel(
            parcel.id, parcel.measured_area_m2 or parcel.cadastral_area_m2
        )

        return ParcelPreview(
            refcat=parcel.refcat,
            municipality=parcel.municipality,
            province=parcel.province,
            use=parcel.use,
            cadastral_area_m2=parcel.cadastral_area_m2,
            lat=parcel.lat,
            lon=parcel.lon,
            area=self.compare_areas(parcel),
            subplots=[Subplot(**item) for item in parcel.subplots or []],
            checked_layers=[item.layer_code for item in coverage if item.loaded],
            unavailable_layers=[item.layer_code for item in coverage if not item.loaded],
            included_in_full_report=FULL_REPORT_CONTENTS,
            sources=BASE_SOURCES,
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def compare_areas(parcel: Parcel) -> AreaComparison:
        cadastral, measured = parcel.cadastral_area_m2, parcel.measured_area_m2
        ratio = (measured - cadastral) / cadastral if cadastral and measured else 0.0
        return AreaComparison(
            cadastral_area_m2=round(cadastral, 1),
            measured_area_m2=round(measured, 1),
            difference_ratio=round(ratio, 4),
            is_significant=abs(ratio) > AREA_DISCREPANCY_THRESHOLD,
        )

    async def geometry_in(self, parcel: Parcel, srid: int) -> BaseGeometry:
        """Geometría de la parcela en el SRID pedido, como objeto shapely."""
        geojson = await self.repository.geometry_as_geojson(parcel.id, srid)
        if not geojson:
            raise ParcelUnavailable(f"La parcela {parcel.refcat} no tiene geometría almacenada")
        return shape(json.loads(geojson))

    async def geometry_wgs84(self, parcel: Parcel) -> BaseGeometry:
        """Shapely geometry in WGS84 — lo que esperan los WMS y Copernicus."""
        return await self.geometry_in(parcel, SRID_WGS84)

    @staticmethod
    def _is_fresh(parcel: Parcel) -> bool:
        if parcel.refreshed_at is None or parcel.geom is None:
            return False
        age = datetime.now(UTC).replace(tzinfo=None) - parcel.refreshed_at
        return age < timedelta(days=settings.parcel_cache_days)
