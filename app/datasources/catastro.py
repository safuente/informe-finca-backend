"""Dirección General del Catastro: non-protected data + INSPIRE geometry.

Two services, two purposes:
  - OVC (SOAP-ish REST returning XML): descriptive data and reverse geocoding.
  - INSPIRE WFS: the parcel polygon, which is what makes measured area, intersections
    and image framing possible.

Attribution required in every output: "Dirección General del Catastro".
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Polygon

from app.datasources.exceptions import DataSourceError, GeometryUnavailable, ParcelNotFound
from app.datasources.http import find_text, http_client, strip_ns

OVC_BASE = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC"
OVC_COORDS = f"{OVC_BASE}/OVCCoordenadas.asmx/Consulta_RCCOOR"
OVC_DNPRC = f"{OVC_BASE}/OVCCallejero.asmx/Consulta_DNPRC"
WFS_PARCELS = "https://ovc.catastro.meh.es/INSPIRE/wfsCP.aspx"

# Spanish latitudes, used to detect the WFS axis order (urn EPSG::4326 is lat/lon).
_LAT_RANGE = (27.0, 45.0)


@dataclass(slots=True)
class Subplot:
    """Subparcela de cultivo."""

    crop: str = ""
    intensity: str = ""
    area_m2: float = 0.0


@dataclass(slots=True)
class CadastralData:
    refcat: str
    municipality: str = ""
    province: str = ""
    use: str = ""
    area_m2: float = 0.0
    built_area_m2: float = 0.0
    subplots: list[Subplot] = field(default_factory=list)


async def _get_xml(url: str, params: dict) -> ET.Element:
    async with http_client() as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        try:
            return ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise DataSourceError(f"Respuesta no XML de {url}") from exc


async def refcat_from_coords(lat: float, lon: float) -> str:
    """Reverse geocoding: coordinates → cadastral reference."""
    root = await _get_xml(
        OVC_COORDS, {"SRS": "EPSG:4326", "Coordenada_X": lon, "Coordenada_Y": lat}
    )
    pc1, pc2 = find_text(root, "pc1"), find_text(root, "pc2")
    if not pc1:
        raise ParcelNotFound(f"El Catastro no devuelve parcela en {lat}, {lon}")
    return pc1 + pc2


async def fetch_cadastral_data(refcat: str) -> CadastralData:
    """Non-protected data: use, area and crop subplots."""
    root = await _get_xml(OVC_DNPRC, {"Provincia": "", "Municipio": "", "RC": refcat})

    error_code = find_text(root, "cod")
    if error_code and error_code != "0" and find_text(root, "des"):
        raise ParcelNotFound(f"Catastro: {find_text(root, 'des')} ({refcat})")

    data = CadastralData(
        refcat=refcat,
        municipality=find_text(root, "nm"),
        province=find_text(root, "np"),
        use=find_text(root, "luso") or find_text(root, "cn"),
    )
    if not data.municipality:
        raise ParcelNotFound(f"El Catastro no reconoce la referencia {refcat}")

    # <ssp> is the plot area; inside a <spr> block it means that subplot's area, so the
    # first occurrence (the outer one) is taken and the subplot total refines it below.
    for element in root.iter():
        tag = strip_ns(element.tag)
        if tag == "ssp" and element.text and not data.area_m2:
            data.area_m2 = _as_float(element.text)
        elif tag == "sfc" and element.text and not data.built_area_m2:
            data.built_area_m2 = _as_float(element.text)

    for block in root.iter():
        if strip_ns(block.tag) != "spr":
            continue
        subplot = Subplot()
        for element in block.iter():
            tag, text = strip_ns(element.tag), (element.text or "").strip()
            if not text:
                continue
            if tag == "dcc":
                subplot.crop = text
            elif tag == "ip":
                subplot.intensity = text
            elif tag == "ssp":
                subplot.area_m2 = _as_float(text)
        data.subplots.append(subplot)

    # With subplots present, the plot area is their sum — more reliable than guessing
    # which <ssp> was the outer one.
    if data.subplots:
        subplot_total = sum(s.area_m2 for s in data.subplots)
        if subplot_total > 0:
            data.area_m2 = subplot_total

    return data


async def fetch_geometry(refcat: str) -> MultiPolygon:
    """Parcel geometry from the INSPIRE WFS (GetParcel stored query), in EPSG:4326."""
    async with http_client() as client:
        response = await client.get(
            WFS_PARCELS,
            params={
                "service": "wfs",
                "version": "2",
                "request": "getfeature",
                "STOREDQUERIE_ID": "GetParcel",
                "srsname": "EPSG::4326",
                "REFCAT": refcat[:14],
            },
        )
        response.raise_for_status()

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise GeometryUnavailable(
            f"WFS INSPIRE devolvió una respuesta ilegible para {refcat}"
        ) from exc

    rings = [
        element.text
        for element in root.iter()
        if strip_ns(element.tag) == "posList" and element.text
    ]
    if not rings:
        raise GeometryUnavailable(f"El WFS INSPIRE no devuelve geometría para {refcat}")

    polygons = [Polygon(_coords(ring)) for ring in rings]
    polygons = [p for p in polygons if p.is_valid and p.area > 0]
    if not polygons:
        raise GeometryUnavailable(f"Geometría degenerada para {refcat}")

    # The first ring is the outer boundary; further rings of the same feature are holes,
    # but distinguishing holes from parts needs the full GML tree. Keeping the largest
    # part is enough for framing and intersections, and never overstates the area.
    largest = max(polygons, key=lambda p: p.area)
    return MultiPolygon([largest])


def _coords(poslist: str) -> list[tuple[float, float]]:
    numbers = [float(value) for value in poslist.split()]
    pairs = list(zip(numbers[0::2], numbers[1::2], strict=False))
    # urn:EPSG::4326 is lat/lon; shapely wants lon/lat. Detect by range instead of
    # trusting the axis order, which varies between WFS deployments.
    if all(_LAT_RANGE[0] < first < _LAT_RANGE[1] for first, _ in pairs):
        return [(second, first) for first, second in pairs]
    return pairs


def _as_float(text: str) -> float:
    try:
        return float(text.replace(".", "").replace(",", ".") if "," in text else text)
    except (TypeError, ValueError):
        return 0.0
