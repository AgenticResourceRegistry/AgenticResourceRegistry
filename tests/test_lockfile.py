# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Focused coverage for the CLI lockfile store."""

from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import os
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from observal_cli import config, lockfile

NOW = "2026-07-01T12:30:45+00:00"


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 1, 12, 30, 45, tzinfo=UTC)
        return value if tz is None else value.astimezone(tz)


@pytest.fixture(autouse=True)
def isolated_lockfile(tmp_path, monkeypatch):
    config_dir = tmp_path / ".observal"
    state = SimpleNamespace(
        path=config_dir / "lockfile.json",
        lock_path=config_dir / "lockfile.lock",
        config_dir=config_dir,
        server_url="https://registry.example.test",
    )
    monkeypatch.setattr(lockfile, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(lockfile, "LOCKFILE_PATH", state.path)
    monkeypatch.setattr(lockfile, "_LOCKFILE_LOCK", state.lock_path)
    monkeypatch.setattr(lockfile, "datetime", FrozenDateTime)
    monkeypatch.setattr(config, "load", lambda: {"server_url": state.server_url})
    return state


def registry_data(state, harnesses: dict, registry_url: str | None = None) -> dict:
    server_url = lockfile.normalize_server_url(registry_url or state.server_url)
    return {
        "lock_version": lockfile.LOCK_VERSION,
        "updated_at": NOW,
        "registries": {
            server_url: {
                "server_url": server_url,
                "harnesses": harnesses,
            }
        },
    }


def persist_registry(state, harnesses: dict, registry_url: str | None = None) -> dict:
    data = registry_data(state, harnesses, registry_url)
    lockfile.write_lockfile(data)
    return data


def raw_lockfile(state) -> dict:
    return json.loads(state.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" registry.example.test/ ", "http://registry.example.test"),
        ("HTTP://REGISTRY.EXAMPLE.TEST:80/", "http://registry.example.test"),
        ("https://REGISTRY.EXAMPLE.TEST:443/api/", "https://registry.example.test/api"),
        ("https://user:secret@Registry.Example.Test:8443/api/?q=1#part", "https://registry.example.test:8443/api"),
        ("http://registry.example.test:8080", "http://registry.example.test:8080"),
    ],
)
def test_normalize_server_url_canonicalizes_registry_aliases(value, expected):
    assert lockfile.normalize_server_url(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "http:///missing", "////"])
def test_normalize_server_url_requires_a_hostname(value):
    with pytest.raises(ValueError, match="configured server URL"):
        lockfile.normalize_server_url(value)


@pytest.mark.parametrize("value", ["https://example.test:notaport", "https://example.test:99999"])
def test_normalize_server_url_rejects_invalid_ports(value):
    with pytest.raises(ValueError):
        lockfile.normalize_server_url(value)


def test_current_registry_url_reads_and_normalizes_config(isolated_lockfile):
    isolated_lockfile.server_url = " HTTPS://Registry.Example.Test:443/root/ "
    assert lockfile.current_registry_url() == "https://registry.example.test/root"

    isolated_lockfile.server_url = ""
    assert lockfile.current_registry_url() == config.PUBLIC_SERVER_URL


def test_read_missing_lockfile_returns_fresh_schema_without_writing(isolated_lockfile):
    first = lockfile.read_lockfile()
    second = lockfile.read_lockfile()

    assert first == {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {},
    }
    assert second == first
    assert first is not second
    assert not isolated_lockfile.path.exists()


def test_read_valid_lockfile_preserves_persisted_data(isolated_lockfile):
    data = registry_data(isolated_lockfile, {"kiro": {"agents": [], "standalone": []}})
    data["updated_at"] = "2025-01-01T00:00:00+00:00"
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(json.dumps(data), encoding="utf-8")

    assert lockfile.read_lockfile() == data
    assert raw_lockfile(isolated_lockfile)["updated_at"] == "2025-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{broken", "Cannot read"),
        (b"\xff", "Cannot read"),
        ("[]", "Invalid lockfile structure"),
        (json.dumps({"lock_version": 2, "registries": []}), "Unsupported lockfile version"),
        (json.dumps({"lock_version": 2}), "Unsupported lockfile version"),
        (json.dumps({"lock_version": 99, "registries": {}}), "Unsupported lockfile version"),
    ],
)
def test_read_rejects_malformed_and_unsupported_data(isolated_lockfile, payload, message):
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_bytes(payload if isinstance(payload, bytes) else payload.encode())

    with pytest.raises(RuntimeError, match=message):
        lockfile.read_lockfile()


@pytest.mark.parametrize("payload", ["{broken", "[]", b"\xff"])
def test_atomic_update_rejects_corrupt_lockfile_without_writing(isolated_lockfile, payload):
    isolated_lockfile.path.parent.mkdir(parents=True)
    original = payload if isinstance(payload, bytes) else payload.encode()
    isolated_lockfile.path.write_bytes(original)
    with pytest.raises(RuntimeError, match=r"Cannot read|Invalid lockfile structure"):
        lockfile.update_lockfile(lambda data: data.update({"registries": {}}))
    assert isolated_lockfile.path.read_bytes() == original


