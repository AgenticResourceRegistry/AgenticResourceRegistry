# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import services.mcp_validator as validator


@pytest.fixture(autouse=True)
def _block_external_io(monkeypatch):
    """Make an accidental DNS lookup or Git clone fail before leaving the test."""
    monkeypatch.setattr(
        validator,
        "_ssrf_is_private",
        Mock(side_effect=AssertionError("unexpected SSRF lookup")),
    )
    monkeypatch.setattr(
        validator.Repo,
        "clone_from",
        Mock(side_effect=AssertionError("unexpected Git clone")),
    )


def _db():
    return SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), add=Mock())


def _listing(**overrides):
    values = {
        "id": uuid.UUID(int=1),
        "git_url": "https://git.example/acme/catalog.git",
        "name": "catalog",
        "description": "A" * 120,
        "mcp_validated": False,
        "framework": None,
        "docker_image": None,
        "setup_instructions": None,
        "command": None,
        "args": None,
        "latest_version": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(source: str = "", relative: str = "src/server.py"):
    entry = MagicMock(spec=Path)
    entry.read_text.return_value = source
    entry.relative_to.return_value = Path(relative)
    return entry


def _added_results(db):
    return [call.args[0] for call in db.add.call_args_list]


def _mock_tempdir(monkeypatch, path: str = "/virtual/repo"):
    make = Mock(return_value=path)
    remove = Mock()
    monkeypatch.setattr(validator.tempfile, "mkdtemp", make)
    monkeypatch.setattr(validator.shutil, "rmtree", remove)
    return make, remove


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("git://git.example/acme/catalog", "scheme"),
        ("https:///catalog", "no hostname"),
        ("https://[broken", "Invalid URL"),
    ],
)
def test_validate_git_url_rejects_malformed_or_disallowed_urls(url, message, monkeypatch):
    monkeypatch.setattr(validator, "ALLOWED_SCHEMES", {"https"})

    assert message in validator._validate_git_url(url)


def test_validate_git_url_rejects_private_resolution(monkeypatch):
    guard = Mock(return_value=True)
    monkeypatch.setattr(validator, "_ssrf_is_private", guard)
    monkeypatch.setattr(validator, "ALLOW_INTERNAL_URLS", False)

    error = validator._validate_git_url("https://git.example/acme/catalog")

    assert error == ("Internal/private URLs not allowed (set ALLOW_INTERNAL_GIT_URLS=true for self-hosted deployments)")
    guard.assert_called_once_with("https://git.example/acme/catalog")


def test_validate_git_url_accepts_public_and_explicitly_allowed_internal_urls(monkeypatch):
    guard = Mock(return_value=False)
    monkeypatch.setattr(validator, "_ssrf_is_private", guard)
    monkeypatch.setattr(validator, "ALLOW_INTERNAL_URLS", False)
    assert validator._validate_git_url("https://git.example/acme/catalog") is None
    guard.assert_called_once()

    guard.reset_mock(side_effect=True)
    guard.side_effect = AssertionError("guard should be bypassed")
    monkeypatch.setattr(validator, "ALLOW_INTERNAL_URLS", True)
    assert validator._validate_git_url("https://10.0.0.8/acme/catalog") is None
    guard.assert_not_called()


def test_git_url_warning_only_reports_opted_in_http(monkeypatch):
    monkeypatch.setattr(validator, "ALLOW_HTTP_GIT", True)
    assert "MCP_ALLOW_HTTP_GIT" in validator._git_url_warning("http://git.example/catalog")
    assert validator._git_url_warning("https://git.example/catalog") == ""

    monkeypatch.setattr(validator, "ALLOW_HTTP_GIT", False)
    assert validator._git_url_warning("http://git.example/catalog") == ""


