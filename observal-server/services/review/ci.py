# SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
# SPDX-License-Identifier: Apache-2.0
"""Automated review checks that run on the worker after a revision is submitted.

Nothing here executes submitted code. Scripts are parsed, repositories are only
fetched, and every outbound request goes through the SSRF guard and is pinned to
the address that passed it. A check that cannot run reports ``skipped`` or
``error``; it never reports ``pass``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import yaml
from loguru import logger as optic
from sqlalchemy import select

from models.agent_component import AgentComponent
from models.review import Review, ReviewRevision
from services.review import check_config

CHECK_TIMEOUT = 90
STALE_RUNNING = timedelta(minutes=15)
MAX_RESPONSE_BYTES = 1_000_000
# Required checks in these states block publication; warn and skipped never do.
BLOCKING = frozenset({"fail", "error", "queued", "running"})
PENDING = frozenset({"queued", "running"})


class CheckSkippedError(Exception):
    """The check does not apply or cannot be run for a reason that is not a failure."""


@dataclass
class Outcome:
    status: str
    details: list[str]


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Queueing ────────────────────────────────────────────────────────────


def visible(checks: list | None, enabled: bool) -> list:
    """Automated checks are experimental: while the feature is off they are hidden everywhere and never block."""
    return [c for c in (checks or []) if enabled or c.get("kind") != "automated"]


def queued_entries(subject_type: str, config: dict) -> list[dict]:
    """Placeholder entries for every enabled automated check that applies to this subject."""
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "status": "queued",
            "required": config[spec.id]["required"],
            "details": [],
            "kind": "automated",
            "network": spec.network,
        }
        for spec in check_config.SPECS
        if not spec.locked and subject_type in spec.subjects and config[spec.id]["enabled"]
    ]


async def attach_queued(revision, subject, subject_type: str) -> None:
    """Add this revision's automated checks as queued. The caller owns the commit."""
    if not await check_config.enabled():
        revision.checks = visible(revision.checks, False)
        revision.checks_pending = False
        return
    config = await check_config.effective(subject.team_id, is_private=bool(subject.is_private))
    entries = queued_entries(subject_type, config)
    revision.checks = [c for c in (revision.checks or []) if c.get("kind") != "automated"] + entries
    revision.checks_pending = bool(entries)


# ── Context ─────────────────────────────────────────────────────────────


class Ctx:
    def __init__(self, subject_type, subject, version, db):
        self.subject_type, self.subject, self.version, self.db = subject_type, subject, version, db
        self._repo: Path | None = None
        self._tmp: str | None = None

    def source(self) -> tuple[str | None, str | None, str | None]:
        """(url, ref, recorded commit) of the Git source, if the version has one."""
        v = self.version
        if self.subject_type == "skill":
            return (
                (v.git_url or None, v.git_ref or None, None) if v.delivery_mode == "git_fetch" else (None, None, None)
            )
        return (
            getattr(v, "source_url", None) or None,
            getattr(v, "source_ref", None) or None,
            getattr(v, "resolved_sha", None) or None,
        )

    async def repo(self) -> Path:
        """Shallow checkout of the source at its ref, fetched once and shared by every check."""
        if self._repo is not None:
            return self._repo
        url, ref, _ = self.source()
        if not url:
            raise CheckSkippedError("No Git source recorded")
        _validate_git_url(url)
        self._tmp = tempfile.mkdtemp(prefix="observal_ci_")
        for args in (("init", "-q"), ("fetch", "-q", "--depth", "1", "--no-tags", url, ref or "HEAD")):
            code, _, err = await _git(*args, cwd=self._tmp)
            if code:
                raise CheckError(f"git {args[0]} failed: {_redact(err, url)}")
        code, _, err = await _git(
            "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD", cwd=self._tmp
        )
        if code:
            raise CheckError(f"git checkout failed: {_redact(err, url)}")
        self._repo = Path(self._tmp)
        return self._repo

    def cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)


