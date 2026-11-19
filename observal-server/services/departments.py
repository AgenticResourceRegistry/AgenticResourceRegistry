# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Department membership for dashboards: SCIM groups first, then each user's department field."""

import uuid  # noqa: TC003

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.user_group import UserGroup


async def resolve_user_departments(db: AsyncSession) -> dict[str, list[str]]:
    """Return a deployment-wide department to user ID mapping."""
    dept_map: dict[str, list[str]] = {}
    assigned_user_ids: set[uuid.UUID] = set()

    group_rows = (await db.execute(select(UserGroup.group_name, UserGroup.user_id))).all()
    for row in group_rows:
        dept_map.setdefault(row.group_name, []).append(str(row.user_id))
        assigned_user_ids.add(row.user_id)

    department_filter = User.department.isnot(None)
    if assigned_user_ids:
        department_filter = department_filter & User.id.notin_(assigned_user_ids)
    dept_rows = (await db.execute(select(User.id, User.department).where(department_filter))).all()
    for row in dept_rows:
        dept_map.setdefault(row.department, []).append(str(row.id))
        assigned_user_ids.add(row.id)

    all_users = (await db.execute(select(User.id))).scalars().all()
    unassigned = [str(uid) for uid in all_users if uid not in assigned_user_ids]
    if unassigned:
        dept_map["Unassigned"] = unassigned
    return dept_map