def test_build_clone_url_handles_anonymous_and_token_auth(monkeypatch):
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "")
    source = "https://git.example/acme/catalog.git"
    assert validator._build_clone_url(source) == source

    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY_SECRET_FOR_TEST")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "git.example, github.com")
    monkeypatch.setenv("GIT_CLONE_TOKEN_USER", "oauth2")
    assert validator._build_clone_url(source) == "https://oauth2:DUMMY_SECRET_FOR_TEST@git.example/acme/catalog.git"
    assert validator._build_clone_url("https://GITHUB.COM/acme/repo.git") == (
        "https://oauth2:DUMMY_SECRET_FOR_TEST@GITHUB.COM/acme/repo.git"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/repo.git",
        "https://github.com.attacker.example/repo.git",
        "https://github.com@attacker.example/repo.git",
        "https://attacker.example@github.com/repo.git",
        "https://github.com./repo.git",
        "https://github.com:8443/repo.git",
        "http://github.com/repo.git",
    ],
)
def test_build_clone_url_never_authenticates_untrusted_destinations(url, monkeypatch):
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY_SECRET_FOR_TEST")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "github.com")
    assert validator._build_clone_url(url) == url


def test_build_clone_url_requires_explicit_hosts_and_preserves_https_port_and_query(monkeypatch):
    monkeypatch.delenv("GIT_CLONE_TOKEN_USER", raising=False)
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY/SECRET:@")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "")
    url = "https://git.example:8443/acme/repo.git?ref=main"
    assert validator._build_clone_url(url) == url
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "git.example:8443")
    assert validator._build_clone_url(url) == (
        "https://x-access-token:DUMMY%2FSECRET%3A%40@git.example:8443/acme/repo.git?ref=main"
    )


def test_redact_clone_error_removes_clone_token_before_general_redaction(monkeypatch):
    redactor = Mock(return_value="sanitized")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "secret-token")
    monkeypatch.setattr(validator, "redact_secrets", redactor)

    result = validator._redact_clone_error(RuntimeError("clone secret-token Authorization: bearer-value"))

    assert result == "sanitized"
    redacted_input = redactor.call_args.args[0]
    assert "secret-token" not in redacted_input
    assert validator.REDACTED in redacted_input


def test_redact_clone_error_removes_percent_encoded_token_in_git_url(monkeypatch):
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY/SECRET:@")
    error = RuntimeError("fatal: unable to access 'https://x-access-token:DUMMY%2FSECRET%3A%40@github.com/repo.git'")

    result = validator._redact_clone_error(error)

    assert "DUMMY%2FSECRET%3A%40" not in result
    assert "DUMMY/SECRET:@" not in result
    assert validator.REDACTED in result


def test_redact_clone_error_without_clone_token_still_uses_secret_redactor(monkeypatch):
    redactor = Mock(return_value="safe")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "")
    monkeypatch.setattr(validator, "redact_secrets", redactor)

    assert validator._redact_clone_error(ValueError("ordinary failure")) == "safe"
    redactor.assert_called_once_with("ordinary failure")


