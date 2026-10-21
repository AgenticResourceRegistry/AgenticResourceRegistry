# SPDX-FileCopyrightText: 2026 Yash Gadgil <yashgadgil08@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trusted proxy middleware (SEC-003).

Replaces Uvicorn's --proxy-headers flag with app-layer proxy handling
that uses the same trust config (security.trusted_proxy_ips) as the
rate limiter.

When the TCP peer is a trusted proxy this middleware:
  1. Resolves the real client IP from X-Forwarded-For (rightmost non-trusted)
     and overwrites request.scope["client"] so that ALL downstream consumers
     (audit, download tracker, rate limiter, etc.) see the correct IP.
  2. Reads X-Forwarded-Proto and sets request.scope["scheme"].

Unlike Uvicorn's --proxy-headers (which takes the leftmost XFF entry and
is trivially spoofable), this walks the header right-to-left and skips
trusted proxy IPs, using the same algorithm as _get_real_ip() in
api/ratelimit.py.

The setting supports both plain IPs and CIDR notation (e.g.
"172.16.0.0/12,10.0.0.0/8") so Docker-internal networks are matched
regardless of the container IP assigned at runtime.
"""

from __future__ import annotations

from starlette.requests import Request

import services.dynamic_settings as ds
from services.shared.ip_utils import is_trusted, parse_trusted


class TrustedProxyMiddleware:
    """Resolve real client IP and scheme from proxy headers when the TCP peer is trusted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        client_ip = request.client.host if request.client else None
        trusted_str = ds.get_sync("security.trusted_proxy_ips")

        if not trusted_str or not client_ip:
            await self.app(scope, receive, send)
            return

        exact, networks = parse_trusted(trusted_str)

        if is_trusted(client_ip, exact, networks):
            # Resolve real client IP from X-Forwarded-For (rightmost non-trusted)
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                ips = [ip.strip() for ip in forwarded.split(",")]
                for ip in reversed(ips):
                    if not is_trusted(ip, exact, networks):
                        # Overwrite scope so all downstream sees the real IP
                        request.scope["client"] = (ip, request.scope["client"][1])
                        break

            # Set scheme from X-Forwarded-Proto
            proto = request.headers.get("x-forwarded-proto")
            if proto in ("http", "https"):
                request.scope["scheme"] = proto

        await self.app(scope, receive, send)
