# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import enum
import hashlib
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text, and_
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class UserRole(str, enum.Enum):
    super_admin = "super_admin"
    admin = "admin"
    reviewer = "reviewer"
    user = "user"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email_trgm", "email", postgresql_using="gin", postgresql_ops={"email": "gin_trgm_ops"}),
        Index("ix_users_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index(
            "ix_users_username_trgm",
            "username",
            postgresql_using="gin",
            postgresql_ops={"username": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    auth_provider: Mapped[str] = mapped_column(String(50), default="local", server_default="local")
    sso_subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # Set when an admin deletes the account. The row stays as an empty shell so
    # everything that references this ID (authored listings, telemetry, reports)
    # keeps resolving; identifying fields are scrubbed and it can never log in.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("is_demo", False)
        super().__init__(**kwargs)

    def set_password(self, password: str) -> None:
        salt = os.urandom(16)
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        self.password_hash = f"{salt.hex()}${key.hex()}"

    def verify_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        try:
            salt_hex, key_hex = self.password_hash.split("$")
            salt = bytes.fromhex(salt_hex)
            key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
            return key.hex() == key_hex
        except (ValueError, TypeError):
            return False


DELETED_USER_NAME = "Deleted user"
DELETED_AUTH_PROVIDER = "deleted"


def is_deleted_account(user: object) -> bool:
    """True for a deleted-account shell.

    Checks the scrubbed ``auth_provider`` as well as ``deleted_at``, so the
    lockout does not depend on the marker column alone.
    """
    # Real rows hold a datetime or None; anything else (a test double) is not a marker.
    return isinstance(getattr(user, "deleted_at", None), datetime) or (
        getattr(user, "auth_provider", None) == DELETED_AUTH_PROVIDER
    )


def live_users():
    """WHERE clause excluding deleted-account shells from lookups and listings."""
    return and_(User.deleted_at.is_(None), User.auth_provider != DELETED_AUTH_PROVIDER)
