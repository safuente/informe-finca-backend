"""Configurado y fallando no es lo mismo que no configurado.

Sin credenciales, el informe sale sin NDVI y eso es correcto: nunca se prometió. Con
credenciales que fallan, estamos vendiendo informes sin una sección que sí anunciamos, y
como el informe degrada con elegancia nadie se enteraría. Ese silencio es el fallo.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from shapely.geometry import Polygon

from app.core.config import settings
from app.datasources import copernicus

PARCELA = Polygon([(-6.249, 42.4508), (-6.2475, 42.4509), (-6.2474, 42.4517), (-6.2489, 42.4516)])


def cliente_que_falla(error: Exception):
    """http_client es un context manager asíncrono; el fallo debe salir del `post`."""

    class Cliente:
        async def post(self, *args, **kwargs):
            raise error

    @asynccontextmanager
    async def fabrica(*args, **kwargs):
        yield Cliente()

    return fabrica


@pytest.fixture(autouse=True)
def _reset_alert():
    copernicus._alerted = False
    yield
    copernicus._alerted = False


async def test_without_credentials_it_stays_quiet():
    avisos = []
    with (
        patch.object(settings, "cdse_client_id", ""),
        patch.object(settings, "cdse_client_secret", ""),
        patch("app.core.mailer.send_email", lambda *a, **k: avisos.append(a)),
    ):
        assert await copernicus.fetch_ndvi_series(PARCELA) == []
    assert avisos == []


async def test_a_broken_credential_reaches_a_human():
    avisos = []

    with (
        patch.object(settings, "cdse_client_id", "id"),
        patch.object(settings, "cdse_client_secret", "secreto"),
        patch(
            "app.datasources.copernicus.http_client",
            cliente_que_falla(RuntimeError("401 Unauthorized")),
        ),
        patch("app.core.mailer.send_email", lambda to, subj, body, **k: avisos.append(subj)),
    ):
        assert await copernicus.fetch_ndvi_series(PARCELA) == []

    assert len(avisos) == 1
    assert "Copernicus" in avisos[0]


async def test_it_does_not_flood_the_inbox():
    """Si CDSE está caído, cada informe fallaría igual: un aviso basta."""
    avisos = []

    with (
        patch.object(settings, "cdse_client_id", "id"),
        patch.object(settings, "cdse_client_secret", "secreto"),
        patch("app.datasources.copernicus.http_client", cliente_que_falla(RuntimeError("503"))),
        patch("app.core.mailer.send_email", lambda to, subj, body, **k: avisos.append(subj)),
    ):
        await copernicus.fetch_ndvi_series(PARCELA)
        await copernicus.fetch_ndvi_series(PARCELA)
        await copernicus.fetch_ndvi_series(PARCELA)

    assert len(avisos) == 1