@pytest.mark.asyncio
async def test_async_clone_delegates_to_bounded_thread(monkeypatch):
    calls = {}
    clone = Mock(return_value="repo")

    async def fake_to_thread(function, *args, **kwargs):
        calls["thread"] = (function, args, kwargs)
        return function(*args, **kwargs)

    async def fake_wait_for(awaitable, timeout):
        calls["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(validator.Repo, "clone_from", clone)
    monkeypatch.setattr(validator.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(validator.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(validator, "CLONE_TIMEOUT", 17)

    await validator._async_clone("https://git.example/acme/catalog", "/virtual/repo", depth=2)

    assert calls["timeout"] == 17
    assert calls["thread"] == (
        clone,
        ("https://git.example/acme/catalog", "/virtual/repo"),
        {"depth": 2},
    )
    clone.assert_called_once()


@pytest.mark.asyncio
async def test_authenticated_clone_disables_http_redirects(monkeypatch):
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY_SECRET_FOR_TEST")
    clone = Mock()
    monkeypatch.setattr(validator.Repo, "clone_from", clone)
    url = "https://x-access-token:DUMMY_SECRET_FOR_TEST@github.com/acme/private.git"
    await validator._async_clone(url, "/virtual/repo")
    assert clone.call_args.kwargs["env"] == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.followRedirects",
        "GIT_CONFIG_VALUE_0": "false",
    }


def test_apply_container_detection_sets_only_missing_fields(monkeypatch):
    detect = Mock(return_value=("ghcr.io/acme/catalog:1", True, ["docker build -t catalog .", "make image"]))
    monkeypatch.setattr(validator, "detect_container_image", detect)
    listing = _listing()

    validator._apply_container_detection(listing, "/virtual/repo")

    assert listing.docker_image == "ghcr.io/acme/catalog:1"
    assert listing.setup_instructions == "docker build -t catalog .\nmake image"
    detect.assert_called_once_with(Path("/virtual/repo"), listing.git_url, listing.name)

    listing.docker_image = "existing:image"
    listing.setup_instructions = "existing setup"
    validator._apply_container_detection(listing, "/virtual/repo")
    assert listing.docker_image == "existing:image"
    assert listing.setup_instructions == "existing setup"


@pytest.mark.asyncio
async def test_run_validation_replaces_results_runs_both_stages_and_cleans_up(monkeypatch):
    db = _db()
    listing = _listing()
    entry = _entry()
    clone = AsyncMock(return_value=entry)
    manifest = AsyncMock()
    make, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_clone_and_inspect", clone)
    monkeypatch.setattr(validator, "_manifest_validation", manifest)

    await validator.run_validation(listing, db)

    db.execute.assert_awaited_once()
    assert "mcp_validation_results" in str(db.execute.call_args.args[0])
    assert db.commit.await_count == 1
    make.assert_called_once_with(prefix="observal_")
    clone.assert_awaited_once_with(listing, db, "/virtual/repo")
    manifest.assert_awaited_once_with(listing, db, entry, "/virtual/repo")
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_run_validation_stops_after_clone_stage_but_still_cleans_up(monkeypatch):
    db = _db()
    listing = _listing()
    manifest = AsyncMock()
    _, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_clone_and_inspect", AsyncMock(return_value=None))
    monkeypatch.setattr(validator, "_manifest_validation", manifest)

    await validator.run_validation(listing, db)

    manifest.assert_not_awaited()
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_run_validation_cleans_up_when_a_stage_raises(monkeypatch):
    db = _db()
    _, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_clone_and_inspect", AsyncMock(side_effect=RuntimeError("stage failed")))

    with pytest.raises(RuntimeError, match="stage failed"):
        await validator.run_validation(_listing(), db)

    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_clone_stage_records_url_security_rejection(monkeypatch):
    db = _db()
    listing = _listing()
    clone = AsyncMock()
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value="private URL blocked"))
    monkeypatch.setattr(validator, "_async_clone", clone)

    assert await validator._clone_and_inspect(listing, db, "/virtual/repo") is None

    clone.assert_not_awaited()
    result = _added_results(db)[0]
    assert (result.stage, result.passed, result.details) == ("clone_and_inspect", False, "private URL blocked")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_validation_only_passes_token_for_allowlisted_host(monkeypatch):
    monkeypatch.delenv("GIT_CLONE_TOKEN_USER", raising=False)
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY_SECRET_FOR_TEST")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "github.com")
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    clone = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(validator, "_async_clone", clone)
    for url, authenticated in (
        ("https://github.com/acme/private.git", True),
        ("https://github.com.attacker.example/acme/private.git", False),
    ):
        await validator._clone_and_inspect(_listing(git_url=url), _db(), "/virtual/repo")
        expected = url.replace("https://", "https://x-access-token:DUMMY_SECRET_FOR_TEST@", 1) if authenticated else url
        clone.assert_awaited_with(expected, "/virtual/repo")


@pytest.mark.asyncio
async def test_analyze_only_passes_token_for_allowlisted_host(monkeypatch):
    monkeypatch.delenv("GIT_CLONE_TOKEN_USER", raising=False)
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY_SECRET_FOR_TEST")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "github.com")
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    _mock_tempdir(monkeypatch)
    clone = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(validator, "_async_clone", clone)
    for url, authenticated in (
        ("https://github.com/acme/private.git", True),
        ("https://attacker.example/acme/private.git", False),
    ):
        await validator.analyze_repo(url)
        expected = url.replace("https://", "https://x-access-token:DUMMY_SECRET_FOR_TEST@", 1) if authenticated else url
        clone.assert_awaited_with(expected, "/virtual/repo")