def test_read_wraps_filesystem_errors(isolated_lockfile, monkeypatch):
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(json.dumps(registry_data(isolated_lockfile, {})), encoding="utf-8")
    real_read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == isolated_lockfile.path:
            raise PermissionError("read denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(RuntimeError, match="Cannot read") as exc:
        lockfile.read_lockfile()
    assert isinstance(exc.value.__cause__, PermissionError)


def test_read_reports_a_failure_between_migration_probe_and_parse(isolated_lockfile, monkeypatch):
    valid = json.dumps(registry_data(isolated_lockfile, {}))
    calls = 0
    real_read_text = Path.read_text

    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(valid, encoding="utf-8")

    def flaky_read(path, *args, **kwargs):
        nonlocal calls
        if path == isolated_lockfile.path:
            calls += 1
            if calls == 1:
                return valid
            raise OSError("media failure")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read)
    with pytest.raises(RuntimeError, match="Cannot read") as exc:
        lockfile.read_lockfile()
    assert isinstance(exc.value.__cause__, OSError)


def test_read_rejects_structure_changed_after_migration_probe(isolated_lockfile, monkeypatch):
    valid = json.dumps(registry_data(isolated_lockfile, {}))
    responses = iter([valid, "[]"])
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(valid, encoding="utf-8")
    real_read_text = Path.read_text

    def changed_read(path, *args, **kwargs):
        if path == isolated_lockfile.path:
            return next(responses)
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", changed_read)
    with pytest.raises(RuntimeError, match="Invalid lockfile structure"):
        lockfile.read_lockfile()


def test_v1_is_migrated_to_the_explicit_registry(isolated_lockfile):
    legacy_harnesses = {
        "kiro": {
            "agents": [{"id": "agent-1", "version": "1.2.3"}],
            "standalone": [],
        }
    }
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(
        json.dumps({"lock_version": 1, "harnesses": legacy_harnesses}),
        encoding="utf-8",
    )

    assert lockfile.migrate_lockfile_v1(" HTTPS://OLD.EXAMPLE.TEST:443/ ") is True
    assert raw_lockfile(isolated_lockfile) == {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {
            "https://old.example.test": {
                "server_url": "https://old.example.test",
                "harnesses": legacy_harnesses,
            }
        },
    }
    assert lockfile.migrate_lockfile_v1("https://unused.example.test") is False


def test_read_automatically_migrates_v1_to_configured_registry(isolated_lockfile):
    isolated_lockfile.server_url = "http://CURRENT.EXAMPLE.TEST:80/"
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text(json.dumps({"lock_version": 1}), encoding="utf-8")

    assert lockfile.read_lockfile() == {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {
            "http://current.example.test": {
                "server_url": "http://current.example.test",
                "harnesses": {},
            }
        },
    }


def test_v1_migration_noops_for_missing_or_current_files(isolated_lockfile):
    assert lockfile.migrate_lockfile_v1() is False

    persist_registry(isolated_lockfile, {})
    before = isolated_lockfile.path.read_bytes()
    assert lockfile.migrate_lockfile_v1() is False
    assert isolated_lockfile.path.read_bytes() == before


