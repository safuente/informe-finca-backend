from pydantic import BaseModel

from app.layers.catalog import GeometryKind, LayerKind


class LayerHit(BaseModel):
    """What one reference layer has to say about one parcel."""

    layer_code: str
    label: str
    # Qué significa la sigla; lo pone el catálogo, no la consulta.
    meaning: str = ""
    kind: LayerKind
    source: str
    geometry: GeometryKind = GeometryKind.AREA
    intersects: bool
    # Only meaningful when intersects is True and the layer is polygonal.
    area_m2: float = 0.0
    area_ratio: float = 0.0
    # Only meaningful when intersects is True and the layer is linear: how far the axis
    # runs inside the parcel.
    length_m: float = 0.0
    feature_names: list[str] = []
    # Only meaningful when intersects is False and the layer reports proximity.
    nearest_name: str | None = None
    nearest_distance_m: float | None = None


class LayerCoverage(BaseModel):
    """Whether a layer is loaded at all for this area.

    An empty layer must never be read as "no affection": in Phase 1 only Castilla y León
    is loaded, so a parcel in Aragón would otherwise get a clean bill of health it did
    not earn.
    """

    layer_code: str
    loaded: bool
