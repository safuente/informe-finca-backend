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

from shapely.geometry.base import BaseGeometry

from app.core.config import settings
from app.core.logger import get_logger
from app.datasources.http import http_client

logger = get_logger(__name__)

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
STATISTICS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

EVALSCRIPT_NDVI = """//VERSION=3
function setup() { return { input: [{bands:["B04","B08","dataMask"]}],
  output: [{id:"ndvi", bands:1}, {id:"dataMask", bands:1}] }; }
function evaluatePixel(s) {
  return { ndvi: [(s.B08-s.B04)/(s.B08+s.B04)], dataMask: [s.dataMask] }; }
"""


@dataclass(slots=True)
class NdviPoint:
    month: str  # YYYY-MM
    value: float


async def fetch_ndvi_series(geometry: BaseGeometry, years: int | None = None) -> list[NdviPoint]:
    """Monthly mean NDVI over the parcel. Empty list when CDSE is not configured."""
    if not settings.ndvi_enabled:
        logger.info("NDVI skipped: CDSE credentials not configured")
        return []

    years = years or settings.ndvi_years
    today = date.today()

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
                        "resx": 10,
                        "resy": 10,
                    },
                    "calculations": {"ndvi": {}},
                },
            )
            stats_response.raise_for_status()
            payload = stats_response.json()
        except Exception:  # noqa: BLE001 — NDVI is a nice-to-have, never a blocker
            logger.warning("Copernicus NDVI unavailable", exc_info=True)
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
