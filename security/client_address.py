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


def normalize_client_address(host: str | None) -> str:
    """
    Rate-limit key for a connection address.

    IPv4 stays as is, IPv4-mapped IPv6 becomes the IPv4 address, other IPv6
    becomes its /64 network. Missing or unparsable values share one key.
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
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
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
