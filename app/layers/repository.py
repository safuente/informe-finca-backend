from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.layers.catalog import GeometryKind
from app.layers.models import LayerFeature
from app.shared.base_repository import BaseRepository


class LayerRepository(BaseRepository[LayerFeature]):
    model = LayerFeature

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def intersection(
        self, layer_code: str, parcel_id: int, geometry: GeometryKind = GeometryKind.AREA
    ) -> tuple[float, list[str]]:
        """Size of the intersection and the names of the features hit.

        Returns m² for polygon layers and metres for line layers — ST_Area of a line is
        zero, which would silently read as "no affection" for vías pecuarias.

        The parcel geometry is read from its row rather than passed as WKT: it keeps the
        polygon inside the database, where the GiST index can use it.
        """
        measure = "ST_Length" if geometry is GeometryKind.LINE else "ST_Area"
        result = await self.db.execute(
            text(
                f"""
                SELECT COALESCE(SUM({measure}(ST_Intersection(f.geom, p.geom))), 0) AS size,
                       COALESCE(
                           ARRAY_AGG(DISTINCT f.name) FILTER (WHERE f.name IS NOT NULL),
                           ARRAY[]::varchar[]
                       ) AS names
                FROM layer_features f
                JOIN parcels p ON p.id = :parcel_id
                WHERE f.layer_code = :layer_code
                  AND ST_Intersects(f.geom, p.geom)
                """
            ),
            {"layer_code": layer_code, "parcel_id": parcel_id},
        )
        row = result.one()
        return float(row.size or 0.0), list(row.names or [])

    async def nearest(
        self, layer_code: str, parcel_id: int, max_distance_m: float = 10_000
    ) -> tuple[str | None, float | None]:
        """Closest feature of the layer within max_distance_m, using the KNN operator."""
        result = await self.db.execute(
            text(
                """
                SELECT f.name AS name,
                       ST_Distance(f.geom, p.geom) AS distance
                FROM layer_features f
                JOIN parcels p ON p.id = :parcel_id
                WHERE f.layer_code = :layer_code
                  AND ST_DWithin(f.geom, p.geom, :max_distance)
                ORDER BY f.geom <-> p.geom
                LIMIT 1
                """
            ),
            {
                "layer_code": layer_code,
                "parcel_id": parcel_id,
                "max_distance": max_distance_m,
            },
        )
        row = result.first()
        if row is None:
            return None, None
        return row.name, float(row.distance)

    async def loaded_codes(self) -> set[str]:
        """Layer codes that actually have features loaded."""
        result = await self.db.execute(text("SELECT DISTINCT layer_code FROM layer_features"))
        return {row[0] for row in result.all()}

    async def covers_parcel(self, layer_code: str, parcel_id: int) -> bool:
        """Whether the layer is loaded for this parcel's area — the coverage check.

        'This area' is a 50 km radius around the parcel: layers are loaded region by
        region, so a parcel with no feature of that layer within 50 km is almost certainly
        outside the loaded extent, and its silence means nothing.

        Measured against the stored parcel geometry rather than against coordinates passed
        in by the caller: a parcel whose lat/lon were never populated would otherwise be
        checked around (0, 0) and every layer would look unloaded.
        """
        result = await self.db.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM layer_features f
                    JOIN parcels p ON p.id = :parcel_id
                    WHERE f.layer_code = :layer_code
                      AND ST_DWithin(f.geom, p.geom, 50000)
                )
                """
            ),
            {"layer_code": layer_code, "parcel_id": parcel_id},
        )
        return bool(result.scalar())
