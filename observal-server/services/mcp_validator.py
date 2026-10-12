# SPDX-FileCopyrightText: 2026 Subramania Raja <dhanpraja231@gmail.com>
# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-FileCopyrightText: 2026 tsitu0 <tomsitu0102@gmail.com>
# SPDX-FileCopyrightText: 2026 Lokesh <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import ast
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from git import Repo
from loguru import logger as optic
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.mcp import McpListing, McpValidationResult
from observal_shared.mcp_analysis import (
    analyze_python_entry,
    detect_container_image,
    detect_docker_image,
    detect_env_vars,
    detect_non_python_mcp,
    extract_repo_name,
    find_python_entry,
    infer_command_args,
    is_filtered_env_var,
    is_test_file,
)
from services.secrets_redactor import REDACTED, redact_secrets
from services.ssrf_guard import is_private_url as _ssrf_is_private

_is_filtered_env_var = is_filtered_env_var
_is_test_file = is_test_file
_detect_env_vars = detect_env_vars
_detect_docker_image = detect_docker_image
_detect_container_image = detect_container_image
_infer_command_args = infer_command_args
_detect_non_python_mcp = detect_non_python_mcp
_extract_repo_name = extract_repo_name
_analyze_python_entry = analyze_python_entry

ALLOW_HTTP_GIT = os.environ.get("MCP_ALLOW_HTTP_GIT", "").lower() in ("1", "true", "yes")
ALLOWED_SCHEMES = {"https"} | ({"http"} if ALLOW_HTTP_GIT else set())

# Self-hosted deployments set ALLOW_INTERNAL_GIT_URLS=true to allow corporate
# GitLab / GitHub Enterprise / Gitea on a private network.
ALLOW_INTERNAL_URLS = os.environ.get("ALLOW_INTERNAL_GIT_URLS", "").lower() in ("1", "true", "yes")

# Clone timeout in seconds (default 120s; internal GitLab may need more)
CLONE_TIMEOUT = int(os.environ.get("GIT_CLONE_TIMEOUT", "120"))


def _validate_git_url(url: str) -> str | None:
    """Returns error message if URL is unsafe, None if OK."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"URL scheme '{parsed.scheme}' not allowed. Use https://"
    if not parsed.hostname:
        return "URL has no hostname"
    if not ALLOW_INTERNAL_URLS and _ssrf_is_private(url):
        return "Internal/private URLs not allowed (set ALLOW_INTERNAL_GIT_URLS=true for self-hosted deployments)"
    return None


def _git_url_warning(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "http" and ALLOW_HTTP_GIT:
        return "Warning: insecure http:// Git URL accepted because MCP_ALLOW_HTTP_GIT=true."
    return ""


def _build_clone_url(git_url: str) -> str:
    """Inject auth token into git URL if configured. Supports GitHub and GitLab token formats."""
    git_token = settings.GIT_CLONE_TOKEN or ""
    if not git_token:
        return git_url
    parsed = urlparse(git_url)
    token_user = os.environ.get("GIT_CLONE_TOKEN_USER", "x-access-token")
    return f"{parsed.scheme}://{token_user}:{git_token}@{parsed.hostname}{parsed.path}"


def _redact_clone_error(error: Exception) -> str:
    message = str(error)
    git_token = settings.GIT_CLONE_TOKEN or ""
    if git_token:
        message = message.replace(git_token, REDACTED)
    return redact_secrets(message)


async def _async_clone(clone_url: str, dest: str, depth: int = 1) -> None:
    """Clone a repo in a thread with a timeout so we don't block the event loop."""
    await asyncio.wait_for(
        asyncio.to_thread(Repo.clone_from, clone_url, dest, depth=depth),
        timeout=CLONE_TIMEOUT,
    )


def _apply_container_detection(listing: McpListing, tmp_dir: str) -> None:
    image, _suggested, setup_commands = detect_container_image(Path(tmp_dir), listing.git_url or "", listing.name)
    if image and not listing.docker_image:
        listing.docker_image = image
    if setup_commands and not listing.setup_instructions:
        listing.setup_instructions = "\n".join(setup_commands)


