# SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Boot-time configuration: env vars required to start the server.

All runtime-tunable settings have been moved to the Settings page
(stored in runtime settings, accessed via services.dynamic_settings).

Only infrastructure, crypto, and auth middleware vars remain here.
"""

import os
from typing import Literal

from dotenv import dotenv_values
from pydantic_settings import BaseSettings

from observal_shared.secrets import resolve_secret


class Settings(BaseSettings):
    # Infrastructure
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/observal"
    CLICKHOUSE_URL: str = "clickhouse://localhost:8123/observal"
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_SOCKET_TIMEOUT: float = 2.0
    REDIS_MAX_CONNECTIONS: int = 200

    # Crypto
    SECRET_KEY: str = "change-me-to-a-random-string"
    OLD_SECRET_KEY: str | None = None

    # JWT key management (boot-time, keys loaded once at startup)
    JWT_SIGNING_ALGORITHM: Literal["ES256", "RS256"] = "ES256"
    JWT_KEY_DIR: str = "~/.observal/keys"
    JWT_KEY_PASSWORD: str | None = None

    # Outbound Git authentication
    GIT_CLONE_TOKEN: str | None = None

    # Vendor usage-ping destination. The production default is intentionally
    # fixed; overrides exist for development and isolated collector deployments.
    USAGE_PING_URL: str = "https://usage.observal.io/api/v1/usage-pings"
    USAGE_PING_DEPLOYMENT_TYPE: Literal["self-managed", "cloud", "development"] | None = None

    # Connection pool sizing (boot-time, pool created once at startup)
    DB_POOL_SIZE: int = 30
    DB_MAX_OVERFLOW: int = 50
    CLICKHOUSE_MAX_CONNECTIONS: int = 100
    CLICKHOUSE_MAX_KEEPALIVE: int = 100
    CLICKHOUSE_TIMEOUT: float = 10.0

    # Logging (boot-time, configured before event loop starts)
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    SKIP_DDL_ON_STARTUP: bool = False

    # Demo accounts (boot-time, needed to bootstrap first login)
    SEED_DEMO_ACCOUNTS: bool = True
    DEMO_SUPER_ADMIN_EMAIL: str | None = None
    DEMO_SUPER_ADMIN_PASSWORD: str | None = None
    DEMO_ADMIN_EMAIL: str | None = None
    DEMO_ADMIN_PASSWORD: str | None = None
    DEMO_REVIEWER_EMAIL: str | None = None
    DEMO_REVIEWER_PASSWORD: str | None = None
    DEMO_USER_EMAIL: str | None = None
    DEMO_USER_PASSWORD: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}


_SECRET_FIELDS = (
    "DATABASE_URL",
    "CLICKHOUSE_URL",
    "REDIS_URL",
    "SECRET_KEY",
    "OLD_SECRET_KEY",
    "JWT_KEY_PASSWORD",
    "GIT_CLONE_TOKEN",
    "DEMO_SUPER_ADMIN_PASSWORD",
    "DEMO_ADMIN_PASSWORD",
    "DEMO_REVIEWER_PASSWORD",
    "DEMO_USER_PASSWORD",
)


def _secret_overrides() -> dict[str, str]:
    env_file = {key: value for key, value in dotenv_values(".env").items() if value is not None}
    values = {**env_file, **os.environ}
    resolved = {}
    for name in _SECRET_FIELDS:
        value = resolve_secret(name, values)
        if value is not None:
            resolved[name] = value
    return resolved


settings = Settings(**_secret_overrides())
