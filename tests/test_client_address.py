"""Unit tests for security/client_address.py"""

import logging
from types import SimpleNamespace

import pytest

from security import client_address
from security.client_address import client_ip, ipv6_site_key, normalize_client_address
from security.rate_limited_warning import RateLimitedWarning


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("2002:cb00:7107::1", "203.0.113.7"),
        ("2002:cb00:7107:42:aaaa::5", "203.0.113.7"),
        ("64:ff9b::cb00:7107", "203.0.113.7"),
        ("64:ff9b::203.0.113.7", "203.0.113.7"),
        ("::203.0.113.7", "203.0.113.7"),
        ("::", "::/64"),
        ("64:ff9b:1::1", "64:ff9b:1::/64"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("::1", "::/64"),
        ("fe80::1%eth0", "fe80::/64"),
        ("not-an-ip", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalize_client_address(host, expected):
    assert normalize_client_address(host) == expected


def test_ipv6_addresses_in_same_64_share_a_key():
    """Regression: rotating within one /64 gave each address a fresh budget."""
    keys = {normalize_client_address(f"2001:db8:0:1::{i:x}") for i in range(1, 500)}
    assert keys == {"2001:db8:0:1::/64"}


@pytest.mark.parametrize(
    "host",
    ["203.0.113.7", "::ffff:203.0.113.7", "2002:cb00:7107::1", "64:ff9b::cb00:7107", "::203.0.113.7"],
)
def test_embedded_ipv4_shares_the_ipv4_key(host):
    """Regression: 6to4, NAT64 and IPv4-compatible forms got their own budget."""
    assert normalize_client_address(host) == "203.0.113.7"


def test_ipv6_addresses_in_different_64s_differ():
    assert normalize_client_address("2001:db8:0:1::1") != normalize_client_address(
        "2001:db8:0:2::1"
    )


def test_client_ip_without_client():
    assert client_ip(SimpleNamespace(client=None)) == "unknown"


def test_client_ip_uses_connection_host():
    request = SimpleNamespace(client=SimpleNamespace(host="2001:db8::5", port=1))
    assert client_ip(request) == "2001:db8::/64"


@pytest.fixture()
def unknown_client_clock(monkeypatch):
    clock = FakeClock()
    warning = RateLimitedWarning(
        client_address.logger,
        "Client address missing or not an IP address",
        interval_seconds=60.0,
        clock=clock,
    )
    monkeypatch.setattr(client_address, "_warn_unknown_client", warning)
    return clock


def test_unparsable_address_logs_rate_limited_warning(caplog, unknown_client_clock):
    with caplog.at_level(logging.WARNING, logger="security.client_address"):
        for _ in range(50):
            normalize_client_address("proxy-sock")
        normalize_client_address(None)
        assert len(caplog.records) == 1
        unknown_client_clock.now += 60
        normalize_client_address("still-bad")
    assert len(caplog.records) == 2
    assert "51 time(s)" in caplog.records[1].getMessage()
    for record in caplog.records:
        assert "proxy-sock" not in record.getMessage()
        assert "still-bad" not in record.getMessage()


def test_valid_addresses_do_not_warn(caplog, unknown_client_clock):
    with caplog.at_level(logging.WARNING, logger="security.client_address"):
        normalize_client_address("203.0.113.7")
        normalize_client_address("2001:db8::1")
    assert caplog.records == []


@pytest.mark.parametrize(
    ("client_key", "expected"),
    [
        ("2001:db8:aa:1::/64", "2001:db8:aa::/48"),
        ("2001:db8:aa:ffff::/64", "2001:db8:aa::/48"),
        ("203.0.113.7", None),
        ("unknown", None),
    ],
)
def test_ipv6_site_key(client_key, expected):
    assert ipv6_site_key(client_key) == expected



# ---------------------------------------------------------------------------
# Vercel: x-real-ip instead of the connection address (TRUST_VERCEL_HEADERS)
# ---------------------------------------------------------------------------


def _request(headers: list[tuple[str, str]], client=("10.9.8.7", 4321)):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": client,
    }
    return Request(scope)


@pytest.fixture()
def vercel_headers_on(monkeypatch):
    monkeypatch.setattr(client_address, "_trust_vercel_headers", True)


def test_header_ignored_when_setting_is_off():
    request = _request([("x-real-ip", "203.0.113.50")])
    assert client_ip(request) == "10.9.8.7"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("203.0.113.50", "203.0.113.50"),
        ("  203.0.113.50  ", "203.0.113.50"),
        ("2001:db8:1:2::abcd", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.50", "203.0.113.50"),
        ("2002:cb00:7107::1", "203.0.113.7"),
    ],
)
def test_header_used_when_setting_is_on(vercel_headers_on, header, expected):
    request = _request([("x-real-ip", header)])
    assert client_ip(request) == expected