async def run_validation(listing: McpListing, db: AsyncSession):
    optic.trace("running MCP validation for listing {}", listing.id)
    await db.execute(delete(McpValidationResult).where(McpValidationResult.listing_id == listing.id))
    await db.commit()

    tmp_dir = tempfile.mkdtemp(prefix="observal_")
    try:
        # Stage 1: Clone & Inspect
        entry_point = await _clone_and_inspect(listing, db, tmp_dir)
        if not entry_point:
            return

        # Stage 2: Manifest Validation
        await _manifest_validation(listing, db, entry_point, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _clone_and_inspect(listing: McpListing, db: AsyncSession, tmp_dir: str) -> Path | None:
    url_err = _validate_git_url(listing.git_url)
    if url_err:
        db.add(McpValidationResult(listing_id=listing.id, stage="clone_and_inspect", passed=False, details=url_err))
        await db.commit()
        return None
    url_warning = _git_url_warning(listing.git_url)
    clone_url = _build_clone_url(listing.git_url)
    try:
        await _async_clone(clone_url, tmp_dir)
    except TimeoutError:
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="clone_and_inspect",
                passed=False,
                details=f"Clone timed out after {CLONE_TIMEOUT}s. For slow repos, increase GIT_CLONE_TIMEOUT.",
            )
        )
        await db.commit()
        return None
    except Exception as e:
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="clone_and_inspect",
                passed=False,
                details=f"Failed to clone repo: {_redact_clone_error(e)}",
            )
        )
        await db.commit()
        return None

    # Try Python files first
    entry_point = find_python_entry(tmp_dir)

    if entry_point:
        listing.mcp_validated = True
        listing.framework = "python-mcp"
        _apply_container_detection(listing, tmp_dir)
        cmd, cmd_args = infer_command_args(listing.framework, listing.docker_image, listing.name)
        if cmd and not listing.command:
            listing.command = cmd
        if cmd_args and not listing.args:
            listing.args = cmd_args
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="clone_and_inspect",
                passed=True,
                details="\n".join(
                    part for part in (f"Found MCP entry point: {entry_point.relative_to(tmp_dir)}", url_warning) if part
                ),
            )
        )
        await db.commit()
        return entry_point

    # Try non-Python MCP frameworks
    non_python_framework = detect_non_python_mcp(tmp_dir)
    if non_python_framework:
        listing.mcp_validated = True
        listing.framework = non_python_framework
        _apply_container_detection(listing, tmp_dir)
        cmd, cmd_args = infer_command_args(listing.framework, listing.docker_image, listing.name)
        if cmd and not listing.command:
            listing.command = cmd
        if cmd_args and not listing.args:
            listing.args = cmd_args
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="clone_and_inspect",
                passed=True,
                details="\n".join(
                    part for part in (f"Found non-Python MCP framework: {non_python_framework}", url_warning) if part
                ),
            )
        )
        await db.commit()
        return None

    # No known framework detected - still mark as validated but note unknown framework
    listing.mcp_validated = True
    _apply_container_detection(listing, tmp_dir)
    cmd, cmd_args = infer_command_args(listing.framework, listing.docker_image, listing.name)
    if cmd and not listing.command:
        listing.command = cmd
    if cmd_args and not listing.args:
        listing.args = cmd_args
    db.add(
        McpValidationResult(
            listing_id=listing.id,
            stage="clone_and_inspect",
            passed=True,
            details="\n".join(
                part
                for part in (
                    "No recognized MCP framework detected. "
                    "Marked as validated with framework: unknown. "
                    "Supported detection: FastMCP, MCP SDK (Python/TypeScript/Go), "
                    "and common MCP patterns.",
                    url_warning,
                )
                if part
            ),
        )
    )
    await db.commit()
    return None


async def _manifest_validation(listing: McpListing, db: AsyncSession, entry_point: Path, tmp_dir: str):
    issues = []
    tools_found = []

    try:
        tree = ast.parse(entry_point.read_text(errors="ignore"))
    except SyntaxError as e:
        db.add(
            McpValidationResult(
                listing_id=listing.id,
                stage="manifest_validation",
                passed=False,
                details=f"Syntax error in entry point: {e}",
            )
        )
        await db.commit()
        return

    # Extract server name from FastMCP() or Server(name=...) constructor
    server_name = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # FastMCP("name") pattern
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "FastMCP"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            server_name = node.args[0].value
            break
        # Server(name="name") pattern
        if isinstance(node.func, ast.Name) and node.func.id == "Server":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    server_name = kw.value.value
                    break
            if server_name:
                break
            # Server("name") positional
            if node.args and isinstance(node.args[0], ast.Constant):
                server_name = node.args[0].value
                break

    # Fallback to repo/directory name
    if not server_name:
        server_name = extract_repo_name(listing.git_url, tmp_dir)

    # Find @mcp.tool / @app.tool / @server.tool decorated functions
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_tool = any(
            (isinstance(d, ast.Attribute) and d.attr == "tool")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool")
            for d in node.decorator_list
        )
        if not is_tool:
            continue

        docstring = ast.get_docstring(node) or ""
        # Check params have type annotations (skip 'self' and 'return')
        untyped = [a.arg for a in node.args.args if a.arg != "self" and a.annotation is None]

        tools_found.append(
            {
                "name": node.name,
                "docstring": docstring[:100],
                "has_types": len(untyped) == 0,
            }
        )

        if len(docstring) < 20:
            issues.append(f"Tool '{node.name}' docstring too short ({len(docstring)} chars, need 20+)")
        if untyped:
            issues.append(f"Tool '{node.name}' has untyped params: {', '.join(untyped)}")

    if len(listing.description) < 100:
        issues.append(f"Server description too short ({len(listing.description)} chars, need 100+)")

    if not tools_found:
        issues.append("No @tool decorated functions found")

    passed = len(issues) == 0
    details = f"Server: {server_name}, Tools: {len(tools_found)}"
    if issues:
        details += "\nIssues:\n- " + "\n- ".join(issues)

    if not passed:
        listing.mcp_validated = False

    db.add(
        McpValidationResult(
            listing_id=listing.id,
            stage="manifest_validation",
            passed=passed,
            details=details,
        )
    )
    await db.commit()


