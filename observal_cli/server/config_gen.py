# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generate service configuration files for embedded mode.

Creates minimal, locally-tuned configs for PostgreSQL, ClickHouse, and Redis
that bind to 127.0.0.1 on non-standard ports.
"""

from __future__ import annotations

import secrets
from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from observal_cli.server.constants import (
    CLICKHOUSE_HTTP_PORT,
    CLICKHOUSE_TCP_PORT,
    CONFIG_DIR,
    LOG_DIR,
    POSTGRES_PORT,
    REDIS_PORT,
    RUN_DIR,
    get_data_paths,
)


def generate_secret(length: int = 32) -> str:
    """Generate a URL-safe random secret."""
    return secrets.token_urlsafe(length)


def ensure_dirs() -> None:
    """Create all required directories."""
    data_paths = get_data_paths()
    for d in [CONFIG_DIR, LOG_DIR, RUN_DIR, *data_paths.values()]:
        d.mkdir(parents=True, exist_ok=True)


def generate_postgres_conf() -> Path:
    """Generate postgresql.conf for embedded mode.

    Returns path to the generated config file.
    """
    conf_path = CONFIG_DIR / "postgresql.conf"

    content = dedent(f"""\
        # Observal embedded PostgreSQL configuration
        # Auto-generated - do not edit manually

        listen_addresses = '127.0.0.1'
        port = {POSTGRES_PORT}
        max_connections = 30
        shared_buffers = 128MB
        work_mem = 4MB
        maintenance_work_mem = 64MB
        effective_cache_size = 256MB

        # WAL
        wal_level = minimal
        max_wal_senders = 0
        fsync = on
        synchronous_commit = off

        # Logging
        log_destination = 'stderr'
        logging_collector = off
        log_min_messages = warning

        # Connection
        unix_socket_directories = '{RUN_DIR}'

        # Data
        dynamic_shared_memory_type = posix
    """)

    conf_path.write_text(content)
    return conf_path


def generate_pg_hba_conf() -> Path:
    """Generate pg_hba.conf allowing local TCP connections with password."""
    hba_path = get_data_paths()["postgres"] / "pg_hba.conf"

    content = dedent("""\
        # Observal embedded PostgreSQL HBA
        # Auto-generated - do not edit manually
        # Trust-based auth is safe here: server binds to 127.0.0.1 only.

        # TYPE  DATABASE  USER       ADDRESS        METHOD
        local   all       all                       trust
        host    all       all        127.0.0.1/32   trust
        host    all       all        ::1/128        trust
    """)

    hba_path.write_text(content)
    return hba_path


def generate_clickhouse_config() -> Path:
    """Generate ClickHouse config for embedded mode.

    Returns path to the generated config file.
    """
    conf_path = CONFIG_DIR / "clickhouse-config.xml"
    data_path = get_data_paths()["clickhouse"]
    log_path = LOG_DIR / "clickhouse.log"
    error_log_path = LOG_DIR / "clickhouse-error.log"

    content = dedent(f"""\
        <?xml version="1.0"?>
        <clickhouse>
            <logger>
                <level>warning</level>
                <log>{log_path}</log>
                <errorlog>{error_log_path}</errorlog>
                <size>100M</size>
                <count>3</count>
            </logger>

            <http_port>{CLICKHOUSE_HTTP_PORT}</http_port>
            <tcp_port>{CLICKHOUSE_TCP_PORT}</tcp_port>
            <listen_host>127.0.0.1</listen_host>

            <path>{data_path}/</path>
            <tmp_path>{data_path}/tmp/</tmp_path>
            <user_files_path>{data_path}/user_files/</user_files_path>
            <format_schema_path>{data_path}/format_schemas/</format_schema_path>

            <max_server_memory_usage_ratio>0.5</max_server_memory_usage_ratio>
            <max_concurrent_queries>20</max_concurrent_queries>

            <send_crash_reports>
                <enabled>false</enabled>
                <send_logical_errors>false</send_logical_errors>
            </send_crash_reports>
            <remote_url_allow_hosts></remote_url_allow_hosts>

            <mark_cache_size>5368709120</mark_cache_size>

            <users>
                <default>
                    <password></password>
                    <networks>
                        <ip>127.0.0.1</ip>
                    </networks>
                    <profile>default</profile>
                    <quota>default</quota>
                    <access_management>1</access_management>
                </default>
            </users>

            <profiles>
                <default>
                    <max_memory_usage>2000000000</max_memory_usage>
                    <load_balancing>random</load_balancing>
                </default>
            </profiles>

            <quotas>
                <default>
                    <interval>
                        <duration>3600</duration>
                        <queries>0</queries>
                        <errors>0</errors>
                        <result_rows>0</result_rows>
                        <read_rows>0</read_rows>
                        <execution_time>0</execution_time>
                    </interval>
                </default>
            </quotas>
        </clickhouse>
    """)

    conf_path.write_text(content)
    return conf_path


def generate_redis_conf() -> Path:
    """Generate Redis config for embedded mode.

    Returns path to the generated config file.
    """
    conf_path = CONFIG_DIR / "redis.conf"
    data_path = get_data_paths()["redis"]
    log_path = LOG_DIR / "redis.log"
    pid_path = RUN_DIR / "redis.pid"

    content = dedent(f"""\
        # Observal embedded Redis configuration
        # Auto-generated - do not edit manually

        bind 127.0.0.1
        port {REDIS_PORT}
        daemonize no
        pidfile {pid_path}

        # Persistence
        dir {data_path}
        save 900 1
        save 300 10
        save 60 10000
        dbfilename dump.rdb

        # Memory
        maxmemory 128mb
        maxmemory-policy allkeys-lru

        # Logging
        logfile {log_path}
        loglevel warning

        # Performance
        tcp-backlog 128
        timeout 300
        tcp-keepalive 60
    """)

    conf_path.write_text(content)
    return conf_path


def generate_all_configs() -> dict[str, Path]:
    """Generate all service configurations.

    Returns dict mapping service name to config file path.
    """
    ensure_dirs()
    return {
        "postgres": generate_postgres_conf(),
        "clickhouse": generate_clickhouse_config(),
        "redis": generate_redis_conf(),
    }
