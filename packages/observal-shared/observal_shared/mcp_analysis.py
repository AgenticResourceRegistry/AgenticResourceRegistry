# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared MCP repository analysis helpers."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

PYTHON_MCP_PATTERN = re.compile(
    r"FastMCP\("
    r"|@mcp\.server"
    r"|from\s+mcp\.server\s+import\s+Server"
    r"|from\s+mcp\s+import"
    r"|import\s+mcp\b"
    r"|McpServer\("
    r"|MCPServer\("
    r"|@app\.tool\b"
    r"|@server\.tool\b"
    r"|Server\(\s*name\s*="
)

_ENV_VAR_PATTERN_PYTHON = re.compile(
    r"""os\.environ\s*(?:\.get\s*\(\s*|\.?\[?\s*\[?\s*)["']([A-Z][A-Z0-9_]+)["']"""
    r"""|os\.getenv\s*\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_ENV_VAR_PATTERN_GO = re.compile(r"""os\.Getenv\(\s*"([A-Z][A-Z0-9_]+)"\s*\)""")
_ENV_VAR_PATTERN_TS = re.compile(
    r"""process\.env\.([A-Z][A-Z0-9_]+)"""
    r"""|process\.env\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]"""
)
_README_PATTERNS = [
    re.compile(r"""-e\s+([A-Z][A-Z0-9_]+)"""),
    re.compile(r"""export\s+([A-Z][A-Z0-9_]+)="""),
    re.compile(r'"([A-Z][A-Z0-9_]+)"\s*:\s*"'),
]
_INTERNAL_ENV_VARS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "LANG",
        "TERM",
        "PWD",
        "TMPDIR",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUSERBASE",
        "PYTHONHOME",
        "PYTHONUNBUFFERED",
        "VIRTUAL_ENV",
        "NODE_ENV",
        "NODE_PATH",
        "NODE_OPTIONS",
        "PORT",
        "HOST",
        "DEBUG",
        "APP",
        "LOG_LEVEL",
        "LOGGING_LEVEL",
        "HOSTNAME",
        "DISPLAY",
        "EDITOR",
        "PAGER",
        "TZ",
        "LC_ALL",
        "LC_CTYPE",
    }
)
_ALLOWED_ENV_VARS = frozenset({"GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN", "DOCKER_HOST"})
_FILTERED_PREFIXES = (
    "CI_",
    "GITHUB_",
    "GITLAB_",
    "CIRCLECI_",
    "TRAVIS_",
    "JENKINS_",
    "BUILDKITE_",
    "DOCKER_",
    "BUILDKIT_",
    "COMPOSE_",
    "NPM_",
    "PIP_",
    "UV_",
    "MCP_LOG_",
)
_SKIP_DIRS = frozenset(
    {"test", "tests", "e2e", "internal", "testdata", "vendor", "node_modules", "__pycache__", ".git"}
)
_DOCKER_IMAGE_PATTERN = re.compile(
    r"((?:ghcr\.io|docker\.io|registry\.[a-z0-9.-]+\.[a-z]{2,}|[a-z0-9.-]+\.azurecr\.io|[a-z0-9.-]+\.gcr\.io)"
    r"/[a-z0-9_./-]+"
    r"(?::[a-z0-9._-]+)?)"
)


def is_filtered_env_var(name: str) -> bool:
    if name in _ALLOWED_ENV_VARS:
        return False
    if name in _INTERNAL_ENV_VARS:
        return True
    return any(name.startswith(prefix) for prefix in _FILTERED_PREFIXES)


def is_test_file(path: Path) -> bool:
    if any(part in _SKIP_DIRS for part in path.parts):
        return True
    name = path.name
    return name.endswith("_test.go") or name.startswith("test_") or name.endswith("_test.py")


def _scan_files_for_env_vars(root: Path, glob: str, pattern: re.Pattern[str], found: dict[str, str]) -> None:
    for path in root.rglob(glob):
        if is_test_file(path.relative_to(root)):
            continue
        try:
            content = path.read_text(errors="ignore")
            for match in pattern.finditer(content):
                name = next((group for group in match.groups() if group), None)
                if name and not is_filtered_env_var(name):
                    found.setdefault(name, "")
        except Exception:
            continue


def _scan_readme_for_env_vars(root: Path, found: dict[str, str]) -> None:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = root / name
        if not readme.exists():
            continue
        try:
            content = readme.read_text(errors="ignore")
        except Exception:
            continue
        for pattern in _README_PATTERNS:
            for match in pattern.finditer(content):
                var = match.group(1)
                if var and not is_filtered_env_var(var):
                    found.setdefault(var, "")
        break


def _extract_manifest_env_vars(root: Path, found: dict[str, str]) -> bool:
    manifest = root / "server.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(errors="ignore"))
    except Exception:
        return False
    for package in data.get("packages", []):
        for arg in package.get("runtimeArguments", []):
            value = arg.get("value", "")
            if "=" in value:
                var_name = value.split("=", 1)[0]
                if var_name and var_name == var_name.upper():
                    found.setdefault(var_name, arg.get("description", ""))
    for remote in data.get("remotes", []):
        for var_key, var_meta in (remote.get("variables") or {}).items():
            desc = var_meta.get("description", "") if isinstance(var_meta, dict) else ""
            found.setdefault(var_key, desc)
    return True


def _scan_env_example(root: Path, found: dict[str, str]) -> None:
    for env_file in root.glob(".env*"):
        if env_file.name in (".env", ".env.local"):
            continue
        try:
            for line in env_file.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key = line.split("=", 1)[0].strip()
                if key and key == key.upper() and not is_filtered_env_var(key):
                    found.setdefault(key, "")
        except Exception:
            continue


def detect_env_vars(tmp_dir: str) -> list[dict]:
    root = Path(tmp_dir)
    found: dict[str, str] = {}
    if _extract_manifest_env_vars(root, found):
        return [{"name": key, "description": value, "required": True} for key, value in sorted(found.items())]
    _scan_readme_for_env_vars(root, found)
    if found:
        return [{"name": key, "description": value, "required": True} for key, value in sorted(found.items())]
    _scan_env_example(root, found)
    if found:
        return [{"name": key, "description": value, "required": True} for key, value in sorted(found.items())]
    _scan_files_for_env_vars(root, "*.py", _ENV_VAR_PATTERN_PYTHON, found)
    _scan_files_for_env_vars(root, "*.go", _ENV_VAR_PATTERN_GO, found)
    for ext in ("*.ts", "*.js", "*.mts", "*.mjs"):
        _scan_files_for_env_vars(root, ext, _ENV_VAR_PATTERN_TS, found)
    return [{"name": key, "description": value, "required": True} for key, value in sorted(found.items())]


def _safe_image_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip(".-")
    return name or "mcp-server"


def _github_image(git_url: str) -> tuple[str | None, bool]:
    safe_name = re.compile(r"^[a-zA-Z0-9._-]+$")
    try:
        parts = urlparse(git_url)
        if parts.hostname != "github.com":
            return (None, False)
        path = parts.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        owner_repo = path.split("/")
        if len(owner_repo) >= 2 and safe_name.match(owner_repo[0]) and safe_name.match(owner_repo[1]):
            return (f"ghcr.io/{owner_repo[0]}/{owner_repo[1]}", True)
    except Exception:
        pass
    return (None, False)


def _repo_image_name(git_url: str, fallback: str | None = None) -> str:
    try:
        path = urlparse(git_url).path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if path:
            return _safe_image_name(path.replace("/", "-"))
    except Exception:
        pass
    return _safe_image_name(fallback or "mcp-server")


def _docker_build_command(tag: str, context: str = ".", dockerfile: str | None = None) -> str:
    context = context or "."
    parts = ["docker", "build", "-t", tag]
    if dockerfile:
        dockerfile_path = str(PurePosixPath(context) / dockerfile) if not dockerfile.startswith("/") else dockerfile
        parts.extend(["-f", dockerfile_path])
    parts.append(context)
    return " ".join(parts)


def detect_container_image(root: Path, git_url: str, name: str | None = None) -> tuple[str | None, bool, list[str]]:
    """Detect a runnable OCI image and any local setup commands.

    ``suggested`` is true when the image tag is inferred and may need building.
    """
    repo_name = _repo_image_name(git_url, name)
    for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        compose_file = root / compose_name
        if not compose_file.exists():
            continue
        try:
            import yaml

            data = yaml.safe_load(compose_file.read_text(errors="ignore")) or {}
            services = data.get("services") or {}
            for service in services.values():
                image = service.get("image") if isinstance(service, dict) else None
                if image and isinstance(image, str):
                    return (image, False, [])
            for service_name, service in services.items():
                if not isinstance(service, dict) or not service.get("build"):
                    continue
                build = service["build"]
                context = build if isinstance(build, str) else build.get("context", ".")
                dockerfile = None if isinstance(build, str) else build.get("dockerfile")
                tag = service.get("image") or f"{repo_name}-{_safe_image_name(str(service_name))}:latest"
                return (tag, True, [_docker_build_command(tag, str(context or "."), dockerfile)])
        except Exception:
            pass
    for readme_name in ("README.md", "README.rst", "README.txt", "README"):
        readme = root / readme_name
        if not readme.exists():
            continue
        try:
            match = _DOCKER_IMAGE_PATTERN.search(readme.read_text(errors="ignore"))
            if match:
                return (match.group(1), False, [])
        except Exception:
            pass
        break
    for dockerfile in ("Dockerfile", "Containerfile"):
        if (root / dockerfile).exists():
            tag = f"{repo_name}:latest"
            return (tag, True, [_docker_build_command(tag, ".", dockerfile)])
    image, suggested = _github_image(git_url)
    if image:
        return (image, suggested, [])
    return (None, False, [])


def detect_docker_image(root: Path, git_url: str) -> tuple[str | None, bool]:
    image, suggested, setup = detect_container_image(root, git_url)
    if setup:
        return _github_image(git_url)
    return (image, suggested)


# `docker run` / `docker container run` options that take a separate value argument.
_DOCKER_RUN_VALUE_FLAGS = frozenset(
    {
        "-a", "--attach", "--add-host", "--annotation", "--blkio-weight", "--blkio-weight-device",
        "--cap-add", "--cap-drop", "--cgroup-parent", "--cgroupns", "--cidfile", "--cpu-period", "--cpu-quota",
        "--cpu-rt-period", "--cpu-rt-runtime", "-c", "--cpu-shares", "--cpus", "--cpuset-cpus", "--cpuset-mems",
        "--detach-keys", "--device", "--device-cgroup-rule", "--device-read-bps", "--device-read-iops",
        "--device-write-bps", "--device-write-iops", "--dns", "--dns-option", "--dns-search", "--domainname",
        "--entrypoint", "-e", "--env", "--env-file", "--expose", "--gpus", "--group-add", "--health-cmd",
        "--health-interval", "--health-retries", "--health-start-interval", "--health-start-period",
        "--health-timeout", "-h", "--hostname", "--ip", "--ip6", "--ipc", "--isolation", "--kernel-memory",
        "-l", "--label", "--label-file", "--link", "--link-local-ip", "--log-driver", "--log-opt",
        "--mac-address", "-m", "--memory", "--memory-reservation", "--memory-swap", "--memory-swappiness",
        "--mount", "--name", "--network", "--net", "--network-alias", "--net-alias", "--oom-score-adj", "--pid",
        "--pids-limit", "--platform", "-p", "--publish", "--pull", "--restart", "--runtime", "--security-opt",
        "--shm-size", "--stop-signal", "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs", "--ulimit",
        "-u", "--user", "--userns", "--uts", "-v", "--volume", "--volume-driver", "--volumes-from", "-w",
        "--workdir",
    }
)  # fmt: skip
_DOCKER_SHORT_VALUE_FLAGS = frozenset(flag[1] for flag in _DOCKER_RUN_VALUE_FLAGS if len(flag) == 2)


def docker_image_from_args(args: list[str]) -> str | None:
    """The image in ``docker run [OPTIONS] IMAGE [COMMAND] [ARG...]`` arguments.

    The image is the first positional argument after the options; anything after it
    is the container's own command (for example ``stdio --read-only``), never the image.
    Configs that omit ``run`` and start with options are accepted. Returns None for other
    subcommands such as ``build``.
    """
    tokens = list(args)
    if tokens[:2] == ["container", "run"]:
        tokens = tokens[2:]
    elif tokens[:1] == ["run"]:
        tokens = tokens[1:]
    elif not tokens or not tokens[0].startswith("-"):
        return None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1] if index + 1 < len(tokens) else None
        if token.startswith("--"):
            # --flag=value carries its value; a bare value flag consumes the next token.
            index += 1 if "=" in token or token not in _DOCKER_RUN_VALUE_FLAGS else 2
            continue
        if f"-{token}" in _DOCKER_RUN_VALUE_FLAGS:
            # A single-dash long flag such as ``-env`` (common in hand-written configs) takes a value.
            index += 2
            continue
        if token.startswith("-") and len(token) > 1:
            # Grouped short flags such as -it; a value flag consumes the rest of the token or the next one.
            consumed_next = False
            for position, letter in enumerate(token[1:], start=1):
                if letter in _DOCKER_SHORT_VALUE_FLAGS:
                    consumed_next = position == len(token) - 1
                    break
            index += 2 if consumed_next else 1
            continue
        return token
    return None


def infer_command_args(
    framework: str | None,
    docker_image: str | None,
    name: str,
    entry_point: str | None = None,
) -> tuple[str | None, list[str] | None]:
    if docker_image:
        return ("docker", ["run", "-i", "--rm", docker_image])
    fw = (framework or "").lower()
    if "typescript" in fw or "ts" in fw:
        return ("npx", ["-y", name])
    if "go" in fw:
        return (name, [])
    if "python" in fw or entry_point:
        return ("python", ["-m", name])
    return (None, None)


def detect_non_python_mcp(tmp_dir: str) -> str | None:
    root = Path(tmp_dir)
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(errors="ignore"))
            deps = {}
            deps.update(data.get("dependencies", {}))
            deps.update(data.get("devDependencies", {}))
            if "@modelcontextprotocol/sdk" in deps:
                return "typescript-mcp-sdk"
        except Exception:
            pass
    for go_file in root.rglob("*.go"):
        try:
            content = go_file.read_text(errors="ignore")
            if "mcp-go" in content or "mcp_go" in content:
                return "go-mcp-sdk"
        except Exception:
            continue
    return None


def extract_repo_name(git_url: str, tmp_dir: str) -> str:
    try:
        path = urlparse(git_url).path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        name = path.rsplit("/", 1)[-1]
        if name:
            return name
    except Exception:
        pass
    return Path(tmp_dir).name or "unknown"


def find_python_entry(tmp_dir: str) -> Path | None:
    for py_file in Path(tmp_dir).rglob("*.py"):
        try:
            if PYTHON_MCP_PATTERN.search(py_file.read_text(errors="ignore")):
                return py_file
        except Exception:
            continue
    return None


def analyze_python_entry(tree: ast.AST, git_url: str, tmp_dir: str) -> tuple[str, str, list[dict], list[str]]:
    server_name = ""
    server_desc = ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "FastMCP":
            if node.args and isinstance(node.args[0], ast.Constant):
                server_name = str(node.args[0].value)
            for kw in node.keywords:
                if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    server_desc = str(kw.value.value)
            if server_name:
                break
        if node.func.id == "Server":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    server_name = str(kw.value.value)
                if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    server_desc = str(kw.value.value)
            if not server_name and node.args and isinstance(node.args[0], ast.Constant):
                server_name = str(node.args[0].value)
            if server_name:
                break
    if not server_name:
        server_name = extract_repo_name(git_url, tmp_dir)

    tools: list[dict] = []
    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_tool = any(
            (isinstance(dec, ast.Attribute) and dec.attr == "tool")
            or (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool")
            for dec in node.decorator_list
        )
        if is_tool:
            docstring = ast.get_docstring(node) or ""
            untyped = [arg.arg for arg in node.args.args if arg.arg != "self" and arg.annotation is None]
            tools.append({"name": node.name, "docstring": docstring})
            if len(docstring) < 20:
                issues.append(f"Tool '{node.name}': docstring too short ({len(docstring)} chars, need 20+)")
            if untyped:
                issues.append(f"Tool '{node.name}': untyped params: {', '.join(untyped)}")
    if not tools:
        issues.append("No @tool decorated functions found")
    return server_name, server_desc, tools, issues
