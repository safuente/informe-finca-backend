"""IGN, hidrografía INSPIRE: dónde pasan los cauces.

Responde a la pregunta que las láminas del SNCZI no responden. El SNCZI solo cartografía
inundabilidad de los tramos con estudio hidráulico, así que un arroyo sin estudio no
aparece en ninguna de sus capas aunque cruce la parcela — y sigue siendo dominio público
hidráulico, con las servidumbres que eso arrastra.

Se consulta por petición y no se carga en bloque: es un WFS vectorial que responde por
bbox en decenas de kilobytes, así que no compensa bajarse la hidrografía de España entera.

Ojo con lo que este servicio *no* dice: cartografía el eje del cauce, no el deslinde del
dominio público. Para el límite legal exacto hace falta la capa DPH del MITECO.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from app.core.logger import get_logger
from app.datasources.http import http_client, strip_ns

logger = get_logger(__name__)

WFS_URL = "https://servicios.idee.es/wfs-inspire/hidrografia"
WATERCOURSE_TYPE = "hy-n:WatercourseLink"
# Lagunas, charcas y embalses. Se consultan aparte porque son polígonos, no líneas, y
# porque una laguna dentro de la finca no es "agua cerca": puede ser dominio público.
WATER_BODY_TYPE = "hy-p:StandingWater"
MAX_FEATURES = 50

# Márgenes en grados alrededor de la parcela. 0,002° ≈ 200 m: cubre de sobra la zona de
# policía de 100 m, que es la distancia más lejana con consecuencia legal.
BBOX_MARGIN_DEG = 0.002

# Latitudes españolas, para detectar el orden de los ejes igual que en el WFS del Catastro.
_LAT_RANGE = (27.0, 45.0)


@dataclass(slots=True)
class WaterBody:
    """Una masa de agua estancada, en WKT EPSG:4326."""

    wkt: str
    name: str | None = None


@dataclass(slots=True)
class Watercourse:
    """Un tramo de cauce, en WKT EPSG:4326 para que PostGIS haga las cuentas."""

    wkt: str
    name: str | None = None
    source: str = "Hidrografía INSPIRE © Instituto Geográfico Nacional (CC BY 4.0)"


def _bbox(geometry: BaseGeometry) -> str:
    min_x, min_y, max_x, max_y = geometry.bounds
    # WFS 2.0 con EPSG:4326 espera el bbox en lat,lon.
    return (
        f"{min_y - BBOX_MARGIN_DEG},{min_x - BBOX_MARGIN_DEG},"
        f"{max_y + BBOX_MARGIN_DEG},{max_x + BBOX_MARGIN_DEG}"
    )


async def _get_features(type_name: str, bbox: str) -> ET.Element | None:
    async with http_client(timeout=45.0) as client:
        try:
            response = await client.get(
                WFS_URL,
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": type_name,
                    "bbox": bbox,
                    "count": MAX_FEATURES,
                },
            )
            response.raise_for_status()
            return ET.fromstring(response.content)
        except Exception:  # noqa: BLE001 — un servicio caído no tumba el informe
            logger.warning(
                "WFS de hidrografía del IGN no disponible (%s)", type_name, exc_info=True
            )
            return None


async def fetch_watercourses(geometry: BaseGeometry) -> list[Watercourse]:
    """Cauces alrededor de la parcela. Lista vacía si el servicio falla."""
    root = await _get_features(WATERCOURSE_TYPE, _bbox(geometry))
    if root is None:
        return []

    courses: list[Watercourse] = []
    for member in root.iter():
        if strip_ns(member.tag) not in {"member", "featureMember"}:
            continue
        name = _name_of(member)
        for element in member.iter():
            if strip_ns(element.tag) != "posList" or not element.text:
                continue
            coordinates = _coords(element.text)
            if len(coordinates) < 2:
                continue
            points = ", ".join(f"{x} {y}" for x, y in coordinates)
            courses.append(Watercourse(wkt=f"LINESTRING({points})", name=name))

    logger.info("IGN hidrografía: %d tramos de cauce alrededor de la parcela", len(courses))
    return courses


async def fetch_water_bodies(geometry: BaseGeometry) -> list[WaterBody]:
    """Lagunas, charcas y embalses alrededor de la parcela."""
    root = await _get_features(WATER_BODY_TYPE, _bbox(geometry))
    if root is None:
        return []

    bodies: list[WaterBody] = []
    for member in root.iter():
        if strip_ns(member.tag) not in {"member", "featureMember"}:
            continue
        name = _name_of(member)
        for element in member.iter():
            if strip_ns(element.tag) != "posList" or not element.text:
                continue
            coordinates = _coords(element.text)
            if len(coordinates) < 4:
                continue
            points = ", ".join(f"{x} {y}" for x, y in coordinates)
            bodies.append(WaterBody(wkt=f"POLYGON(({points}))", name=name))

    logger.info("IGN hidrografía: %d masas de agua alrededor de la parcela", len(bodies))
    return bodies


def _name_of(member: ET.Element) -> str | None:
    """El nombre oficial, cuando lo hay: muchos tramos menores van sin nombrar."""
    for element in member.iter():
        if strip_ns(element.tag) == "text" and element.text and element.text.strip():
            return element.text.strip()
    return None


def _coords(poslist: str) -> list[tuple[float, float]]:
    numbers = [float(value) for value in poslist.split()]
    pairs = list(zip(numbers[0::2], numbers[1::2], strict=False))
    # GML urn:EPSG::4326 va en lat/lon; el WKT que construimos necesita lon/lat.
    if pairs and all(_LAT_RANGE[0] < first < _LAT_RANGE[1] for first, _ in pairs):
        return [(second, first) for first, second in pairs]
    return pairs
