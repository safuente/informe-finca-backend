async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_lists_the_three_domains(client):
    """The scaffold is wired: parcels, reports and payments all reach the v1 router."""
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/v1/parcels/{refcat}/preview" in paths
    assert "/api/v1/reports/{token}/download" in paths
    assert "/api/v1/payments/stripe/webhook" in paths
