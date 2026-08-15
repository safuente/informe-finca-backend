from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin
from app.shared.geo import SRID_STORAGE


class Parcel(Base, TimestampMixin):
    """A cadastral parcel, cached from the Catastro.

    Cached on purpose: the OVC publishes no rate limits and the free preview is a public
    endpoint. Every consultation also leaves an indexable parcel behind, which is the SEO
    engine of Phase 1.
    """

    __tablename__ = "parcels"

    id: Mapped[int] = mapped_column(primary_key=True)
    refcat: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    municipality: Mapped[str] = mapped_column(String(120), default="")
    province: Mapped[str] = mapped_column(String(120), default="")
    use: Mapped[str] = mapped_column(String(120), default="")

    cadastral_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    built_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    # Area of the INSPIRE geometry, measured in EPSG:25830 by PostGIS. The gap against
    # the declared area is the first finding of every report.
    measured_area_m2: Mapped[float] = mapped_column(Float, default=0.0)

    subplots: Mapped[list] = mapped_column(JSONB, default=list)

    lat: Mapped[float] = mapped_column(Float, default=0.0)
    lon: Mapped[float] = mapped_column(Float, default=0.0)
    geom: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=SRID_STORAGE, spatial_index=False),
        default=None,
    )

    refreshed_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (Index("ix_parcels_geom", "geom", postgresql_using="gist"),)