async def analyze_repo(git_url: str) -> dict:
    """Clone and analyze a repo without creating a listing. Returns extracted metadata."""
    _empty = {"name": "", "description": "", "version": "0.1.0", "tools": []}
    url_err = _validate_git_url(git_url)
    if url_err:
        return {**_empty, "error": url_err}

    clone_url = _build_clone_url(git_url)

    tmp_dir = tempfile.mkdtemp(prefix="observal_analyze_")
    try:
        try:
            await _async_clone(clone_url, tmp_dir)
        except TimeoutError:
            return {
                **_empty,
                "error": f"Clone timed out after {CLONE_TIMEOUT}s. For slow repos, increase GIT_CLONE_TIMEOUT.",
            }
        except Exception as e:
            err_msg = str(e).lower()
            auth_hints = ("authentication", "403", "404", "could not read username", "terminal prompts disabled")
            if any(h in err_msg for h in auth_hints):
                return {
                    **_empty,
                    "error": "Repository is private or not accessible. Configure GIT_CLONE_TOKEN for private repos.",
                }
            if "not found" in err_msg or "does not exist" in err_msg:
                return {**_empty, "error": "Repository not found. Check the URL."}
            return {**_empty, "error": "Failed to clone repository. Check the URL and try again."}

        return analyze_checkout(tmp_dir, git_url)
    except Exception:
        return {"name": "", "description": "", "version": "0.1.0", "tools": []}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def analyze_checkout(tmp_dir: str, git_url: str) -> dict:
    """Extract MCP metadata from a repository that is already checked out at ``tmp_dir``."""
    entry_point = find_python_entry(tmp_dir)
    env_vars = detect_env_vars(tmp_dir)

    if not entry_point:
        # Try non-Python detection; return repo name as fallback
        non_python = detect_non_python_mcp(tmp_dir)
        name = extract_repo_name(git_url, tmp_dir)
        docker_image, docker_suggested, setup_commands = detect_container_image(Path(tmp_dir), git_url, name)
        cmd, cmd_args = infer_command_args(non_python, docker_image, name)
        base: dict = {
            "name": name,
            "description": "",
            "version": "0.1.0",
            "tools": [],
            "environment_variables": env_vars,
        }
        if non_python:
            base["framework"] = non_python
        if docker_image:
            base["docker_image"] = docker_image
            base["docker_image_suggested"] = docker_suggested
        if setup_commands:
            base["setup_instructions"] = "\n".join(setup_commands)
        if cmd:
            base["command"] = cmd
            base["args"] = cmd_args
        return base

    tree = ast.parse(entry_point.read_text(errors="ignore"))
    server_name, server_desc, tools, issues = analyze_python_entry(tree, git_url, tmp_dir)
    relative_entry = str(entry_point.relative_to(tmp_dir))
    docker_image, docker_suggested, setup_commands = detect_container_image(Path(tmp_dir), git_url, server_name)
    cmd, cmd_args = infer_command_args("python", docker_image, server_name, relative_entry)
    result: dict = {
        "name": server_name,
        "description": server_desc,
        "version": "0.1.0",
        "tools": tools,
        "issues": issues,
        "environment_variables": env_vars,
    }
    if docker_image:
        result["docker_image"] = docker_image
        result["docker_image_suggested"] = docker_suggested
    if setup_commands:
        result["setup_instructions"] = "\n".join(setup_commands)
    if cmd:
        result["command"] = cmd
        result["args"] = cmd_args
    return result
