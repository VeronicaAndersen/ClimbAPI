from httpx import ASGITransport, AsyncClient

from main import app


async def test_cors_exposes_retry_after_to_allowed_origin():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs", headers={"Origin": "http://localhost:5173"})

    exposed = response.headers.get("access-control-expose-headers", "")
    assert "retry-after" in exposed.lower()