@pytest.mark.asyncio
async def test_clone_stage_classifies_timeout(monkeypatch):
    db = _db()
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock(side_effect=TimeoutError))
    monkeypatch.setattr(validator, "CLONE_TIMEOUT", 9)

    assert await validator._clone_and_inspect(_listing(), db, "/virtual/repo") is None

    result = _added_results(db)[0]
    assert result.stage == "clone_and_inspect"
    assert result.passed is False
    assert result.details == "Clone timed out after 9s. For slow repos, increase GIT_CLONE_TIMEOUT."


@pytest.mark.asyncio
async def test_clone_stage_classifies_and_redacts_clone_failure(monkeypatch):
    db = _db()
    redact = Mock(return_value="credentials removed")
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock(side_effect=RuntimeError("clone failed")))
    monkeypatch.setattr(validator, "_redact_clone_error", redact)

    assert await validator._clone_and_inspect(_listing(), db, "/virtual/repo") is None

    result = _added_results(db)[0]
    assert result.passed is False
    assert result.details == "Failed to clone repo: credentials removed"
    redact.assert_called_once()


@pytest.mark.asyncio
async def test_clone_stage_does_not_persist_percent_encoded_git_token(monkeypatch):
    db = _db()
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN", "DUMMY/SECRET:@")
    monkeypatch.setattr(validator.settings, "GIT_CLONE_TOKEN_HOSTS", "github.com")
    monkeypatch.delenv("GIT_CLONE_TOKEN_USER", raising=False)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    url = "https://github.com/acme/private.git"
    authenticated = validator._build_clone_url(url)
    monkeypatch.setattr(
        validator, "_async_clone", AsyncMock(side_effect=RuntimeError(f"unable to access {authenticated}"))
    )

    assert await validator._clone_and_inspect(_listing(git_url=url), db, "/virtual/repo") is None

    details = _added_results(db)[0].details
    assert "DUMMY%2FSECRET%3A%40" not in details
    assert "DUMMY/SECRET:@" not in details
    assert validator.REDACTED in details


@pytest.mark.asyncio
async def test_clone_stage_detects_python_and_infers_startup_config(monkeypatch):
    db = _db()
    listing = _listing()
    entry = _entry(relative="src/catalog.py")
    apply_container = Mock()
    infer = Mock(return_value=("python", ["-m", "catalog"]))
    clone = AsyncMock()
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_git_url_warning", Mock(return_value="insecure URL warning"))
    monkeypatch.setattr(validator, "_build_clone_url", Mock(return_value="authenticated-url"))
    monkeypatch.setattr(validator, "_async_clone", clone)
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=entry))
    monkeypatch.setattr(validator, "_apply_container_detection", apply_container)
    monkeypatch.setattr(validator, "infer_command_args", infer)

    returned = await validator._clone_and_inspect(listing, db, "/virtual/repo")

    assert returned is entry
    clone.assert_awaited_once_with("authenticated-url", "/virtual/repo")
    assert listing.mcp_validated is True
    assert listing.framework == "python-mcp"
    assert listing.command == "python"
    assert listing.args == ["-m", "catalog"]
    apply_container.assert_called_once_with(listing, "/virtual/repo")
    infer.assert_called_once_with("python-mcp", None, "catalog")
    result = _added_results(db)[0]
    assert result.passed is True
    assert result.details == "Found MCP entry point: src/catalog.py\ninsecure URL warning"

    explicit = _listing(command="existing", args=["keep"])
    await validator._clone_and_inspect(explicit, _db(), "/virtual/repo")
    assert explicit.command == "existing"
    assert explicit.args == ["keep"]


