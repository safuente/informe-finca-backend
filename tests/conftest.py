import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """App client without lifespan: no Redis, no Postgres.

    The tests that matter here (findings wording, webhook handling) are deliberately
    reachable without infrastructure — see tests/README.md for the ones that are not.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
