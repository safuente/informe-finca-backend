from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.parcels.models import Parcel
from app.shared.base_repository import BaseRepository
from app.shared.geo import SRID_STORAGE, SRID_WGS84


class ParcelRepository(BaseRepository[Parcel]):
    model = Parcel

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def get_by_refcat(self, refcat: str) -> Parcel | None:
        result = await self.db.execute(select(Parcel).where(Parcel.refcat == refcat))
        return result.scalar_one_or_none()

    async def set_geometry(self, parcel: Parcel, wkt_4326: str) -> float:
        """Store the geometry reprojected to the storage SRID; return its measured area.

        The reprojection runs in PostGIS rather than in Python: EPSG:25830 is metric, so
        ST_Area gives real square metres instead of the flat-earth approximation the PoC
        used.
        """
        await self.db.execute(
            text(
                f"""
                UPDATE parcels
                SET geom = ST_Multi(
                        ST_Transform(ST_GeomFromText(:wkt, {SRID_WGS84}), {SRID_STORAGE})
                    )
                WHERE id = :parcel_id
                """
            ),
            {"wkt": wkt_4326, "parcel_id": parcel.id},
        )
        result = await self.db.execute(
            text("SELECT ST_Area(geom) FROM parcels WHERE id = :parcel_id"),
            {"parcel_id": parcel.id},
        )
        area = float(result.scalar() or 0.0)
        await self.db.execute(
            text("UPDATE parcels SET measured_area_m2 = :area WHERE id = :parcel_id"),
            {"area": area, "parcel_id": parcel.id},
        )
        await self.db.commit()
        await self.db.refresh(parcel)
        return area

    async def measure_against_lines(
        self, parcel_id: int, wkts: list[str]
    ) -> tuple[bool, float | None, float | None]:
        """(cruza, metros al más próximo, metros de eje dentro) frente a unas líneas 4326.

        Las geometrías vienen de un servicio externo, no de nuestras tablas, pero las
        cuentas las sigue haciendo PostGIS: en 25830 los metros son metros, mientras que
        restar grados de longitud daría distancias distintas según la latitud.
        """
        if not wkts:
            return False, None, None

        values = ", ".join(f"(ST_GeomFromText(:wkt{i}, {SRID_WGS84}))" for i in range(len(wkts)))
        params: dict[str, object] = {f"wkt{i}": wkt for i, wkt in enumerate(wkts)}
        params["parcel_id"] = parcel_id

        result = await self.db.execute(
            text(
                f"""
                WITH lines AS (
                    SELECT ST_Transform(g, {SRID_STORAGE}) AS geom
                    FROM (VALUES {values}) AS v(g)
                ), par AS (
                    SELECT geom FROM parcels WHERE id = :parcel_id
                )
                SELECT bool_or(ST_Intersects(l.geom, p.geom)) AS crosses,
                       min(ST_Distance(l.geom, p.geom)) AS distance,
                       COALESCE(SUM(ST_Length(ST_Intersection(l.geom, p.geom))), 0) AS inside
                FROM lines l, par p
                """
            ),
            params,
        )
        row = result.one()
        return (
            bool(row.crosses),
            float(row.distance) if row.distance is not None else None,
            float(row.inside or 0.0),
        )

    async def measure_against_polygons(
        self, parcel_id: int, wkts: list[str]
    ) -> tuple[float, float | None]:
        """(m² dentro de la parcela, metros a la más próxima) frente a unos polígonos 4326."""
        if not wkts:
            return 0.0, None

        values = ", ".join(f"(ST_GeomFromText(:wkt{i}, {SRID_WGS84}))" for i in range(len(wkts)))
        params: dict[str, object] = {f"wkt{i}": wkt for i, wkt in enumerate(wkts)}
        params["parcel_id"] = parcel_id

        result = await self.db.execute(
            text(
                f"""
                WITH shapes AS (
                    SELECT ST_MakeValid(ST_Transform(g, {SRID_STORAGE})) AS geom
                    FROM (VALUES {values}) AS v(g)
                ), par AS (
                    SELECT geom FROM parcels WHERE id = :parcel_id
                )
                SELECT COALESCE(SUM(ST_Area(ST_Intersection(s.geom, p.geom))), 0) AS inside,
                       min(ST_Distance(s.geom, p.geom)) AS distance
                FROM shapes s, par p
                """
            ),
            params,
        )
        row = result.one()
        return float(row.inside or 0.0), (float(row.distance) if row.distance is not None else None)

    async def geometry_as_geojson(self, parcel_id: int, srid: int = SRID_WGS84) -> str | None:
        """Geometría de la parcela en el SRID pedido.

        Por defecto WGS84, que es lo que esperan los WMS. Pero algunos servicios miden la
        resolución en las unidades del CRS que les mandas, y ahí hay que darles metros:
        pedir 10 "de resolución" con la geometría en grados significa 10 grados por píxel.
        """
        result = await self.db.execute(
            # El cast explícito es necesario: asyncpg no puede inferir el tipo del
            # parámetro dentro de ST_Transform y lo manda como texto.
            text(
                "SELECT ST_AsGeoJSON(ST_Transform(geom, CAST(:srid AS integer))) "
                "FROM parcels WHERE id = :pid"
            ),
            {"pid": parcel_id, "srid": srid},
        )
        return result.scalar()
