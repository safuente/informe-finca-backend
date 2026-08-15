"""Allow line geometries in layer_features.

The Red General de Vías Pecuarias publishes centrelines, not the legal strip, so the
MULTIPOLYGON column of 0001 would reject exactly the layer that matters most for public
domain. Widened to generic GEOMETRY; the report measures polygons by area and lines by
length (see GeometryKind in app/layers/catalog.py).

Revision ID: 0002_layer_geometry
Revises: 0001_initial
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_layer_geometry"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SRID = 25830


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE layer_features "
        f"ALTER COLUMN geom TYPE geometry(Geometry, {SRID}) USING geom::geometry"
    )


def downgrade() -> None:
    # Any line feature loaded meanwhile would fail the cast — deliberately, rather than
    # silently discarding vías pecuarias.
    op.execute(
        f"ALTER TABLE layer_features "
        f"ALTER COLUMN geom TYPE geometry(MultiPolygon, {SRID}) USING geom::geometry"
    )
