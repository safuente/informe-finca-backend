from app.core.logger import get_logger
from app.layers.catalog import CATALOG, GeometryKind, LayerSpec
from app.layers.repository import LayerRepository
from app.layers.schemas import LayerCoverage, LayerHit

logger = get_logger(__name__)


class LayerService:
    """Answers 'what affects this parcel' from the bulk-loaded PostGIS layers."""

    def __init__(self, repository: LayerRepository) -> None:
        self.repository = repository

    async def hits_for_parcel(
        self, parcel_id: int, parcel_area_m2: float
    ) -> tuple[list[LayerHit], list[LayerCoverage]]:
        """Per-layer verdict plus the coverage map that qualifies it.

        Returns hits only for layers that are loaded for this area; the coverage list
        tells the report which layers could not be checked, so silence is never sold as
        a clean result.
        """
        loaded = await self.repository.loaded_codes()
        hits: list[LayerHit] = []
        coverage: list[LayerCoverage] = []

        for spec in CATALOG:
            if spec.code not in loaded or not await self.repository.covers_parcel(
                spec.code, parcel_id
            ):
                coverage.append(LayerCoverage(layer_code=spec.code, loaded=False))
                continue

            coverage.append(LayerCoverage(layer_code=spec.code, loaded=True))
            hits.append(await self._hit(spec, parcel_id, parcel_area_m2))

        return hits, coverage

    async def _hit(self, spec: LayerSpec, parcel_id: int, parcel_area_m2: float) -> LayerHit:
        size, names = await self.repository.intersection(spec.code, parcel_id, spec.geometry)
        is_line = spec.geometry is GeometryKind.LINE
        ratio = 0.0 if is_line else (size / parcel_area_m2 if parcel_area_m2 else 0.0)

        # A crossing line has no area share to compare against, so any measurable length
        # counts: one metre of vía pecuaria inside the parcel is still public domain.
        significant = size > 0 and (is_line or ratio >= spec.min_area_ratio)

        if significant:
            return LayerHit(
                layer_code=spec.code,
                label=spec.label,
                kind=spec.kind,
                source=spec.source,
                geometry=spec.geometry,
                intersects=True,
                area_m2=0.0 if is_line else round(size, 1),
                area_ratio=round(ratio, 4),
                length_m=round(size, 1) if is_line else 0.0,
                feature_names=names,
            )

        hit = LayerHit(
            layer_code=spec.code,
            label=spec.label,
            kind=spec.kind,
            source=spec.source,
            geometry=spec.geometry,
            intersects=False,
        )
        if spec.report_nearest:
            hit.nearest_name, distance = await self.repository.nearest(spec.code, parcel_id)
            hit.nearest_distance_m = round(distance, 0) if distance is not None else None
        return hit
