"""422 responses never echo request input."""

import pytest

BASE = "/api/v1"
PASSWORD = "p" * 1025


@pytest.mark.parametrize(
    ("path", "body"),
    [
        # Password missing: the error's input is the whole body.
        ("/auth/login", '{"username": "a\\ud800b"}'),
        # Username too long: the error's input is the surrogate string itself.
        ("/auth/password-reset/request", '{"username": "a\\ud800b' + "x" * 200 + '"}'),
    ],
)
async def test_lone_surrogate_gives_clean_422(client, path, body):
    """
    Regression: the default 422 echoed the input, which held a lone
    surrogate, so the response could not be encoded: 500 plus a traceback.
    """
    resp = await client.post(
        f"{BASE}{path}", content=body, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail
    for error in detail:
        assert set(error) == {"loc", "msg", "type"}


async def test_422_does_not_echo_password(client):
    resp = await client.post(
        f"{BASE}/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert resp.status_code == 422
    assert PASSWORD not in resp.text
    (error,) = resp.json()["detail"]
    assert error["loc"] == ["body", "password"]
    assert error["type"] == "string_too_long"
    assert error["msg"]


async def test_422_shape_is_loc_msg_type(client):
    resp = await client.post(f"{BASE}/auth/login", json={})
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert {tuple(e["loc"]) for e in errors} == {("body", "username"), ("body", "password")}
    assert all(set(e) == {"loc", "msg", "type"} for e in errors)
    assert all(e["type"] == "missing" for e in errors)
