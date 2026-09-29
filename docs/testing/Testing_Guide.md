---
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 RAWx18 <rawx18.dev@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Testing Guide"
---

This guide defines the pattern for new Python tests in Observal. The current test suite has several styles because it grew over time. Do not rewrite old tests only for style. New tests and touched files should move toward this pattern when it keeps the diff focused.

## Goals

Good tests in this repo should be:

- **Hermetic**: no real network, Docker, user config, or external services.
- **Behavioral**: assert user-visible behavior or service contracts, not incidental internals.
- **Small**: one route group, command, service, or behavior area per file.
- **Explicit**: setup is visible in the test or in small local helpers.
- **Fast**: sleeps, external calls, and expensive setup are mocked at the boundary.

## File layout

Use this structure for most new test files:

```python
# SPDX-FileCopyrightText: 2026 Your Name <email@example.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the behavior under test.

Cover the most important scenarios in a short list when useful:
* successful path
* denied access
* edge case or regression
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock


def _make_item(*, name: str = "example") -> MagicMock:
    item = MagicMock()
    item.id = uuid.uuid4()
    item.name = name
    return item


class TestExampleBehavior:
    """Tests for the public behavior of ExampleBehavior."""

    def test_returns_name_for_valid_item(self) -> None:
        from services.example import item_name

        item = _make_item(name="alpha")

        result = item_name(item)

        assert result == "alpha"
```

Keep the order consistent:

1. SPDX header
2. Module docstring
3. `from __future__ import annotations` when useful
4. Standard library imports
5. Test library imports
6. App imports needed for the test harness
7. Local helper factories
8. Test classes or test functions

## File scope

Prefer one file per behavior area:

- `tests/test_agent_name_lookup.py` for agent lookup behavior
- `tests/test_component_versions_api.py` for component version endpoints
- `observal_cli/tests/test_cmd_scan.py` for the `scan` command
- `observal-server/tests/test_jwt.py` for JWT service behavior

Split a file when it mixes unrelated layers. For example, schema validation, route behavior, config generation, and resolver service behavior should usually live in separate files.

## Imports

Use top-level imports for the test harness:

- Standard library modules
- `pytest`
- `unittest.mock`
- Test clients such as `AsyncClient`, `ASGITransport`, and `CliRunner`
- Models, schemas, routers, and dependency functions needed by local helpers

Inline imports are useful for the unit under test:

```python
def test_resolves_agent_components() -> None:
    from services.agent_resolver import ResolvedAgent

    resolved = ResolvedAgent(
        agent_id=uuid.uuid4(),
        agent_name="demo",
        agent_version="1.0.0",
    )

    assert resolved.ok is True
```

Use inline imports when importing the unit under test needs environment setup, has side effects, or makes the test easier to isolate. Do not force every import to be inline.

## Helpers and fixtures

Prefer small helper functions for local setup:

```python
def _make_user(*, role: UserRole = UserRole.user) -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = role
    user.email = "test@example.com"
    user.username = "testuser"
    return user


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    return db
```

Use `pytest.fixture` when pytest lifecycle behavior is helpful:

- Temporary directories through `tmp_path`
- Environment isolation through `monkeypatch`
- Cleanup with `yield`
- Shared setup used by many files through `conftest.py`

Prefer helpers over fixtures when the setup takes parameters or is only used in one file.

## Test body shape

Use a simple arrange, act, assert flow with blank lines between phases:

```python
def test_owner_can_view_pending_listing() -> None:
    user = _make_user(role=UserRole.user)
    listing = _make_listing(status=ListingStatus.pending, created_by=user.id)

    result = can_view_listing(listing, user)

    assert result is True
```

Comments are only needed when the reason is not obvious. A clear test name is better than a comment that repeats the code.

## Naming

Use behavior names:

```python
def test_missing_auth_returns_401() -> None:
def test_owner_can_install_pending_listing() -> None:
def test_scan_does_not_modify_ide_files() -> None:
def test_hyphenated_name_resolves_by_name() -> None:
```

Avoid vague names:

```python
def test_case_1() -> None:
def test_function_works() -> None:
def test_route() -> None:
```

A good pattern is:

```text
test_<condition>_<expected_behavior>
```

Inside a class, the class can provide the subject:

```python
class TestAgentLookup:
    def test_hyphenated_name_returns_agent(self) -> None:
        ...
```

Use a test class when there are at least a few related tests. Module-level test functions are fine for one or two simple cases.

## Mocking

Mock boundaries, not the behavior under test.

Good boundaries to mock:

- HTTP clients and webhooks
- Database session methods
- Filesystem writes when the write itself is not under test
- Sleep and time delays
- Auth providers
- External CLIs and subprocesses

Avoid mocking:

- Pydantic schemas
- Small pure functions
- The function being tested
- Several layers of internal implementation at once

Use `AsyncMock` for async methods and `MagicMock` for sync methods. Use `spec` when mocking model objects:

```python
user = MagicMock(spec=User)
```

Keep patches close to the test that needs them:

```python
@patch("api.routes.agent.crud._load_agent")
async def test_name_lookup_returns_agent(mock_load: MagicMock) -> None:
    ...
```

## Async tests

Mark async tests explicitly:

```python
@pytest.mark.asyncio
async def test_fetches_current_user() -> None:
    from services.users import fetch_current_user

    db = _mock_db()

    user = await fetch_current_user(db, user_id=uuid.uuid4())

    assert user is not None
```

The root pytest config sets asyncio mode to auto, but the marker keeps async tests easy to spot.

## API route tests

Use a small FastAPI app with dependency overrides. Do not boot the full application unless the integration boundary is the thing being tested.

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import get_current_user, get_db
from api.routes.agent import router
from models.user import User, UserRole


