"""
Client keys for the IP limits and the login throttle.

Based on the connection address (``request.client.host``); see
``security.rate_limit`` about running behind a reverse proxy. On Vercel
(TRUST_VERCEL_HEADERS), the ``x-real-ip`` header that Vercel's edge sets is
used instead, and the connection address is ignored.
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
# Set once at startup by security.rate_limit from TRUST_VERCEL_HEADERS (kept
# here as a plain flag so this module does not import the settings module,
# which imports this one).
_trust_vercel_headers = False
_VERCEL_CLIENT_IP_HEADER = "x-real-ip"


def configure(*, trust_vercel_headers: bool) -> None:
    """Choose where client_ip() takes the client address from."""
    global _trust_vercel_headers
    _trust_vercel_headers = trust_vercel_headers


# One IPv6 customer usually gets at least a /64, so rotating addresses inside
# it must not give a fresh budget.
_IPV6_CLIENT_PREFIX = 64
# Coarser grouping for the login throttle: a site or small business commonly
# gets a /48, i.e. 65,536 /64s.
_IPV6_SITE_PREFIX = 48

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


def _vercel_client_address(request: Request) -> str | None:
    """
    The client address Vercel's edge put in ``x-real-ip``, or None.

    It must be exactly one header holding one bare IP address. A list, a
    port, anything unparsable or a missing header gives None; the
    connection address is never used instead, since on Vercel it is the
    platform's, shared by all clients.
    """
    values = request.headers.getlist(_VERCEL_CLIENT_IP_HEADER)
    if len(values) != 1:
        return None
    value = values[0].strip()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def client_ip(request: Request) -> str:
    """
    Client key for the IP limits and the login throttle.

    Based on the connection address, or on Vercel on the ``x-real-ip``
    header; see the module docstring about proxies.
    """
    if _trust_vercel_headers:
        return normalize_client_address(_vercel_client_address(request))
    host = request.client.host if request.client is not None else None
    return normalize_client_address(host)


def ipv6_site_key(client_key: str) -> str | None:
    """
    The /48 network around an IPv6 client key, or None for IPv4 and unknown.

    Takes the output of ``normalize_client_address``; IPv6 forms that embed
    IPv4 were already mapped to IPv4 there and so get None.
    """
    try:
        network = ipaddress.ip_network(client_key)
    except ValueError:
        return None
    if not isinstance(network, ipaddress.IPv6Network):
        return None
    return str(network.supernet(new_prefix=_IPV6_SITE_PREFIX))
