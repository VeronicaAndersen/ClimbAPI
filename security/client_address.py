"""
Client keys for the IP limits and the login throttle.

Based on the connection address (``request.client.host``); see
``security.rate_limit`` about running behind a reverse proxy.
"""

import ipaddress
import logging

from fastapi import Request

from security.rate_limited_warning import RateLimitedWarning

logger = logging.getLogger(__name__)

_UNKNOWN_CLIENT = "unknown"
# All clients without a usable address share one budget, so this usually
# means a proxy or socket setup is wrong. The address itself is not logged.
_warn_unknown_client = RateLimitedWarning(
    logger,
    "Client address missing or not an IP address; using the shared 'unknown' "
    "rate-limit key. Check the proxy and FORWARDED_ALLOW_IPS settings",
)
# One IPv6 customer usually gets at least a /64, so rotating addresses inside
# it must not give a fresh budget.
_IPV6_CLIENT_PREFIX = 64

# IPv6 forms that carry an IPv4 address in the low 32 bits. A client reaching
# us through them is that IPv4 client and must share its budget.
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")  # RFC 6052
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")  # RFC 4291, deprecated


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:  # ::ffff:a.b.c.d
        return address.ipv4_mapped
    if address.sixtofour is not None:  # 2002:aabb:ccdd::/48, 6to4
        return address.sixtofour
    if address in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
    # ::a.b.c.d, excluding :: and ::1, which are not IPv4-compatible addresses.
    if address in _IPV4_COMPATIBLE and int(address) > 1:
        return ipaddress.IPv4Address(int(address))
    return None


def normalize_client_address(host: str | None) -> str:
    """
    Rate-limit key for a connection address.

    IPv4 stays as is. IPv6 that embeds an IPv4 address (IPv4-mapped, 6to4
    2002::/16, NAT64 64:ff9b::/96, IPv4-compatible ::a.b.c.d) becomes that
    IPv4 address; other IPv6 becomes its /64 network. Missing or unparsable
    values share one key.
    """
    if not host:
        _warn_unknown_client()
        return _UNKNOWN_CLIENT
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        _warn_unknown_client()
        return _UNKNOWN_CLIENT
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return str(embedded)
        network = ipaddress.IPv6Network(
            (address.packed, _IPV6_CLIENT_PREFIX), strict=False
        )
        return str(network)
    return str(address)


def client_ip(request: Request) -> str:
    """
    Client key for the IP limits and the login throttle.

    Based on the connection address; see the module docstring about proxies.
    """
    host = request.client.host if request.client is not None else None
    return normalize_client_address(host)