def _make_user() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = UserRole.user
    user.email = "test@example.com"
    user.username = "testuser"
    return user


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


def _app_with(
    *,
    user: User | None = None,
    db: AsyncMock | None = None,
) -> tuple[FastAPI, AsyncMock, User]:
    user = user or _make_user()
    db = db or _mock_db()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, db, user


class TestAgentLookup:
    """Tests for agent lookup routes."""

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_404(self) -> None:
        app, db, _ = _app_with()
        db.execute.return_value = MagicMock(one_or_none=MagicMock(return_value=None))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/agents/missing-agent")

        assert response.status_code == 404
```

For route tests, assert the response status and response body first. Assert database calls only when they are part of the contract.

## CLI tests

CLI tests should not read or write the real home directory. Redirect home and current working directory to a temp path:

```python
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from observal_cli.main import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture()
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


class TestScanCommand:
    """Tests for the scan command."""

    def test_empty_home_exits_with_message(self, sandbox_home: Path) -> None:
        result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1, result.output
        assert "No harness configurations found" in result.output
```

Always include `result.output` in exit code assertions so failures are easy to diagnose.

## Parametrize

Use parametrization for the same behavior across multiple inputs:

```python
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (UserRole.admin, True),
        (UserRole.reviewer, True),
        (UserRole.user, False),
    ],
)
def test_role_can_review(role: UserRole, expected: bool) -> None:
    result = can_review(role)

    assert result is expected
```

Do not use a large parameter matrix when separate named tests would be easier to read.

## Property-based tests

`hypothesis` is available for invariants that should hold over many inputs. Use it for pure logic, parsers, serializers, redaction, and validation edge cases.

```python
from hypothesis import given, strategies as st


class TestRedactionProperties:
    """Property tests for support bundle redaction."""

    @given(secret=st.text(min_size=1, max_size=100))
    def test_secret_value_is_removed(self, secret: str) -> None:
        from observal_cli.support.redaction import redact_text

        result = redact_text(f"token={secret}", secrets=[secret])

        assert secret not in result
```

Keep property-based tests deterministic and focused. If a failing example reveals a bug, add a named regression test too.

## Fuzzing

Property tests cover invariants you can name. Fuzzing covers the ones you cannot: the Atheris targets live in `fuzz/`, and the project is being submitted to [Google OSS-Fuzz](https://google.github.io/oss-fuzz/) for continuous fuzzing.

Reach for a fuzz target instead of a property test when the boundary takes untrusted bytes and the interesting failures are crashes rather than wrong answers. Session transcripts, the ingest classifiers, and both redaction layers are covered today.

```bash
make test-fuzz                                          # smoke-test every target
python3 fuzz/session_jsonl_fuzzer.py -atheris_runs=100000
python3 fuzz/session_jsonl_fuzzer.py crash-<sha1>       # reproduce a finding
```

`fuzz/README.md` covers adding a target, seed corpora, dictionaries, and the OSS-Fuzz project configuration.

## Assertions

Use plain `assert`. Assert the public result before internal interactions:

```python
assert response.status_code == 200
assert response.json()["name"] == "my-agent"
```

Use call assertions when the call is the behavior:

```python
mock_post.assert_called_once_with("/ingest", json=payload)
```

For CLI tests, include output in the assertion message:

```python
assert result.exit_code == 0, result.output
```

For exceptions, assert the important fields:

```python
with pytest.raises(HTTPException) as exc_info:
    await require_admin(user)

assert exc_info.value.status_code == 403
assert "admin" in exc_info.value.detail.lower()
```

## Test directories

Use the existing directories:

- `tests/` for cross-cutting backend, CLI, and integration-style unit tests
- `observal-server/tests/` for server-focused tests that live with the server package
- `observal_cli/tests/` for CLI command and CLI package tests
- `tests/e2e/` for Playwright tests that require the running stack
- `tests/integration_clickhouse/` for opt-in tests against real ClickHouse and PostgreSQL; see [Database integration tests](database-integration-tests.md)

Shared setup should stay small:

- `tests/conftest.py` adds the server source path for imports.
- `observal-server/tests/conftest.py` sets test environment variables and shared auth fixtures.

Extend `conftest.py` only when setup is useful across several files.

## Running tests

Use the make targets for normal workflows:

```bash
make test
make test-v
make test-eval-completeness
make test-adversarial
make test-fuzz
make test-all
```

Run focused pytest commands from `observal-server` when iterating on one file:

```bash
cd observal-server
uv run pytest ../tests/test_agent_name_lookup.py -q
uv run pytest ../observal-server/tests/test_jwt.py -q
uv run pytest ../observal_cli/tests/test_cmd_scan.py -q
```

Use `make lint` and `make format` before pushing Python changes. Use `make check` when you need the full pre-commit suite.

## Formatting and lint notes

The root `pyproject.toml` relaxes a few lint rules for tests. Those ignores are for practical mock setup, not a reason to leave tests messy.

Current per-file ignores include:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["F841", "RUF059", "B007", "TID251"]
"observal-server/tests/*" = ["F841", "RUF059", "B007", "TID251", "E402"]
```

In practice:

- Unused mock variables are allowed in test files.
- Some server tests can use guarded imports after setup.
- Tests should import feature modules from their normal server package paths.
- New tests should still prefer clear imports, typed helpers, and small setup.

## Review checklist

Before submitting a test change, check that:

- The test fails without the product fix or covers a meaningful invariant.
- External services are mocked or replaced with local fakes.
- Real user config and real home directories are not touched.
- The test name states the behavior.
- Helpers are local unless they are reused across files.
- Async tests use `AsyncMock` for awaited methods.
- CLI tests include command output in exit code assertions.
- API route tests override dependencies in a local FastAPI app.
- Focused tests pass locally.
