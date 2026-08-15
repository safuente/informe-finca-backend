"""Geometry conventions shared by every domain.

Everything is stored in EPSG:25830 (ETRS89 / UTM 30N), the SRID the SPEC fixes for the
whole PostGIS side: it is metric, so areas, distances and intersections are computed by
the database instead of being approximated in Python. Inputs arrive in EPSG:4326 (the
Catastro WFS, and coordinates from users), so conversion happens at the edge.
"""

from shapely.geometry.base import BaseGeometry

SRID_STORAGE = 25830
SRID_WGS84 = 4326


def to_storage_srid(wkt_4326: str) -> str:
    """SQL expression that turns a WGS84 WKT literal into a storage-SRID geometry."""
    return f"ST_Transform(ST_GeomFromText('{wkt_4326}', {SRID_WGS84}), {SRID_STORAGE})"


def as_multipolygon_wkt(geometry: BaseGeometry) -> str:
    """WKT for a polygon or multipolygon, always as MULTIPOLYGON.

    Cadastral parcels are sometimes multipart (a parcel split by a road), so the column
    is MULTIPOLYGON and single polygons are promoted rather than stored in a second column.
    """
    if geometry.geom_type == "Polygon":
        return f"MULTIPOLYGON({geometry.wkt[len('POLYGON') :]})"
    if geometry.geom_type == "MultiPolygon":
        return geometry.wkt
    raise ValueError(f"Unsupported geometry type for a parcel: {geometry.geom_type}")