@pytest.mark.asyncio
async def test_clone_stage_detects_non_python_and_preserves_explicit_startup_config(monkeypatch):
    db = _db()
    listing = _listing(command="custom", args=["serve"])
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_git_url_warning", Mock(return_value=""))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=None))
    monkeypatch.setattr(validator, "detect_non_python_mcp", Mock(return_value="typescript-mcp-sdk"))
    monkeypatch.setattr(validator, "_apply_container_detection", Mock())
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=("npx", ["-y", "catalog"])))

    assert await validator._clone_and_inspect(listing, db, "/virtual/repo") is None

    assert listing.mcp_validated is True
    assert listing.framework == "typescript-mcp-sdk"
    assert listing.command == "custom"
    assert listing.args == ["serve"]
    result = _added_results(db)[0]
    assert (result.stage, result.passed) == ("clone_and_inspect", True)
    assert result.details == "Found non-Python MCP framework: typescript-mcp-sdk"


@pytest.mark.asyncio
async def test_clone_stage_infers_non_python_startup_config(monkeypatch):
    db = _db()
    listing = _listing()
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=None))
    monkeypatch.setattr(validator, "detect_non_python_mcp", Mock(return_value="go-mcp-sdk"))
    monkeypatch.setattr(validator, "_apply_container_detection", Mock())
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=("catalog", ["serve"])))

    await validator._clone_and_inspect(listing, db, "/virtual/repo")

    assert listing.command == "catalog"
    assert listing.args == ["serve"]


@pytest.mark.asyncio
async def test_clone_stage_accepts_unknown_framework_with_explicit_result(monkeypatch):
    db = _db()
    listing = _listing()
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_git_url_warning", Mock(return_value="review warning"))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=None))
    monkeypatch.setattr(validator, "detect_non_python_mcp", Mock(return_value=None))
    monkeypatch.setattr(validator, "_apply_container_detection", Mock())
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=("catalog", ["serve"])))

    assert await validator._clone_and_inspect(listing, db, "/virtual/repo") is None

    assert listing.mcp_validated is True
    assert listing.command == "catalog"
    assert listing.args == ["serve"]
    result = _added_results(db)[0]
    assert result.passed is True
    assert "No recognized MCP framework detected" in result.details
    assert result.details.endswith("review warning")

    explicit = _listing(command="existing", args=["keep"])
    await validator._clone_and_inspect(explicit, _db(), "/virtual/repo")
    assert explicit.command == "existing"
    assert explicit.args == ["keep"]


@pytest.mark.asyncio
async def test_manifest_validation_accepts_typed_documented_fastmcp_tool():
    db = _db()
    listing = _listing(mcp_validated=True)
    source = '''
server = FastMCP("catalog-server")

def helper():
    return None

@server.tool
def search(query: str) -> str:
    """Search the catalog for matching entries."""
    return query
'''

    await validator._manifest_validation(listing, db, _entry(source), "/virtual/repo")

    result = _added_results(db)[0]
    assert (result.stage, result.passed) == ("manifest_validation", True)
    assert result.details == "Server: catalog-server, Tools: 1"
    assert listing.mcp_validated is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("constructor", "expected_name"),
    [
        ('Server(description="catalog", name="keyword-server")', "keyword-server"),
        ('Server("positional-server")', "positional-server"),
    ],
)
async def test_manifest_validation_extracts_server_constructor_names(constructor, expected_name):
    db = _db()
    source = f'''
server = {constructor}

@server.tool()
async def fetch(self, item: str) -> str:
    """Fetch one item from the remote catalog."""
    return item
'''

    await validator._manifest_validation(_listing(), db, _entry(source), "/virtual/repo")

    result = _added_results(db)[0]
    assert result.passed is True
    assert result.details == f"Server: {expected_name}, Tools: 1"


@pytest.mark.asyncio
async def test_manifest_validation_uses_repo_name_fallback(monkeypatch):
    db = _db()
    extract = Mock(return_value="fallback-name")
    monkeypatch.setattr(validator, "extract_repo_name", extract)
    source = '''
unused = Server()

@application.tool()
def inspect(value: str) -> str:
    """Inspect a value and return useful metadata."""
    return value
'''
    listing = _listing()

    await validator._manifest_validation(listing, db, _entry(source), "/virtual/repo")

    assert _added_results(db)[0].details == "Server: fallback-name, Tools: 1"
    extract.assert_called_once_with(listing.git_url, "/virtual/repo")


