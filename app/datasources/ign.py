"""Instituto Geográfico Nacional: PNOA orthophotos, current and historical.

The multitemporal series is the evidential core of the report: an outbuilding visible in
the 2010 flight and absent in 1956 is documentary proof, not an inference. Images are
fetched as PNG crops around the parcel bounding box and embedded in the PDF.

Licence: CC-BY 4.0 — cite "IGN (CC BY 4.0) ign.es".
"""

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO

from shapely.geometry.base import BaseGeometry

from app.core.logger import get_logger
from app.datasources.http import http_client, strip_ns

logger = get_logger(__name__)

WMS_PNOA = "https://www.ign.es/wms-inspire/pnoa-ma"
WMS_HISTORIC = "https://www.ign.es/wms/pnoa-historico"
CURRENT_LAYER = "OI.OrthoimageCoverage"

# Por debajo de esta variedad de color, la imagen es plana y no es una ortofoto. Medido
# sobre una parcela real: los vuelos sin cobertura devolvían PNG de 3 KB y los buenos
# entre 519 KB y 1,2 MB, pero el umbral va en colores y no en bytes para no descartar un
# recorte legítimo que resulte muy uniforme.
MIN_DISTINCT_COLOURS = 64

# Historical flights worth showing, oldest first. Layer names change between IGN
# deployments, so these are matched against GetCapabilities rather than requested blind.
HISTORIC_LAYER_HINTS = [
    "AMS_1956",
    "Interministerial",
    "Nacional_1981",
    "PNOA2004",
    "PNOA2010",
    "PNOA2016",
    "PNOA2020",
]


@dataclass(slots=True)
class Orthophoto:
    title: str
    png_base64: str
    source: str = "PNOA © Instituto Geográfico Nacional (CC BY 4.0)"


def bbox_around(
    geometry: BaseGeometry, buffer_ratio: float = 0.35
) -> tuple[float, float, float, float]:
    """Bounding box in EPSG:4326 with margin, so the parcel is not glued to the border."""
    min_x, min_y, max_x, max_y = geometry.bounds
    margin = max(max_x - min_x, max_y - min_y) * buffer_ratio
    return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)


async def _get_map(url: str, layer: str, bbox: tuple[float, ...], size: int = 700) -> bytes | None:
    async with http_client() as client:
        try:
            response = await client.get(
                url,
                params={
                    "SERVICE": "WMS",
                    "VERSION": "1.1.1",
                    "REQUEST": "GetMap",
                    "LAYERS": layer,
                    "SRS": "EPSG:4326",
                    "BBOX": ",".join(f"{value:.6f}" for value in bbox),
                    "WIDTH": size,
                    "HEIGHT": size,
                    "FORMAT": "image/png",
                    "STYLES": "",
                },
            )
        except Exception:  # noqa: BLE001 — one missing flight must not fail the report
            logger.warning("WMS GetMap failed for layer %s", layer, exc_info=True)
            return None

    if response.status_code != 200:
        return None
    if not response.headers.get("content-type", "").startswith("image"):
        return None
    if _is_blank(response.content):
        # Un vuelo que no cubre la zona no da error: devuelve una imagen válida y vacía.
        logger.info("Vuelo %s sin cobertura en esta zona: imagen en blanco, se descarta", layer)
        return None
    return response.content


def _is_blank(png: bytes) -> bool:
    """¿La imagen está vacía?

    El WMS del IGN responde 200 con un PNG uniforme cuando el vuelo no cubre el recorte
    pedido, y colar esas páginas en blanco en el informe lo abarata sin aportar nada.
    Se mide la variedad de color real, no el tamaño del fichero: un recorte legítimo de
    nieve o de embalse también comprimiría poco, y descartarlo sería peor que el problema.
    """
    from PIL import Image

    try:
        image = Image.open(BytesIO(png)).convert("RGB")
    except Exception:  # noqa: BLE001 — si no se puede leer, que decida quien la use
        logger.warning("No se ha podido inspeccionar la imagen del WMS", exc_info=True)
        return False

    colours = image.getcolors(maxcolors=MIN_DISTINCT_COLOURS)
    # getcolors devuelve None cuando hay más colores que el máximo pedido: eso es una
    # ortofoto de verdad. Una lista corta significa una imagen plana.
    return colours is not None


async def historic_layers() -> list[str]:
    """Which historical flights the IGN currently serves, in HISTORIC_LAYER_HINTS order."""
    async with http_client() as client:
        try:
            response = await client.get(
                WMS_HISTORIC, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"}
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception:  # noqa: BLE001
            logger.warning("WMS GetCapabilities unavailable", exc_info=True)
            return []

    names = [
        element.text for element in root.iter() if strip_ns(element.tag) == "Name" and element.text
    ]
    ordered: list[str] = []
    for hint in HISTORIC_LAYER_HINTS:
        ordered.extend(name for name in names if hint.lower() in name.lower())
    return ordered


async def fetch_time_series(geometry: BaseGeometry) -> list[Orthophoto]:
    """La serie completa en orden cronológico: del vuelo más antiguo al más reciente.

    El orden no es cosmético. La sección se titula «análisis multitemporal» y su valor
    probatorio está en poder decir «esta edificación aparece en 2010 y no en 1956»: leído
    al revés, o con la actual delante, el lector tiene que reconstruir la secuencia
    mentalmente y el argumento se pierde.
    """
    bbox = bbox_around(geometry)
    images: list[Orthophoto] = []

    # historic_layers() ya devuelve los vuelos de más antiguo a más reciente.
    for layer in await historic_layers():
        if png := await _get_map(WMS_HISTORIC, layer, bbox):
            images.append(
                Orthophoto(
                    title=f"Vuelo histórico: {layer}",
                    png_base64=base64.b64encode(png).decode(),
                )
            )

    # La actual cierra la serie: es, por definición, la más reciente.
    if png := await _get_map(WMS_PNOA, CURRENT_LAYER, bbox):
        images.append(
            Orthophoto(
                title="PNOA actual (máxima actualidad)", png_base64=base64.b64encode(png).decode()
            )
        )

    return images
