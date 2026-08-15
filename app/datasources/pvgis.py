"""PVGIS (JRC, European Commission): photovoltaic yield for the parcel centroid.

Returns kWh per kWp and year for a fixed, free-standing installation — the figure that
tells a buyer whether a solar developer would ever call. Attribution: PVGIS © European Union.
"""

from dataclasses import dataclass

from app.core.logger import get_logger
from app.datasources.http import http_client

logger = get_logger(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc"


@dataclass(slots=True)
class SolarPotential:
    kwh_per_kwp_year: float
    optimal_slope_deg: float | None = None
    source: str = "PVGIS © Unión Europea, 2001-2024"


async def fetch_solar_potential(lat: float, lon: float) -> SolarPotential | None:
    """None when PVGIS is unavailable: the report says so instead of inventing a figure."""
    async with http_client() as client:
        try:
            response = await client.get(
                PVGIS_URL,
                params={
                    "lat": lat,
                    "lon": lon,
                    "peakpower": 1,
                    "loss": 14,
                    "optimalangles": 1,
                    "outputformat": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            logger.warning("PVGIS unavailable for %s, %s", lat, lon, exc_info=True)
            return None

    try:
        fixed = payload["outputs"]["totals"]["fixed"]
        mounting = payload["inputs"]["mounting_system"]["fixed"]
        return SolarPotential(
            kwh_per_kwp_year=float(fixed["E_y"]),
            optimal_slope_deg=float(mounting["slope"]["value"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("Unexpected PVGIS payload shape", exc_info=True)
        return None