@pytest.mark.asyncio
async def test_manifest_validation_reports_syntax_error():
    db = _db()

    await validator._manifest_validation(_listing(), db, _entry("def broken(:\n"), "/virtual/repo")

    result = _added_results(db)[0]
    assert result.stage == "manifest_validation"
    assert result.passed is False
    assert result.details.startswith("Syntax error in entry point:")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_manifest_validation_reports_tool_and_description_quality_failures():
    db = _db()
    listing = _listing(description="short", mcp_validated=True)
    source = '''
server = FastMCP("quality")

@server.tool()
def weak(self, missing, typed: str):
    """tiny"""
    return typed
'''

    await validator._manifest_validation(listing, db, _entry(source), "/virtual/repo")

    result = _added_results(db)[0]
    assert result.passed is False
    assert "Tool 'weak' docstring too short (4 chars, need 20+)" in result.details
    assert "Tool 'weak' has untyped params: missing" in result.details
    assert "Server description too short (5 chars, need 100+)" in result.details
    assert listing.mcp_validated is False


@pytest.mark.asyncio
async def test_manifest_validation_reports_missing_tools():
    db = _db()
    listing = _listing(mcp_validated=True)

    await validator._manifest_validation(listing, db, _entry('server = FastMCP("empty")'), "/virtual/repo")

    result = _added_results(db)[0]
    assert result.passed is False
    assert "No @tool decorated functions found" in result.details
    assert listing.mcp_validated is False


@pytest.mark.asyncio
async def test_analyze_repo_rejects_url_before_allocating_or_cloning(monkeypatch):
    make = Mock(side_effect=AssertionError("temp directory should not be created"))
    clone = AsyncMock()
    monkeypatch.setattr(validator.tempfile, "mkdtemp", make)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value="private URL blocked"))
    monkeypatch.setattr(validator, "_async_clone", clone)

    result = await validator.analyze_repo("https://10.0.0.8/catalog")

    assert result == {
        "name": "",
        "description": "",
        "version": "0.1.0",
        "tools": [],
        "error": "private URL blocked",
    }
    make.assert_not_called()
    clone.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_repo_classifies_clone_timeout_and_cleans_up(monkeypatch):
    _, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock(side_effect=TimeoutError))
    monkeypatch.setattr(validator, "CLONE_TIMEOUT", 11)

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result["error"] == "Clone timed out after 11s. For slow repos, increase GIT_CLONE_TIMEOUT."
    assert result["tools"] == []
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            "authentication failed",
            "Repository is private or not accessible. Configure GIT_CLONE_TOKEN and GIT_CLONE_TOKEN_HOSTS for private repos.",
        ),
        ("repository not found", "Repository not found. Check the URL."),
        ("connection reset", "Failed to clone repository. Check the URL and try again."),
    ],
)
async def test_analyze_repo_classifies_clone_failures(failure, message, monkeypatch):
    _, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock(side_effect=RuntimeError(failure)))

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result["error"] == message
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_analyze_repo_returns_non_python_metadata_environment_and_container_setup(monkeypatch):
    _, remove = _mock_tempdir(monkeypatch)
    env = [{"name": "API_TOKEN", "description": "Access token", "required": True}]
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_build_clone_url", Mock(return_value="authenticated-url"))
    clone = AsyncMock()
    monkeypatch.setattr(validator, "_async_clone", clone)
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=None))
    monkeypatch.setattr(validator, "detect_env_vars", Mock(return_value=env))
    monkeypatch.setattr(validator, "detect_non_python_mcp", Mock(return_value="typescript-mcp-sdk"))
    monkeypatch.setattr(validator, "extract_repo_name", Mock(return_value="catalog"))
    monkeypatch.setattr(
        validator,
        "detect_container_image",
        Mock(return_value=("catalog:local", True, ["docker build -t catalog:local ."])),
    )
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=("npx", ["-y", "catalog"])))

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result == {
        "name": "catalog",
        "description": "",
        "version": "0.1.0",
        "tools": [],
        "environment_variables": env,
        "framework": "typescript-mcp-sdk",
        "docker_image": "catalog:local",
        "docker_image_suggested": True,
        "setup_instructions": "docker build -t catalog:local .",
        "command": "npx",
        "args": ["-y", "catalog"],
    }
    clone.assert_awaited_once_with("authenticated-url", "/virtual/repo")
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_analyze_repo_omits_unavailable_non_python_metadata(monkeypatch):
    _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=None))
    monkeypatch.setattr(validator, "detect_env_vars", Mock(return_value=[]))
    monkeypatch.setattr(validator, "detect_non_python_mcp", Mock(return_value=None))
    monkeypatch.setattr(validator, "extract_repo_name", Mock(return_value="catalog"))
    monkeypatch.setattr(validator, "detect_container_image", Mock(return_value=(None, False, [])))
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=(None, None)))

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result == {
        "name": "catalog",
        "description": "",
        "version": "0.1.0",
        "tools": [],
        "environment_variables": [],
    }