@pytest.mark.skipif(lockfile.fcntl is None, reason="flock is POSIX-only")
def test_write_is_atomic_locked_and_respects_restrictive_umask(isolated_lockfile, monkeypatch):
    data = {"registries": {}}
    operations: list[int] = []
    real_flock = lockfile.fcntl.flock

    def tracked_flock(fd, operation):
        operations.append(operation)
        return real_flock(fd, operation)

    monkeypatch.setattr(lockfile.fcntl, "flock", tracked_flock)
    old_umask = os.umask(0o077)
    try:
        lockfile.write_lockfile(data)
    finally:
        os.umask(old_umask)

    assert data == {
        "registries": {},
        "updated_at": NOW,
        "lock_version": 2,
    }
    assert raw_lockfile(isolated_lockfile) == data
    assert isolated_lockfile.path.read_text(encoding="utf-8").endswith("\n")
    assert not isolated_lockfile.path.with_suffix(".tmp").exists()
    assert operations == [lockfile.fcntl.LOCK_EX, lockfile.fcntl.LOCK_UN]
    assert stat.S_IMODE(isolated_lockfile.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(isolated_lockfile.lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(lockfile.fcntl is None, reason="flock is POSIX-only")
def test_failed_temporary_write_is_cleaned_and_releases_lock(isolated_lockfile, monkeypatch):
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text("original\n", encoding="utf-8")
    operations: list[int] = []
    real_flock = lockfile.fcntl.flock
    real_fsync = lockfile.os.fsync

    def tracked_flock(fd, operation):
        operations.append(operation)
        return real_flock(fd, operation)

    def partial_write(fd):
        # The unique temporary file was written, but durability failed before
        # replacement. No predictable .tmp filename or symlink target exists.
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("disk full")
        return real_fsync(fd)

    with monkeypatch.context() as patcher:
        patcher.setattr(lockfile.fcntl, "flock", tracked_flock)
        patcher.setattr(lockfile.os, "fsync", partial_write)
        with pytest.raises(OSError, match="disk full"):
            lockfile.write_lockfile({"registries": {}})

    assert isolated_lockfile.path.read_text(encoding="utf-8") == "original\n"
    assert not list(isolated_lockfile.path.parent.glob(".lockfile.*.tmp"))
    assert operations == [lockfile.fcntl.LOCK_EX, lockfile.fcntl.LOCK_UN]

    lockfile.write_lockfile({"registries": {}})
    assert raw_lockfile(isolated_lockfile)["lock_version"] == 2


class FakeMsvcrt:
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        assert nbytes == 1
        self.calls.append((mode, os.fstat(fd).st_size))


def test_write_locks_with_msvcrt_where_fcntl_is_missing(isolated_lockfile, monkeypatch):
    fake = FakeMsvcrt()
    monkeypatch.setattr(lockfile, "fcntl", None)
    monkeypatch.setattr(lockfile, "msvcrt", fake)

    lockfile.write_lockfile({"registries": {}})

    assert [mode for mode, _ in fake.calls] == [FakeMsvcrt.LK_LOCK, FakeMsvcrt.LK_UNLCK]
    assert raw_lockfile(isolated_lockfile)["lock_version"] == 2


def test_module_imports_without_fcntl(monkeypatch):
    # Windows has no fcntl; importing the CLI must not fail there.
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    try:
        reloaded = importlib.reload(lockfile)
        assert reloaded.fcntl is None and reloaded.msvcrt is fake
    finally:
        monkeypatch.undo()
        importlib.reload(lockfile)
    assert lockfile.fcntl is not None or sys.platform == "win32"


def test_failed_atomic_replace_keeps_original_and_removes_temporary(isolated_lockfile, monkeypatch):
    isolated_lockfile.path.parent.mkdir(parents=True)
    isolated_lockfile.path.write_text("original\n", encoding="utf-8")
    real_replace = Path.replace

    def failed_replace(path, target):
        if path.parent == isolated_lockfile.path.parent and path.name.startswith(".lockfile."):
            raise OSError("replace denied")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(OSError, match="replace denied"):
        lockfile.write_lockfile({"registries": {}})

    assert isolated_lockfile.path.read_text(encoding="utf-8") == "original\n"
    assert not list(isolated_lockfile.path.parent.glob(".lockfile.*.tmp"))


def test_write_propagates_parent_and_lock_file_failures(isolated_lockfile, monkeypatch):
    real_mkdir = Path.mkdir

    def denied_mkdir(path, *args, **kwargs):
        if path == isolated_lockfile.path.parent:
            raise PermissionError("directory denied")
        return real_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "mkdir", denied_mkdir)
        with pytest.raises(PermissionError, match="directory denied"):
            lockfile.write_lockfile({"registries": {}})
    assert not isolated_lockfile.path.exists()

    isolated_lockfile.path.parent.mkdir(parents=True)
    real_open = builtins.open

    def denied_open(path, *args, **kwargs):
        if Path(path) == isolated_lockfile.lock_path:
            raise PermissionError("lock denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied_open)
    with pytest.raises(PermissionError, match="lock denied"):
        lockfile.write_lockfile({"registries": {}})
    assert not isolated_lockfile.path.with_suffix(".tmp").exists()


@pytest.mark.skipif(lockfile.fcntl is None, reason="flock is POSIX-only")
def test_concurrent_writes_are_serialized_by_the_lock(isolated_lockfile, monkeypatch):
    first_inside_write = threading.Event()
    release_first = threading.Event()
    second_attempted_lock = threading.Event()
    second_inside_write = threading.Event()
    counter_lock = threading.Lock()
    exclusive_calls = 0
    write_calls = 0
    real_flock = lockfile.fcntl.flock
    real_write = lockfile._write_locked

    def tracked_flock(fd, operation):
        nonlocal exclusive_calls
        if operation == lockfile.fcntl.LOCK_EX:
            with counter_lock:
                exclusive_calls += 1
                if exclusive_calls == 2:
                    second_attempted_lock.set()
        return real_flock(fd, operation)

    def controlled_write(data):
        nonlocal write_calls
        with counter_lock:
            write_calls += 1
            call_number = write_calls
        if call_number == 1:
            first_inside_write.set()
            if not release_first.wait(5):
                raise TimeoutError("first writer was not released")
        else:
            second_inside_write.set()
        return real_write(data)

    monkeypatch.setattr(lockfile.fcntl, "flock", tracked_flock)
    monkeypatch.setattr(lockfile, "_write_locked", controlled_write)
    first = registry_data(isolated_lockfile, {}, "https://first.example.test")
    second = registry_data(isolated_lockfile, {}, "https://second.example.test")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(lockfile.write_lockfile, first)
        assert first_inside_write.wait(5)
        second_future = executor.submit(lockfile.write_lockfile, second)
        try:
            assert second_attempted_lock.wait(5)
            assert not second_inside_write.is_set()
        finally:
            release_first.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    assert second_inside_write.is_set()
    assert raw_lockfile(isolated_lockfile) == second
    assert not list(isolated_lockfile.path.parent.glob(".lockfile.*.tmp"))


def test_legacy_upserts_cannot_erase_verified_standalone_or_agent_receipts(isolated_lockfile):
    proof = {"target": "/tmp/verified-skill", "listing_id": "skill-1"}
    data = registry_data(
        isolated_lockfile,
        {
            "pi": {
                "agents": [
                    {
                        "id": "agent-1",
                        "version": "1.0.0",
                        "scope": "user",
                        "components": [
                            {"type": "skill", "id": "skill-1", "folder_receipt": proof},
                        ],
                    }
                ],
                "standalone": [{"type": "skill", "id": "skill-1", "scope": "user", "folder_receipt": proof}],
            },
        },
    )
    lockfile.write_lockfile(data)
    with pytest.raises(RuntimeError, match="verified skill folder"):
        lockfile.upsert_standalone(
            "pi", component_type="skill", name="skill-1", component_id="skill-1", version="2.0.0", scope="user"
        )
    with pytest.raises(RuntimeError, match="verified skill folders"):
        lockfile.upsert_agent("pi", name="agent-1", agent_id="agent-1", version="2.0.0", scope="user")
    assert (
        raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["pi"]
        == data["registries"][isolated_lockfile.server_url]["harnesses"]["pi"]
    )


def test_read_registry_section_creation_is_explicit_and_in_memory(isolated_lockfile):
    data, missing = lockfile.read_registry_lockfile()
    assert missing == {"server_url": isolated_lockfile.server_url, "harnesses": {}}
    assert data["registries"] == {}
    assert not isolated_lockfile.path.exists()

    data, created = lockfile.read_registry_lockfile(create=True)
    assert data["registries"][isolated_lockfile.server_url] is created
    assert not isolated_lockfile.path.exists()

    lockfile.write_lockfile(data)
    assert raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url] == created


def test_registry_url_aliases_share_a_section_and_distinct_servers_are_isolated(isolated_lockfile):
    isolated_lockfile.server_url = "HTTPS://REGISTRY.EXAMPLE.TEST:443/"
    lockfile.upsert_agent(
        "kiro",
        name="first",
        agent_id="agent-first",
        version="1.0.0",
        scope="user",
    )

    isolated_lockfile.server_url = "https://registry.example.test"
    lockfile.upsert_standalone(
        "kiro",
        component_type="skill",
        name="review",
        component_id="skill-1",
        version="2.0.0",
    )
    assert set(raw_lockfile(isolated_lockfile)["registries"]) == {"https://registry.example.test"}

    isolated_lockfile.server_url = "https://other.example.test/root/"
    lockfile.upsert_agent(
        "kiro",
        name="second",
        agent_id="agent-second",
        version="3.0.0",
        scope="user",
    )
    data = raw_lockfile(isolated_lockfile)
    assert set(data["registries"]) == {
        "https://registry.example.test",
        "https://other.example.test/root",
    }
    assert lockfile.get_agent_by_id("agent-first") is None
    assert lockfile.get_agent_by_id("agent-second")["version"] == "3.0.0"


def test_agent_upsert_persists_complete_schema_and_component_pins(isolated_lockfile):
    components = [
        {"type": "mcp", "name": "search", "id": "mcp-1", "version": "1.4.2"},
        {"type": "skill", "name": "review", "id": "skill-1", "version": None},
    ]
    lockfile.upsert_agent(
        "kiro",
        name="Reviewer",
        agent_id="agent-1",
        version="2.3.4",
        scope="project",
        directory="/work/repo",
        components=components,
        namespace="alice.team",
        slug="reviewer",
        local_name="alice-team-reviewer",
    )

    assert raw_lockfile(isolated_lockfile) == {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {
            isolated_lockfile.server_url: {
                "server_url": isolated_lockfile.server_url,
                "harnesses": {
                    "kiro": {
                        "agents": [
                            {
                                "name": "Reviewer",
                                "id": "agent-1",
                                "version": "2.3.4",
                                "pulled_at": NOW,
                                "scope": "project",
                                "directory": "/work/repo",
                                "components": components,
                                "namespace": "alice.team",
                                "slug": "reviewer",
                                "qualified_name": "alice.team/reviewer",
                                "local_name": "alice-team-reviewer",
                            }
                        ],
                        "standalone": [],
                    }
                },
            }
        },
    }


def test_agent_upsert_replaces_by_scope_and_directory_without_duplicates(isolated_lockfile):
    common = {"harness": "cursor", "agent_id": "agent-1"}
    lockfile.upsert_agent(
        **common,
        name="old-a",
        version="1.0.0",
        scope="project",
        directory="/repo/a",
        components=[{"type": "mcp", "id": "old"}],
        namespace="old",
        slug="old-a",
    )
    lockfile.upsert_agent(
        **common,
        name="project-b",
        version="1.1.0",
        scope="project",
        directory="/repo/b",
    )
    lockfile.upsert_agent(
        **common,
        name="new-a",
        version="2.0.0",
        scope="project",
        directory="/repo/a",
        local_name="new-a-local",
    )
    lockfile.upsert_agent(
        **common,
        name="old-user",
        version="3.0.0",
        scope="user",
        directory="/metadata/one",
    )
    lockfile.upsert_agent(
        **common,
        name="new-user",
        version=None,
        scope="user",
        directory="/metadata/two",
    )

    agents = raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["cursor"][
        "agents"
    ]
    assert len(agents) == 3
    by_scope_and_dir = {(entry["scope"], entry.get("directory")): entry for entry in agents}
    assert by_scope_and_dir[("project", "/repo/a")] == {
        "name": "new-a",
        "id": "agent-1",
        "version": "2.0.0",
        "pulled_at": NOW,
        "scope": "project",
        "directory": "/repo/a",
        "local_name": "new-a-local",
    }
    assert by_scope_and_dir[("project", "/repo/b")]["name"] == "project-b"
    assert by_scope_and_dir[("user", "/metadata/two")]["name"] == "new-user"
    assert by_scope_and_dir[("user", "/metadata/two")]["version"] is None


def test_existing_partial_harness_sections_are_completed_on_upsert(isolated_lockfile):
    persist_registry(isolated_lockfile, {"pi": {}})

    lockfile.upsert_agent("pi", name="agent", agent_id="agent-1", version="1", scope="user")
    lockfile.upsert_standalone(
        "pi",
        component_type="mcp",
        name="search",
        component_id="mcp-1",
        version="1",
    )

    section = raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["pi"]
    assert len(section["agents"]) == 1
    assert len(section["standalone"]) == 1


def test_standalone_upsert_persists_optional_metadata_and_exact_version(isolated_lockfile):
    lockfile.upsert_standalone(
        "claude-code",
        component_type="mcp",
        name="Search MCP",
        component_id="mcp-1",
        version="4.5.6",
        scope="project",
        directory="/repo",
        integrity="sha256-deadbeef",
        namespace="alice",
        slug="search",
        local_name="alice-search",
    )

    entry = raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["claude-code"][
        "standalone"
    ][0]
    assert entry == {
        "type": "mcp",
        "name": "Search MCP",
        "id": "mcp-1",
        "version": "4.5.6",
        "scope": "project",
        "installed_at": NOW,
        "directory": "/repo",
        "integrity": "sha256-deadbeef",
        "namespace": "alice",
        "slug": "search",
        "qualified_name": "alice/search",
        "local_name": "alice-search",
    }


def test_standalone_upsert_replacement_deduplication_and_type_partition(isolated_lockfile):
    values = [
        ("mcp", "old-a", "1", "project", "/repo/a"),
        ("mcp", "project-b", "2", "project", "/repo/b"),
        ("mcp", "new-a", "3", "project", "/repo/a"),
        ("mcp", "old-user", "4", "user", None),
        ("mcp", "new-user", None, "user", "/user-metadata"),
        ("skill", "same-id-skill", "5", "user", None),
    ]
    for component_type, name, version, scope, directory in values:
        lockfile.upsert_standalone(
            "pi",
            component_type=component_type,
            name=name,
            component_id="component-1",
            version=version,
            scope=scope,
            directory=directory,
        )

    entries = raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["pi"][
        "standalone"
    ]
    assert len(entries) == 4
    keyed = {(entry["type"], entry["scope"], entry.get("directory")): entry for entry in entries}
    assert keyed[("mcp", "project", "/repo/a")]["name"] == "new-a"
    assert keyed[("mcp", "project", "/repo/b")]["name"] == "project-b"
    assert keyed[("mcp", "user", "/user-metadata")]["version"] is None
    assert keyed[("skill", "user", None)]["name"] == "same-id-skill"


def test_scope_match_helpers_distinguish_project_directories_and_user_entries():
    agents = [
        {"id": "shared", "scope": "project", "directory": "/a"},
        {"id": "shared", "scope": "project", "directory": "/b"},
        {"id": "shared", "scope": "user"},
    ]
    standalone = [
        {"type": "mcp", "id": "shared", "scope": "project", "directory": "/a"},
        {"type": "mcp", "id": "shared", "scope": "user"},
        {"type": "skill", "id": "shared", "scope": "user"},
    ]

    assert lockfile._find_agent_idx(agents, "shared", "project", "/b") == 1
    assert lockfile._find_agent_idx(agents, "shared", "project", "/missing") is None
    assert lockfile._find_agent_idx(agents, "shared", "user", None) == 2
    assert lockfile._find_agent_idx(agents, "other", "user", None) is None
    assert lockfile._find_standalone_idx(standalone, "mcp", "shared", "project", "/a") == 0
    assert lockfile._find_standalone_idx(standalone, "mcp", "shared", "project", "/missing") is None
    assert lockfile._find_standalone_idx(standalone, "mcp", "shared", "user", None) == 1
    assert lockfile._find_standalone_idx(standalone, "hook", "shared", "user", None) is None


def test_remove_operations_honor_directory_and_leave_misses_unwritten(isolated_lockfile):
    for directory in ("/a", "/b"):
        lockfile.upsert_agent(
            "kiro",
            name=directory,
            agent_id="agent-1",
            version="1",
            scope="project",
            directory=directory,
        )
        lockfile.upsert_standalone(
            "kiro",
            component_type="mcp",
            name=directory,
            component_id="mcp-1",
            version="1",
            scope="project",
            directory=directory,
        )

    before = isolated_lockfile.path.read_bytes()
    assert lockfile.remove_agent("kiro", "other-agent") is False
    assert lockfile.remove_agent("kiro", "agent-1", "/missing") is False
    assert lockfile.remove_standalone("kiro", "hook", "mcp-1") is False
    assert lockfile.remove_standalone("kiro", "mcp", "mcp-1", "/missing") is False
    assert isolated_lockfile.path.read_bytes() == before

    assert lockfile.remove_agent("kiro", "agent-1", "/a") is True
    assert lockfile.remove_standalone("kiro", "mcp", "mcp-1", "/a") is True
    assert lockfile.remove_agent("kiro", "agent-1") is True
    assert lockfile.remove_standalone("kiro", "mcp", "mcp-1") is True

    section = raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["kiro"]
    assert section == {"agents": [], "standalone": []}

    before = isolated_lockfile.path.read_bytes()
    assert lockfile.remove_agent("unknown", "missing") is False
    assert lockfile.remove_standalone("unknown", "mcp", "missing") is False
    assert isolated_lockfile.path.read_bytes() == before


def test_agent_queries_resolve_paths_scopes_local_aliases_and_ambiguity(isolated_lockfile):
    project_one = {
        "name": "Shared Project",
        "local_name": "shared-project",
        "id": "project-1",
        "scope": "project",
        "directory": "/repo/one",
        "version": "1",
    }
    project_two = {
        "name": "Shared Project",
        "local_name": "shared-project",
        "id": "project-2",
        "scope": "project",
        "directory": "/repo/two",
        "version": "2",
    }
    user = {
        "name": "Shared Project",
        "local_name": "shared-user",
        "id": "user-1",
        "scope": "user",
        "version": "3",
    }
    duplicate_user = [
        {"name": "Duplicate", "id": "dupe-1", "scope": "user"},
        {"name": "Duplicate", "id": "dupe-2", "scope": "user"},
    ]
    same_directory = [
        {"name": "Same Directory", "id": "same-1", "scope": "project", "directory": "/same"},
        {"name": "Same Directory", "id": "same-2", "scope": "project", "directory": "/same"},
    ]
    cursor_agent = {"name": "Cursor", "id": "cursor-1", "scope": "user", "version": "4"}
    persist_registry(
        isolated_lockfile,
        {
            "kiro": {
                "agents": [project_one, project_two, user, *duplicate_user, *same_directory],
                "standalone": [],
            },
            "cursor": {"agents": [cursor_agent], "standalone": []},
        },
    )

    assert lockfile.get_agent_for_directory("kiro", "/repo/two") == project_two
    assert lockfile.get_agent_for_directory("kiro", "/missing") is None
    assert lockfile.get_agent_for_directory("missing", "/repo/two") is None
    assert lockfile.get_agent_by_id("project-1") == project_one
    assert lockfile.get_agent_by_id("cursor-1", harness="kiro") is None
    assert lockfile.get_agent_by_id("cursor-1", harness="cursor") == cursor_agent
    assert lockfile.get_agent_by_name("shared-project", "kiro", directory="/repo/two") == project_two
    assert lockfile.get_agent_by_name("project-1", "kiro", directory="/repo/one") == project_one
    assert lockfile.get_agent_by_name("Shared Project", "kiro") == user
    assert lockfile.get_agent_by_name("shared-user", "kiro", directory="/missing") == user
    assert lockfile.get_agent_by_name("Duplicate", "kiro") is None
    assert lockfile.get_agent_by_name("Same Directory", "kiro", directory="/same") is None
    assert lockfile.get_agent_by_name("Same Directory", "kiro") is None
    assert lockfile.get_agent_by_name("missing", "kiro") is None


def test_get_all_entries_returns_empty_without_a_configured_registry(monkeypatch, isolated_lockfile):
    monkeypatch.setattr(config, "load", lambda: {"server_url": ""})

    assert lockfile.get_all_entries() == []


def test_get_all_entries_flattens_copies_and_filters_harnesses(isolated_lockfile):
    agent = {"name": "agent", "id": "agent-1", "scope": "user"}
    standalone = {"type": "skill", "name": "skill", "id": "skill-1", "scope": "user"}
    other = {"name": "other", "id": "agent-2", "scope": "user"}
    persist_registry(
        isolated_lockfile,
        {
            "kiro": {"agents": [agent], "standalone": [standalone]},
            "cursor": {"agents": [other], "standalone": []},
        },
    )

    assert lockfile.get_all_entries("kiro") == [
        {**agent, "harness": "kiro", "entry_type": "agent"},
        {**standalone, "harness": "kiro", "entry_type": "standalone"},
    ]
    all_entries = lockfile.get_all_entries()
    assert {entry["id"] for entry in all_entries} == {"agent-1", "skill-1", "agent-2"}
    assert (
        "harness"
        not in raw_lockfile(isolated_lockfile)["registries"][isolated_lockfile.server_url]["harnesses"]["kiro"][
            "agents"
        ][0]
    )


def test_local_registry_name_handles_collisions_hosts_types_and_dotted_namespaces(isolated_lockfile):
    current = isolated_lockfile.server_url
    other = "https://other.example.test"
    data = {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {
            current: {
                "server_url": current,
                "harnesses": {
                    "cursor": {
                        "agents": [{"namespace": "agents", "slug": "search", "local_name": "search"}],
                        "standalone": [
                            {"type": "mcp", "namespace": "alice", "slug": "search", "local_name": "search"},
                            {"type": "mcp", "namespace": "holder", "slug": "other", "local_name": "carol-search"},
                            {"type": "skill", "namespace": "skill-owner", "slug": "skill-only"},
                        ],
                    }
                },
            },
            other: {
                "server_url": other,
                "harnesses": {
                    "cursor": {
                        "agents": [],
                        "standalone": [{"type": "mcp", "namespace": "alice", "slug": "remote", "local_name": "remote"}],
                    }
                },
            },
        },
    }
    lockfile.write_lockfile(data)

    assert lockfile.local_registry_name("cursor", "mcp", "alice", "free") == "free"
    assert lockfile.local_registry_name("cursor", "mcp", "alice", "search") == "search"
    assert lockfile.local_registry_name("cursor", "mcp", "bob.team", "search") == "bob-team-search"
    assert lockfile.local_registry_name("cursor", "mcp", "carol", "search") == "registry-example-test-carol-search"
    assert lockfile.local_registry_name("cursor", "mcp", "alice", "remote") == "alice-remote"
    assert lockfile.local_registry_name("cursor", "mcp", "any", "skill-only") == "skill-only"
    assert lockfile.local_registry_name("cursor", "agent", "new", "search") == "new-search"


def test_local_registry_name_limits_project_collisions_to_the_same_directory(isolated_lockfile):
    persist_registry(
        isolated_lockfile,
        {
            "kiro": {
                "agents": [],
                "standalone": [
                    {
                        "type": "skill",
                        "namespace": "alice",
                        "slug": "review",
                        "local_name": "review",
                        "scope": "project",
                        "directory": "/repo/one",
                    },
                    {
                        "type": "skill",
                        "namespace": "alice",
                        "slug": "global",
                        "local_name": "global",
                        "scope": "user",
                    },
                ],
            }
        },
    )

    assert (
        lockfile.local_registry_name("kiro", "skill", "bob", "review", scope="project", directory="/repo/two")
        == "review"
    )
    assert (
        lockfile.local_registry_name("kiro", "skill", "bob", "review", scope="project", directory="/repo/one")
        == "bob-review"
    )
    assert (
        lockfile.local_registry_name("kiro", "skill", "bob", "global", scope="project", directory="/repo/two")
        == "global"
    )


def test_hashes_are_canonical_registry_scoped_and_integrity_is_exact(isolated_lockfile):
    assert lockfile.compute_lockfile_hash() == "0" * 16

    persist_registry(
        isolated_lockfile,
        {"kiro": {"agents": [{"id": "agent-1", "version": "1.0.0"}], "standalone": []}},
    )
    _, registry = lockfile.read_registry_lockfile()
    expected = hashlib.sha256(json.dumps(registry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    first_hash = lockfile.compute_lockfile_hash()
    assert first_hash == expected
    assert len(first_hash) == 16

    data = raw_lockfile(isolated_lockfile)
    data["updated_at"] = "2099-01-01T00:00:00+00:00"
    isolated_lockfile.path.write_text(json.dumps(data), encoding="utf-8")
    assert lockfile.compute_lockfile_hash() == first_hash

    data["registries"][isolated_lockfile.server_url]["harnesses"]["kiro"]["agents"][0]["version"] = "2.0.0"
    isolated_lockfile.path.write_text(json.dumps(data), encoding="utf-8")
    assert lockfile.compute_lockfile_hash() != first_hash
    assert lockfile.compute_integrity("hello") == ("sha256-" + hashlib.sha256(b"hello").hexdigest())


def test_marker_migration_with_no_markers_writes_an_empty_schema(isolated_lockfile, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    isolated_lockfile.config_dir.mkdir(parents=True)
    (isolated_lockfile.config_dir / "sync_state.json").write_text("{broken", encoding="utf-8")
    child = home / "child"
    child.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "cwd", lambda: child)

    assert lockfile.migrate_agent_markers() == 0
    assert raw_lockfile(isolated_lockfile) == {
        "lock_version": 2,
        "updated_at": NOW,
        "registries": {},
    }
    assert lockfile.migrate_agent_markers() == 0


def test_marker_migration_deduplicates_projects_and_cleans_valid_markers(isolated_lockfile, monkeypatch, tmp_path):
    home = tmp_path / "home"
    code = home / "code"
    first_project = code / "first"
    second_project = code / "second"
    first_marker = first_project / ".observal" / "agent"
    second_marker = second_project / ".observal" / "agent"
    missing_id_marker = code / "missing-id" / ".observal" / "agent"
    malformed_marker = code / "malformed" / ".observal" / "agent"
    deep_marker = code / "one" / "two" / "three" / "four" / ".observal" / "agent"
    for marker in (first_marker, second_marker, missing_id_marker, malformed_marker, deep_marker):
        marker.parent.mkdir(parents=True)
    first_marker.write_text(
        json.dumps({"agent_id": "agent-1", "agent_version": "1.2.3", "pulled_at": "2025-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    second_marker.write_text(json.dumps({"agent_id": "agent-1", "agent_version": None}), encoding="utf-8")
    missing_id_marker.write_text(json.dumps({"agent_version": "9"}), encoding="utf-8")
    malformed_marker.write_text("{broken", encoding="utf-8")
    deep_marker.write_text(json.dumps({"agent_id": "too-deep"}), encoding="utf-8")
    isolated_lockfile.config_dir.mkdir(parents=True)
    (isolated_lockfile.config_dir / "sync_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "cwd", lambda: first_project)

    assert lockfile.migrate_agent_markers() == 2

    persisted = raw_lockfile(isolated_lockfile)
    assert persisted["lock_version"] == 2
    registry = persisted["registries"][lockfile.current_registry_url()]
    assert registry["server_url"] == isolated_lockfile.server_url
    agents = registry["harnesses"]["claude-code"]["agents"]
    by_directory = {entry["directory"]: entry for entry in agents}
    assert set(by_directory) == {str(first_project.resolve()), str(second_project.resolve())}
    assert by_directory[str(first_project.resolve())] == {
        "name": "agent-1",
        "id": "agent-1",
        "version": "1.2.3",
        "pulled_at": "2025-01-01T00:00:00Z",
        "scope": "project",
        "directory": str(first_project.resolve()),
        "components": [],
    }
    assert by_directory[str(second_project.resolve())]["version"] is None
    assert by_directory[str(second_project.resolve())]["pulled_at"] == NOW
    assert not first_marker.exists()
    assert not second_marker.exists()
    assert not missing_id_marker.exists()
    assert malformed_marker.exists()
    assert deep_marker.exists()


def test_marker_scan_and_cleanup_filesystem_failures_are_nonfatal(isolated_lockfile, monkeypatch, tmp_path):
    home = tmp_path / "home"
    code = home / "code"
    project = code / "project"
    marker = project / ".observal" / "agent"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"agent_id": "agent-1"}), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "cwd", lambda: home)
    real_unlink = Path.unlink

    def denied_unlink(path, *args, **kwargs):
        if path == marker:
            raise OSError("unlink denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    assert lockfile.migrate_agent_markers() == 1
    assert marker.exists()


def test_marker_root_scan_failure_creates_empty_lockfile(isolated_lockfile, monkeypatch, tmp_path):
    home = tmp_path / "home"
    code = home / "code"
    code.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "cwd", lambda: home)
    real_glob = Path.glob

    def denied_glob(path, pattern):
        if path == code:
            raise PermissionError("scan denied")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", denied_glob)
    assert lockfile.migrate_agent_markers() == 0
    assert raw_lockfile(isolated_lockfile)["registries"] == {}


def test_marker_write_failure_keeps_source_marker(isolated_lockfile, monkeypatch, tmp_path):
    home = tmp_path / "home"
    project = home / "code" / "project"
    marker = project / ".observal" / "agent"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"agent_id": "agent-1"}), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(Path, "cwd", lambda: home)
    write = Mock(side_effect=OSError("write denied"))
    monkeypatch.setattr(lockfile, "write_lockfile", write)

    with pytest.raises(OSError, match="write denied"):
        lockfile.migrate_agent_markers()
    assert marker.exists()
    write.assert_called_once()