class CheckError(Exception):
    """Infrastructure trouble (network, git, registry): the check could not reach a verdict."""


# ── Subprocess and HTTP helpers ─────────────────────────────────────────


def _redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        text = text.replace(secret, "<url>")
    return text.strip()[:300]


def _validate_git_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise CheckSkippedError("Only http(s) Git URLs are checked")
    if parsed.username or parsed.password:
        raise CheckSkippedError("Git URL embeds credentials and was not contacted")
    from services.ssrf_guard import is_private_url

    if is_private_url(url):
        raise CheckSkippedError("Git URL resolves to a private address and was not contacted")


async def _git(*args: str, cwd: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ALLOW_PROTOCOL": "https:http",
    }
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise CheckError(f"git {args[0]} timed out after {timeout}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def _public_request(
    method: str, url: str, *, json_body=None, headers: dict | None = None, timeout: float = 15
) -> tuple[int, httpx.Headers, bytes]:
    """One request to a public address. Redirects are not followed and the connection is pinned to the checked IP."""
    from services.ssrf_guard import resolve_public_address

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise CheckSkippedError("Not a plain http(s) URL")
    address = await asyncio.to_thread(resolve_public_address, parsed.hostname)
    if address is None:
        raise CheckSkippedError("Resolves to a private or unresolvable address; not contacted")
    host = f"[{address}]" if ":" in address else address
    request_url = urlunparse(parsed._replace(netloc=f"{host}:{parsed.port}" if parsed.port else host))
    request_headers = {**(headers or {}), "Host": parsed.netloc}
    try:
        async with (
            asyncio.timeout(timeout * 2),
            httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client,
            client.stream(
                method,
                request_url,
                headers=request_headers,
                json=json_body,
                extensions={"sni_hostname": parsed.hostname},
            ) as response,
        ):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    break
            return response.status_code, response.headers, bytes(body)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise CheckError(f"Request failed: {type(exc).__name__}") from None


# ── Checks ──────────────────────────────────────────────────────────────


async def check_agent_config(ctx: Ctx) -> Outcome:
    from models.mcp import McpListing
    from services.agent_lock import PinnedListing, pinned_versions
    from services.harness import generate_agent_config
    from services.harness.proxy import AgentVersionConfigProxy
    from services.model_resolver import resolve_model_for_harness

    version = ctx.version
    harnesses = list(version.supported_harnesses or [])
    if not harnesses:
        return Outcome("skipped", ["The release declares no supported harnesses"])
    components = list(
        (await ctx.db.execute(select(AgentComponent).where(AgentComponent.agent_version_id == version.id))).scalars()
    )
    mcp_ids = [c.component_id for c in components if c.component_type == "mcp"]
    listings = {}
    if mcp_ids:
        pinned = await pinned_versions(ctx.db, components)
        rows = (await ctx.db.execute(select(McpListing).where(McpListing.id.in_(mcp_ids)))).scalars().all()
        listings = {r.id: PinnedListing(r, pinned[("mcp", r.id)]) if ("mcp", r.id) in pinned else r for r in rows}
    proxy = AgentVersionConfigProxy(ctx.subject, version, components)
    failures, notes = [], []
    for harness in harnesses:
        try:
            model, warnings = await resolve_model_for_harness(
                harness,
                model_name=version.model_name or "",
                models_by_harness=version.models_by_harness or {},
                override=None,
            )
            generate_agent_config(
                proxy, harness, mcp_listings=listings, options={"_resolved_model": model, "_model_warnings": warnings}
            )
            notes.extend(f"{harness}: {w}" for w in warnings or [])
        except Exception as exc:
            failures.append(f"{harness}: {type(exc).__name__}: {str(exc)[:160]}")
    if failures:
        return Outcome("fail", failures)
    return Outcome("warn" if notes else "pass", notes or [f"Config generated for {', '.join(harnesses)}"])


