# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Shared SSRF guard for all outbound HTTP and Git clone operations.

Replaces the narrow string-match hostname blocklists that existed in
alert_evaluator.py and mcp_validator.py with a single function that
covers the full set of private, link-local, and cloud-metadata ranges
for both IPv4 and IPv6.

Usage
-----
from services.ssrf_guard import is_private_url

if is_private_url(url):
    raise ValueError("URL resolves to a private/internal address")
"""

import ipaddress
import socket
from urllib.parse import urlparse

from loguru import logger as optic

# Hostnames that must always be blocked regardless of IP resolution.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP instance metadata
        "metadata.google.internal",
        "fd00:ec2::254",  # AWS IMDSv2 IPv6
    }
)

# All private, link-local, and reserved ranges for IPv4 and IPv6.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / APIPA
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("0.0.0.0/8"),  # unspecified
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
    ipaddress.ip_network("::/128"),  # IPv6 unspecified: connects to the local host on Linux
    ipaddress.ip_network("64:ff9b:1::/48"),  # local-use NAT64
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking, often routed internally
]


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _ip_is_private(addr_str: str) -> bool:
    optic.trace("checking if {} is a private address", addr_str)
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return True  # unparseable, block it
    # IPv6 forms that carry an IPv4 address are judged by that address: IPv4-mapped (::ffff:a.b.c.d),
    # 6to4 (2002::/16) and well-known NAT64 (64:ff9b::/96, what DNS64 returns on IPv6-only networks).
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        elif addr.sixtofour:
            addr = addr.sixtofour
        elif addr in _NAT64:
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return any(addr in net for net in _PRIVATE_NETWORKS)


def is_private_url(url: str) -> bool:
    """Return True if *url* resolves to a private or internal host.

    Blocks:
    - All RFC-1918 and loopback ranges
    - CGNAT (100.64/10), link-local, multicast, reserved
    - IPv6 ULA, link-local, loopback, multicast
    - IPv4-mapped IPv6 addresses
    - Cloud metadata endpoints (169.254.169.254 etc.)

    DNS failures are treated as private (fail closed).
    """
    optic.trace("SSRF check for URL: {}", url)
    if not url:
        return True
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    hostname = parsed.hostname
    if not hostname:
        return True

    hostname_lower = hostname.lower().strip("[]")  # strip IPv6 brackets
    if hostname_lower in _BLOCKED_HOSTNAMES:
        return True

    # Direct IP literal: check without DNS
    try:
        ipaddress.ip_address(hostname_lower)
        return _ip_is_private(hostname_lower)
    except ValueError:
        pass  # not an IP literal, fall through to DNS

    # Hostname: resolve and check every returned address
    try:
        results = socket.getaddrinfo(hostname_lower, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not results:
            return True
        return any(_ip_is_private(r[4][0]) for r in results)
    except (socket.gaierror, OSError):
        return True  # DNS failure, fail closed


def resolve_public_address(hostname: str) -> str | None:
    """Resolve *hostname* once and return an address to connect to, or None if any address is private.

    Connect to the returned address instead of resolving the name again, so DNS
    rebinding cannot swap in a private address between the check and the
    connection. DNS failures return None (fail closed).
    """
    host = (hostname or "").lower().strip("[]")
    if not host or host in _BLOCKED_HOSTNAMES:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None if _ip_is_private(host) else host
    try:
        results = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return None
    addresses = [r[4][0] for r in results]
    if not addresses or any(_ip_is_private(a) for a in addresses):
        return None
    return addresses[0]
