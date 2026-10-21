# SPDX-FileCopyrightText: 2026 Dheirav <dheirav2005@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Authorization header parsing shared by every bearer-token entry point."""

from __future__ import annotations


def bearer_token(authorization: object) -> str | None:
    """Return the credentials of a Bearer ``Authorization`` value, or None for any other scheme.

    HTTP auth schemes are case-insensitive (RFC 9110 section 11.1), so clients that send
    "bearer" or "BEARER" are as valid as "Bearer". An empty credential comes back as "",
    not None, so callers can still tell "no bearer header" from "a bad token".
    """
    if not isinstance(authorization, str):
        return None
    scheme, sep, credentials = authorization.lstrip().partition(" ")
    if not sep or scheme.lower() != "bearer":
        return None
    return credentials.strip()