_RISKY = (
    (re.compile(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"), "downloads and pipes to a shell"),
    (re.compile(r"base64\s+(-d|--decode)[^|\n]*\|\s*(ba|z)?sh\b"), "decodes and pipes to a shell"),
    (re.compile(r"\beval\s+[\"']?\$\(\s*(curl|wget)\b"), "evaluates downloaded code"),
    (
        re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+(/|~|\$HOME)(\s|$|\*)"),
        "recursively deletes / or the home directory",
    ),
    (re.compile(r"\bchmod\s+(-R\s+)?[0-7]?777\b"), "makes files world-writable"),
    (re.compile(r"/dev/tcp/|\bnc\s+(-[a-zA-Z]*e\b|--exec)"), "opens a raw network shell"),
    (re.compile(r">\s*/etc/"), "writes under /etc"),
)


async def _bash_syntax(text: str) -> str | None:
    if not shutil.which("bash"):
        raise CheckError("bash is not installed on the worker")
    proc = await asyncio.create_subprocess_exec(
        "bash", "-n", env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        _, err = await asyncio.wait_for(proc.communicate(text.encode()), 10)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return "syntax check timed out"
    return err.decode(errors="replace").strip()[:200] if proc.returncode else None


def _script_targets(ctx: Ctx) -> list[tuple[str, str]]:
    v = ctx.version
    targets = []
    if getattr(v, "script_content", None):
        targets.append((v.script_filename or "script", v.script_content))
    for resource in getattr(v, "extra_files", None) or []:
        if resource.get("encoding", "utf-8") == "utf-8" and resource.get("content"):
            targets.append((str(resource.get("path", "file")), resource["content"]))
    return targets


async def check_script_lint(ctx: Ctx) -> Outcome:
    targets = _script_targets(ctx)
    handler = getattr(ctx.version, "handler_config", None)
    if handler:
        targets.append(("handler_config", json.dumps(handler)))
    if not targets:
        return Outcome("skipped", ["No bundled scripts or handler command to inspect"])
    errors, risky, parsed = [], [], 0
    for name, text in targets:
        lowered = name.lower()
        try:
            if lowered.endswith((".sh", ".bash")) or text.startswith(
                ("#!/bin/bash", "#!/bin/sh", "#!/usr/bin/env bash")
            ):
                problem = await _bash_syntax(text)
                parsed += 1
                if problem:
                    errors.append(f"{name}: {problem}")
            elif lowered.endswith(".py"):
                parsed += 1
                ast.parse(text, filename=name)
            elif lowered.endswith(".json"):
                parsed += 1
                json.loads(text)
            elif lowered.endswith((".yml", ".yaml")):
                parsed += 1
                yaml.safe_load(text)
        except (SyntaxError, ValueError, yaml.YAMLError, RecursionError, MemoryError) as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
        for pattern, message in _RISKY:
            if pattern.search(text):
                risky.append(f"{name}: {message}")
    if errors:
        return Outcome("fail", errors + risky)
    if risky:
        return Outcome("warn", risky)
    return Outcome("pass", [f"Parsed {parsed} file(s); no risky patterns in {len(targets)} inspected"])


_VAR = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")


async def check_prompt_template(ctx: Ctx) -> Outcome:
    template = ctx.version.template or ""
    if not template.strip():
        return Outcome("fail", ["The template is empty"])
    declared = {str(v.get("name")) for v in (ctx.version.variables or []) if isinstance(v, dict) and v.get("name")}
    used = set(_VAR.findall(template))
    notes = [f"Used but not declared: {n}" for n in sorted(used - declared)]
    notes += [f"Declared but never used: {n}" for n in sorted(declared - used)]
    if len(template) > 100_000:
        notes.append(f"Template is {len(template):,} characters")
    return Outcome("warn" if notes else "pass", notes or [f"{len(used)} variable(s), all declared"])


_KEYED = re.compile(
    r"""(?ix)(api[_-]?key|secret|token|passw(?:or)?d|credential|auth[_-]?key|private[_-]?key)
    ["']?\s*[:=]\s*["']?([A-Za-z0-9+/=_\-]{20,})"""
)
_PLACEHOLDER = re.compile(r"(?i)redacted|example|your[_-]|xxxx|changeme|placeholder|<|\$\{|\{\{")


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


async def check_secrets_entropy(ctx: Ctx) -> Outcome:
    hits = []
    for column in ctx.version.__table__.columns:
        value = getattr(ctx.version, column.name)
        if not isinstance(value, (str, dict, list)):
            continue
        text = value if isinstance(value, str) else json.dumps(value)
        for key, secret in _KEYED.findall(text):
            if not _PLACEHOLDER.search(secret) and _entropy(secret) >= 3.5:
                hits.append(f"{column.name}: value assigned to '{key}' looks like a credential")
    return Outcome("warn", sorted(set(hits))) if hits else Outcome("pass", [])


def _split_image(image: str) -> tuple[str, str, str | None, str | None]:
    """(registry host, repository, tag, digest) with Docker Hub defaults."""
    name, _, digest = image.partition("@")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, name = first, rest
    else:
        registry = "docker.io"
    repo, tag = name, None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        repo, _, tag = name.rpartition(":")
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return registry, repo, tag or (None if digest else "latest"), digest or None


async def check_sandbox_image_pin(ctx: Ctx) -> Outcome:
    _, _, tag, digest = _split_image(ctx.version.image or "")
    if digest:
        return Outcome("pass", [f"Pinned to {digest[:19]}"])
    return Outcome("warn", [f"The image uses the mutable tag '{tag}'; a digest cannot change underneath reviewers"])


_MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


async def check_sandbox_image_resolves(ctx: Ctx) -> Outcome:
    registry, repo, tag, digest = _split_image(ctx.version.image or "")
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    url = f"https://{host}/v2/{repo}/manifests/{digest or tag}"
    headers = {"Accept": _MANIFESTS}
    status, response_headers, _ = await _public_request("GET", url, headers=headers)
    if status == 401:
        match = re.search(r'realm="([^"]+)"(?:,service="([^"]*)")?', response_headers.get("www-authenticate", ""))
        if not match:
            return Outcome("skipped", ["The registry requires authentication; not verified"])
        realm, service = match.groups()
        query = f"?service={service}&scope=repository:{repo}:pull" if service else f"?scope=repository:{repo}:pull"
        token_status, _, body = await _public_request("GET", realm + query)
        token = (
            json.loads(body or b"{}").get("token") or json.loads(body or b"{}").get("access_token")
            if token_status == 200
            else None
        )
        if not token:
            return Outcome("skipped", ["The registry requires authentication; not verified"])
        status, response_headers, _ = await _public_request(
            "GET", url, headers={**headers, "Authorization": f"Bearer {token}"}
        )
    if status == 200:
        return Outcome("pass", [f"Found {response_headers.get('docker-content-digest', 'manifest')[:19]}"])
    if status == 404:
        return Outcome("fail", [f"{ctx.version.image} was not found in {registry}"])
    if status in (401, 403):
        return Outcome(
            "skipped", ["The registry denied anonymous access (the image is private or does not exist); not verified"]
        )
    raise CheckError(f"Registry answered HTTP {status}")


_SHA = re.compile(r"^[0-9a-f]{40}$")


async def check_git_source(ctx: Ctx) -> Outcome:
    url, ref, recorded = ctx.source()
    if not url:
        return Outcome("skipped", ["No Git source recorded"])
    _validate_git_url(url)
    notes = []
    if ref and _SHA.match(ref):
        await ctx.repo()  # Fetching the commit proves it exists.
        head = ref
    else:
        code, out, err = await _git("ls-remote", "--exit-code", url, ref or "HEAD", timeout=45)
        if code == 2:
            return Outcome("fail", [f"Ref '{ref or 'HEAD'}' does not exist in the repository"])
        if code:
            raise CheckError(f"git ls-remote failed: {_redact(err, url)}")
        lines = [line.split("\t") for line in out.splitlines() if line]
        peeled = [sha for sha, name in lines if name.endswith("^{}")]
        head = (peeled or [sha for sha, _ in lines])[0]
    if recorded:
        if not head.startswith(recorded[: len(head)]) and not recorded.startswith(head[: len(recorded)]):
            return Outcome("fail", [f"Ref now points at {head[:12]}, but the release recorded {recorded[:12]}"])
        notes.append(f"Matches recorded commit {recorded[:12]}")
    else:
        notes.append(f"Resolves to {head[:12]}; no commit was recorded, so the ref can still move")
    status = "pass" if recorded else "warn"
    if ctx.subject_type == "skill" and ctx.version.skill_md_content:
        repo = await ctx.repo()
        path = repo / (ctx.version.skill_path or "").lstrip("/") / "SKILL.md"
        if not path.resolve().is_relative_to(repo.resolve()):
            return Outcome("fail", ["skill_path points outside the repository"])
        if not path.is_file():
            return Outcome("fail", [f"No SKILL.md at '{ctx.version.skill_path or '.'}' in the repository"])
        if path.read_text(errors="replace").strip() != ctx.version.skill_md_content.strip():
            notes.append("The stored SKILL.md differs from the repository's at this ref")
            status = "warn"
    return Outcome(status, notes)


def _rpc_payload(body: bytes, content_type: str):
    text = body.decode(errors="replace")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("no data event")
    return json.loads(text)


async def check_remote_mcp(ctx: Ctx) -> Outcome:
    v = ctx.version
    if not v.url or (v.transport or "").lower() == "stdio":
        return Outcome("skipped", ["Not a remote MCP server"])
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "observal-review-check", "version": "1"},
        },
    }
    status, response_headers, body = await _public_request("POST", v.url, json_body=init, headers=headers)
    if status in (401, 403):
        return Outcome("skipped", ["The server requires authentication; its tools were not verified"])
    if status in (404, 405):
        return Outcome("skipped", ["Not a streamable-HTTP endpoint (legacy SSE servers are not probed)"])
    if status != 200:
        raise CheckError(f"Server answered HTTP {status}")
    try:
        reply = _rpc_payload(body, response_headers.get("content-type", ""))
    except ValueError:
        return Outcome("fail", ["The response to initialize was not valid JSON-RPC"])
    if not isinstance(reply, dict) or "result" not in reply:
        return Outcome("fail", ["The server did not return an initialize result"])
    session = response_headers.get("mcp-session-id")
    if session:
        headers["Mcp-Session-Id"] = session
    await _public_request(
        "POST", v.url, json_body={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers
    )
    status, response_headers, body = await _public_request(
        "POST", v.url, json_body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
    )
    try:
        tools = (_rpc_payload(body, response_headers.get("content-type", "")).get("result") or {}).get("tools")
    except (ValueError, AttributeError):
        tools = None
    if status != 200 or not isinstance(tools, list):
        return Outcome("warn", ["The server initialized but did not list tools"])
    live = {t.get("name") for t in tools if isinstance(t, dict)}
    declared = v.tools_schema
    declared_names = (
        {t.get("name") for t in declared.get("tools", []) if isinstance(t, dict)}
        if isinstance(declared, dict) and isinstance(declared.get("tools"), list)
        else set(declared or {})
    )
    missing = sorted(n for n in declared_names - live if n)
    if missing:
        return Outcome("warn", [f"Submitted tools the server does not list: {', '.join(missing[:10])}"])
    return Outcome("pass", [f"Server answered MCP and lists {len(live)} tool(s)"])


def _read_lockfiles(repo: Path) -> list[tuple[str, str, str]]:
    """(ecosystem, name, version) from lockfiles at the repository root and one level down."""
    found: list[tuple[str, str, str]] = []
    for base in [repo, *[p for p in repo.iterdir() if p.is_dir() and not p.name.startswith(".")][:20]]:
        try:
            if (base / "package-lock.json").is_file():
                data = json.loads((base / "package-lock.json").read_text())
                for key, meta in (data.get("packages") or {}).items():
                    if key and meta.get("version") and "node_modules/" in key:
                        found.append(("npm", key.rsplit("node_modules/", 1)[-1], meta["version"]))
            for name in ("poetry.lock", "uv.lock", "Cargo.lock"):
                if (base / name).is_file():
                    eco = "crates.io" if name == "Cargo.lock" else "PyPI"
                    for pkg in tomllib.loads((base / name).read_text()).get("package", []):
                        if pkg.get("name") and pkg.get("version"):
                            found.append((eco, pkg["name"], pkg["version"]))
            for req in base.glob("requirements*.txt"):
                for line in req.read_text(errors="replace").splitlines():
                    m = re.match(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([A-Za-z0-9_.+!-]+)", line)
                    if m:
                        found.append(("PyPI", m.group(1), m.group(2)))
        except (ValueError, OSError, tomllib.TOMLDecodeError):
            continue
    return sorted(set(found))[:2000]


async def check_dependency_audit(ctx: Ctx) -> Outcome:
    repo = await ctx.repo()
    packages = _read_lockfiles(repo)
    if not packages:
        return Outcome(
            "skipped",
            ["No supported lockfile (package-lock.json, poetry.lock, uv.lock, Cargo.lock, pinned requirements)"],
        )
    vulnerable: list[str] = []
    for start in range(0, len(packages), 500):
        chunk = packages[start : start + 500]
        queries = [{"package": {"name": n, "ecosystem": e}, "version": ver} for e, n, ver in chunk]
        status, _, body = await _public_request(
            "POST", "https://api.osv.dev/v1/querybatch", json_body={"queries": queries}, timeout=30
        )
        if status != 200:
            raise CheckError(f"OSV answered HTTP {status}")
        for (eco, name, ver), result in zip(chunk, json.loads(body).get("results", []), strict=False):
            ids = [v["id"] for v in result.get("vulns", [])]
            if ids:
                vulnerable.append(f"{name} {ver} ({eco}): {', '.join(ids[:3])}")
    if vulnerable:
        return Outcome(
            "fail", [f"{len(vulnerable)} of {len(packages)} locked packages have known advisories", *vulnerable[:10]]
        )
    return Outcome("pass", [f"No known advisories in {len(packages)} locked packages"])


RUNNERS = {
    "agent_config": check_agent_config,
    "script_lint": check_script_lint,
    "prompt_template": check_prompt_template,
    "secrets_entropy": check_secrets_entropy,
    "sandbox_image_pin": check_sandbox_image_pin,
    "sandbox_image_resolves": check_sandbox_image_resolves,
    "git_source": check_git_source,
    "remote_mcp": check_remote_mcp,
    "dependency_audit": check_dependency_audit,
}


async def _run_one(check_id: str, ctx: Ctx) -> Outcome:
    try:
        return await asyncio.wait_for(RUNNERS[check_id](ctx), CHECK_TIMEOUT)
    except CheckSkippedError as exc:
        return Outcome("skipped", [str(exc)])
    except CheckError as exc:
        return Outcome("error", [str(exc)])
    except TimeoutError:
        return Outcome("error", [f"Timed out after {CHECK_TIMEOUT}s"])
    except Exception as exc:
        optic.opt(exception=True).error("review check {} crashed", check_id)
        return Outcome("error", [f"The check crashed ({type(exc).__name__}); see the server log"])


# ── Execution ───────────────────────────────────────────────────────────


def _session():
    from database import async_session

    return async_session()


async def _claim(batch: int) -> list[tuple]:
    """Mark queued checks as running (and expire abandoned ones) so concurrent pollers never share work."""

    claims = []
    cutoff = (datetime.now(UTC) - STALE_RUNNING).isoformat()
    async with _session() as db:
        revisions = (
            await db.scalars(
                select(ReviewRevision)
                .where(ReviewRevision.checks_pending.is_(True))
                .order_by(ReviewRevision.created_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for revision in revisions:
            ids, updated = [], []
            for check in revision.checks:
                if check.get("status") == "queued":
                    ids.append(check["id"])
                    check = {**check, "status": "running", "started_at": _now()}
                elif check.get("status") == "running" and check.get("started_at", "") < cutoff:
                    check = {**check, "status": "error", "details": ["The worker stopped before finishing; rerun"]}
                updated.append(check)
            revision.checks = updated
            revision.checks_pending = any(c.get("status") in PENDING for c in updated)
            if ids:
                claims.append((revision.id, ids))
        await db.commit()
    return claims


async def _finish(revision_id, results: dict[str, Outcome]) -> None:
    from services.review.decisions import _lock_review, gate, sync_state
    from services.review.notifications import deliver_gate_change

    async with _session() as db:
        revision = await db.get(ReviewRevision, revision_id)
        review = await db.get(Review, revision.review_id)
        await _lock_review(db, review)
        is_head = review.head_revision_id == revision.id
        was_ready = (await gate(db, review)).ready if is_head else False
        await db.refresh(revision, with_for_update=True)
        merged = []
        for check in revision.checks:
            outcome = results.get(check["id"])
            if outcome and check.get("status") == "running":
                check = {**check, "status": outcome.status, "details": outcome.details, "finished_at": _now()}
            merged.append(check)
        revision.checks = merged
        revision.checks_pending = any(c.get("status") in PENDING for c in merged)
        await db.flush()
        if is_head:
            await sync_state(db, review)
            await deliver_gate_change(db, review, was_ready, None)
        await db.commit()


async def _run_revision(revision_id, check_ids: list[str]) -> None:
    from services.review.decisions import _hash, _target
    from services.review.files import render_files

    results: dict[str, Outcome] = {}
    async with _session() as db:
        revision = await db.get(ReviewRevision, revision_id)
        review = await db.get(Review, revision.review_id)
        stale = None
        if review.head_revision_id != revision.id:
            stale = Outcome("skipped", ["Superseded by a newer revision"])
        else:
            subject, version = await _target(db, review)
            if _hash(await render_files(review.subject_type, version, db)) != revision.content_hash:
                stale = Outcome("error", ["The content changed after this revision was submitted; resubmit"])
        if stale:
            results = dict.fromkeys(check_ids, stale)
        else:
            ctx = Ctx(review.subject_type, subject, version, db)
            try:
                for check_id in check_ids:
                    results[check_id] = await _run_one(check_id, ctx)
            finally:
                ctx.cleanup()
    await _finish(revision_id, results)


async def run_pending(batch: int = 5) -> int:
    """Run every queued check once. Returns the number of revisions processed."""
    if not await check_config.enabled():
        return 0
    claims = await _claim(batch)
    for revision_id, ids in claims:
        try:
            await _run_revision(revision_id, ids)
        except Exception:
            optic.opt(exception=True).error("review checks failed for revision {}", revision_id)
    return len(claims)


async def requeue(db, review, actor_id) -> int:
    """Reset the head revision's automated checks to queued under the current configuration."""
    from services.review.decisions import _event, _head, _target, sync_state

    revision = await _head(db, review)
    subject, _ = await _target(db, review)
    await attach_queued(revision, subject, review.subject_type)
    _event(db, review, "checks_rerun", actor_id)
    await db.flush()
    await sync_state(db, review, actor_id=actor_id)
    return sum(c.get("status") == "queued" for c in revision.checks)
