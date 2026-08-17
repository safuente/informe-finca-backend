"""Copernicus Data Space Ecosystem: monthly NDVI series from Sentinel-2.

NDVI is the only source that shows *use over time* — whether the plot was actually farmed
or has been idle for years. It is also the easiest to over-read: a fallow year looks like
abandonment, so the interpretation layer never states it categorically (see
app/reports/findings.py).

Requires a free CDSE account (OAuth client credentials). Without credentials the report is
still generated, minus this section.
"""

from dataclasses import dataclass
from datetime import date
from math import cos, radians

from shapely.geometry.base import BaseGeometry

from app.core.config import settings
from app.core.logger import get_logger
from app.datasources.http import http_client

logger = get_logger(__name__)

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
STATISTICS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# Resolución nativa de Sentinel-2. Se traduce a grados según la latitud de la parcela: un
# grado de latitud son ~111,3 km en cualquier sitio, pero uno de longitud se encoge con el
# coseno de la latitud, y España va de los 27° de Canarias a los 43,8° del Cantábrico.
TARGET_RESOLUTION_M = 10
METRES_PER_DEGREE_LAT = 111_320


def _degree_resolution(geometry: BaseGeometry) -> tuple[float, float]:
    latitude = geometry.centroid.y
    resy = TARGET_RESOLUTION_M / METRES_PER_DEGREE_LAT
    resx = TARGET_RESOLUTION_M / (METRES_PER_DEGREE_LAT * max(cos(radians(latitude)), 0.1))
    return resx, resy


EVALSCRIPT_NDVI = """//VERSION=3
function setup() { return { input: [{bands:["B04","B08","dataMask"]}],
  output: [{id:"ndvi", bands:1}, {id:"dataMask", bands:1}] }; }
function evaluatePixel(s) {
  return { ndvi: [(s.B08-s.B04)/(s.B08+s.B04)], dataMask: [s.dataMask] }; }
"""


# Un solo aviso por proceso: si CDSE está caído, cada informe fallaría igual y no tiene
# sentido inundar el correo de operaciones con el mismo problema.
_alerted = False


def _alert_ops(exc: Exception) -> None:
    global _alerted
    if _alerted:
        return
    _alerted = True

    from app.core.mailer import send_email

    send_email(
        settings.mail_from,
        "[ACCIÓN] Copernicus NDVI configurado pero fallando",
        (
            "Las credenciales de CDSE están configuradas, pero la consulta de NDVI está "
            "fallando, así que los informes salen sin la serie de vegetación.\n\n"
            f"Error: {type(exc).__name__}: {exc}\n\n"
            "Causas habituales: cuota mensual agotada (10.000 processing units), cliente "
            "OAuth revocado o caducado, o el servicio de Copernicus caído.\n"
            "Panel: https://shapps.dataspace.copernicus.eu/dashboard/\n"
        ),
    )


@dataclass(slots=True)
class NdviPoint:
    month: str  # YYYY-MM
    value: float


async def fetch_ndvi_series(geometry: BaseGeometry, years: int | None = None) -> list[NdviPoint]:
    """Monthly mean NDVI over the parcel. Empty list when CDSE is not configured.

    La geometría va en EPSG:4326 y la resolución **en grados**, no en metros. Sentinel Hub
    interpreta `resx`/`resy` en las unidades del CRS recibido, así que pedir "10" con la
    geometría en grados significaba 10 grados por píxel y la API respondía 400. Mandarla en
    EPSG:25830 para poder pedir metros tampoco vale: esta colección no acepta ese CRS y
    devuelve 500. La salida es quedarse en grados y convertir la resolución.
    """
    if not settings.ndvi_enabled:
        logger.info("NDVI skipped: CDSE credentials not configured")
        return []

    years = years or settings.ndvi_years
    today = date.today()
    resx, resy = _degree_resolution(geometry)

    async with http_client(timeout=120.0) as client:
        try:
            token_response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.cdse_client_id,
                    "client_secret": settings.cdse_client_secret,
                },
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]

            stats_response = await client.post(
                STATISTICS_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "input": {
                        "bounds": {
                            "geometry": geometry.__geo_interface__,
                            "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                        },
                        "data": [
                            {
                                "type": "sentinel-2-l2a",
                                "dataFilter": {"maxCloudCoverage": 30},
                            }
                        ],
                    },
                    "aggregation": {
                        "timeRange": {
                            "from": f"{today.year - years}-01-01T00:00:00Z",
                            "to": f"{today.isoformat()}T00:00:00Z",
                        },
                        "aggregationInterval": {"of": "P1M"},
                        "evalscript": EVALSCRIPT_NDVI,
                        "resx": resx,
                        "resy": resy,
                    },
                    "calculations": {"ndvi": {}},
                },
            )
            stats_response.raise_for_status()
            payload = stats_response.json()
        except Exception as exc:  # noqa: BLE001 — NDVI is a nice-to-have, never a blocker
            # Configurado y fallando no es lo mismo que no configurado. Lo primero
            # significa que estamos vendiendo informes sin una sección que anunciamos, y
            # como el informe degrada con elegancia nadie se enteraría: el log se lo
            # traga. Igual que con un pago que no se puede atender, alguien tiene que
            # recibir el aviso.
            logger.warning("Copernicus NDVI unavailable", exc_info=True)
            _alert_ops(exc)
            return []

    series: list[NdviPoint] = []
    for interval in payload.get("data", []):
        try:
            stats = interval["outputs"]["ndvi"]["bands"]["B0"]["stats"]
            series.append(
                NdviPoint(month=interval["interval"]["from"][:7], value=round(stats["mean"], 3))
            )
        except (KeyError, TypeError, ValueError):
            continue  # cloudy month with no valid pixels
    return series
