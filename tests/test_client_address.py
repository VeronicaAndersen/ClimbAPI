"""Unit tests for client address normalization in security/rate_limit.py"""

from types import SimpleNamespace

import pytest

from security.rate_limit import client_ip, normalize_client_address


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
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


def test_ipv6_addresses_in_different_64s_differ():
    assert normalize_client_address("2001:db8:0:1::1") != normalize_client_address(
        "2001:db8:0:2::1"
    )


def test_client_ip_without_client():
    assert client_ip(SimpleNamespace(client=None)) == "unknown"


def test_client_ip_uses_connection_host():
    request = SimpleNamespace(client=SimpleNamespace(host="2001:db8::5", port=1))
    assert client_ip(request) == "2001:db8::/64"
