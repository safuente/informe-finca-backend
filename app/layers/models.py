from geoalchemy2 import Geometry
from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin
from app.shared.geo import SRID_STORAGE


class LayerFeature(Base, TimestampMixin):
    """One feature of a bulk-loaded reference layer (flood zone, protected area...).

    A single table instead of one per source: every affection is answered by the same
    ST_Intersects query, and adding SIGPAC or vías pecuarias later is a load job, not a
    migration. Source-specific attributes survive in `attributes` (JSONB) so the report
    can quote the official name of the ZEPA or the return period of the flood polygon.
    """

    __tablename__ = "layer_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    layer_code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Generic GEOMETRY, not MULTIPOLYGON: the Red General de Vías Pecuarias publishes
    # axes (lines), and a polygon-only column would reject the one layer whose whole
    # point is that it crosses parcels. See GeometryKind in catalog.py.
    geom: Mapped[object] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=SRID_STORAGE, spatial_index=False)
    )

    __table_args__ = (
        Index("ix_layer_features_geom", "geom", postgresql_using="gist"),
        Index("ix_layer_features_code_geom", "layer_code", "geom", postgresql_using="gist"),
    )
