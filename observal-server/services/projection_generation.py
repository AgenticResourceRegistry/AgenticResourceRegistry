# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi
# SPDX-License-Identifier: Apache-2.0

"""One PostgreSQL sequence for globally ordered layer and activity attempts."""

from sqlalchemy import text

from database import async_session


async def next_projection_generation() -> int:
    """Allocate a positive UInt64-compatible generation before any projection write."""
    async with async_session() as db:
        number = (await db.execute(text("SELECT nextval('projection_generation_seq')"))).scalar_one()
        if not 0 < number < 2**64:
            raise ValueError("Projection generation exceeded UInt64")
        return int(number)
