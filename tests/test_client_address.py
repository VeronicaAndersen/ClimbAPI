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