@pytest.mark.asyncio
async def test_analyze_repo_returns_python_tools_issues_and_startup_metadata(monkeypatch):
    _, remove = _mock_tempdir(monkeypatch)
    entry = _entry("server = FastMCP('catalog')", "src/catalog.py")
    tools = [{"name": "search", "docstring": "Search the catalog"}]
    issues = ["review documentation"]
    analyze = Mock(return_value=("catalog-server", "Catalog service", tools, issues))
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=entry))
    monkeypatch.setattr(
        validator,
        "detect_env_vars",
        Mock(return_value=[{"name": "API_TOKEN", "description": "", "required": True}]),
    )
    monkeypatch.setattr(validator, "analyze_python_entry", analyze)
    monkeypatch.setattr(
        validator,
        "detect_container_image",
        Mock(return_value=("ghcr.io/acme/catalog", False, ["make prepare"])),
    )
    monkeypatch.setattr(
        validator,
        "infer_command_args",
        Mock(return_value=("docker", ["run", "ghcr.io/acme/catalog"])),
    )

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result["name"] == "catalog-server"
    assert result["description"] == "Catalog service"
    assert result["tools"] == tools
    assert result["issues"] == issues
    assert result["environment_variables"][0]["name"] == "API_TOKEN"
    assert result["docker_image"] == "ghcr.io/acme/catalog"
    assert result["docker_image_suggested"] is False
    assert result["setup_instructions"] == "make prepare"
    assert result["command"] == "docker"
    assert result["args"] == ["run", "ghcr.io/acme/catalog"]
    analyze.assert_called_once()
    parsed_tree, git_url, temp_dir = analyze.call_args.args
    assert git_url == "https://git.example/acme/catalog"
    assert temp_dir == "/virtual/repo"
    assert parsed_tree.body
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)


@pytest.mark.asyncio
async def test_analyze_repo_omits_unavailable_python_startup_metadata(monkeypatch):
    _mock_tempdir(monkeypatch)
    entry = _entry("value = 1", "server.py")
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(return_value=entry))
    monkeypatch.setattr(validator, "detect_env_vars", Mock(return_value=[]))
    monkeypatch.setattr(validator, "analyze_python_entry", Mock(return_value=("catalog", "", [], [])))
    monkeypatch.setattr(validator, "detect_container_image", Mock(return_value=(None, False, [])))
    monkeypatch.setattr(validator, "infer_command_args", Mock(return_value=(None, None)))

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result == {
        "name": "catalog",
        "description": "",
        "version": "0.1.0",
        "tools": [],
        "issues": [],
        "environment_variables": [],
    }


@pytest.mark.asyncio
async def test_analyze_repo_contains_unexpected_analysis_failure_and_cleans_up(monkeypatch):
    _, remove = _mock_tempdir(monkeypatch)
    monkeypatch.setattr(validator, "_validate_git_url", Mock(return_value=None))
    monkeypatch.setattr(validator, "_async_clone", AsyncMock())
    monkeypatch.setattr(validator, "find_python_entry", Mock(side_effect=OSError("unreadable checkout")))

    result = await validator.analyze_repo("https://git.example/acme/catalog")

    assert result == {"name": "", "description": "", "version": "0.1.0", "tools": []}
    remove.assert_called_once_with("/virtual/repo", ignore_errors=True)
