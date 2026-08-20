"""Un piso no es una finca: se rechaza antes de cobrar, no después.

Todo el producto está construido sobre cartografía rústica —subparcelas de cultivo, vías
pecuarias, montes de utilidad pública, NDVI—. Sobre un inmueble urbano el informe se
generaría igual, sin superficie, sin subparcelas y sin decir nada útil, y el comprador ya
habría pagado. El corte va en el origen para que la parcela ni siquiera llegue a cachearse.
"""

from unittest.mock import patch

import pytest

from app.datasources import catastro
from app.datasources.exceptions import ParcelNotRustic

RESPUESTA = """<?xml version="1.0" encoding="UTF-8"?>
<consulta_dnp><bico><bi>
  <idbi><cn>{clase}</cn></idbi>
  <dt><np>LEÓN</np><nm>SANTA COLOMBA DE SOMOZA</nm></dt>
  <debi><luso>{uso}</luso><ssp>18430</ssp></debi>
</bi></bico></consulta_dnp>"""


async def responder(clase: str, uso: str):
    import xml.etree.ElementTree as ET

    return ET.fromstring(RESPUESTA.format(clase=clase, uso=uso))


@pytest.mark.asyncio
async def test_an_urban_reference_is_refused_with_its_use_named():
    with patch.object(catastro, "_get_xml", lambda *a, **k: responder("UR", "Residencial")):
        with pytest.raises(ParcelNotRustic) as exc:
            await catastro.fetch_cadastral_data("9872023VH5797S0001WX")

    # El mensaje llega al cliente: tiene que decir qué pasa y por qué, no un código.
    assert "urbano" in str(exc.value)
    assert "Residencial" in str(exc.value)
    assert "solo cubre fincas rústicas" in str(exc.value)


@pytest.mark.asyncio
async def test_a_rustic_reference_passes():
    with patch.object(catastro, "_get_xml", lambda *a, **k: responder("RU", "Agrario")):
        data = await catastro.fetch_cadastral_data("24155A11600027")

    assert data.use == "Agrario"
    assert data.municipality == "SANTA COLOMBA DE SOMOZA"