def test_connection_address_ignored_when_setting_is_on(vercel_headers_on):
    request = _request([("x-real-ip", "203.0.113.50")], client=("198.51.100.1", 1))
    assert client_ip(request) == "203.0.113.50"


def test_site_key_applies_to_header_address(vercel_headers_on):
    key = client_ip(_request([("x-real-ip", "2001:db8:aa:bb::1")]))
    assert ipv6_site_key(key) == "2001:db8:aa::/48"


@pytest.mark.parametrize(
    "headers",
    [
        [("x-real-ip", "203.0.113.50, 198.51.100.1")],  # a list
        [("x-real-ip", "203.0.113.50:8080")],  # a port
        [("x-real-ip", "[2001:db8::1]:443")],
        [("x-real-ip", "not-an-ip")],
        [("x-real-ip", "")],
        [],  # missing
        [("x-real-ip", "203.0.113.50"), ("x-real-ip", "198.51.100.1")],  # twice
        [("x-forwarded-for", "203.0.113.50")],  # other headers are not read
    ],
)
def test_bad_or_missing_header_gives_unknown_and_warns(
    vercel_headers_on, unknown_client_clock, caplog, headers
):
    """Never the connection address: on Vercel that is the platform's."""
    with caplog.at_level(logging.WARNING, logger="security.client_address"):
        assert client_ip(_request(headers)) == "unknown"
    assert len(caplog.records) == 1
    assert "Client address missing or not an IP address" in caplog.records[0].getMessage()


def test_configure_switches_the_source(monkeypatch):
    monkeypatch.setattr(client_address, "_trust_vercel_headers", False)
    request = _request([("x-real-ip", "203.0.113.50")])
    client_address.configure(trust_vercel_headers=True)
    assert client_ip(request) == "203.0.113.50"
    client_address.configure(trust_vercel_headers=False)
    assert client_ip(request) == "10.9.8.7"


@pytest.mark.parametrize(
    ("vercel", "vercel_env"),
    [
        (None, None),
        ("0", "production"),
        ("true", "production"),
        ("", "preview"),
        # What `vercel env pull` writes into a local env file:
        ("1", "development"),
        ("1", None),
        ("1", ""),
        ("1", "Production"),
    ],
)
def test_trusting_vercel_headers_off_a_vercel_deployment_fails_startup(
    monkeypatch, vercel, vercel_env
):
    from security.rate_limit import load_rate_limit_settings

    monkeypatch.setenv("TRUST_VERCEL_HEADERS", "true")
    for name, value in (("VERCEL", vercel), ("VERCEL_ENV", vercel_env)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="only allowed on a Vercel deployment"):
        load_rate_limit_settings()


@pytest.mark.parametrize("vercel_env", ["production", "preview"])
def test_trusting_vercel_headers_on_a_vercel_deployment_is_accepted(monkeypatch, vercel_env):
    from security.rate_limit import load_rate_limit_settings

    monkeypatch.setenv("TRUST_VERCEL_HEADERS", "true")
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", vercel_env)
    assert load_rate_limit_settings().TRUST_VERCEL_HEADERS is True


def test_trust_vercel_headers_defaults_off(monkeypatch):
    from security.rate_limit import load_rate_limit_settings

    monkeypatch.delenv("TRUST_VERCEL_HEADERS", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert load_rate_limit_settings().TRUST_VERCEL_HEADERS is False
