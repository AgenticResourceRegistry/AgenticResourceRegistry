# SPDX-FileCopyrightText: 2026 Observal Contributors
# SPDX-License-Identifier: Apache-2.0

"""Native Codex MCP TOML settings: fail-closed project/user reads and scoped writes."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _entry(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get("disabled") is True or entry.get("enabled") is False:
        return False
    command, url = entry.get("command"), entry.get("url")
    if bool(command) == bool(url) or (command is not None and not isinstance(command, str)):
        return False
    if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "http://"))):
        return False
    args = entry.get("args", [])
    if not isinstance(args, list) or any(not isinstance(value, str) for value in args):
        return False
    for key in ("env", "headers"):
        value = entry.get(key, {})
        if not isinstance(value, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
        ):
            return False
    return True


def read_toml_mcp(path: Path, alias: str) -> tuple[str, dict | None]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None
    except (OSError, UnicodeError, ValueError):
        return "unverified", None
    canonical = data.get("mcp_servers")
    legacy = data.get("mcp")
    legacy = legacy.get("servers") if isinstance(legacy, dict) else None
    if (canonical is not None and not isinstance(canonical, dict)) or (
        legacy is not None and not isinstance(legacy, dict)
    ):
        return "unverified", None
    if isinstance(canonical, dict) and alias in canonical and isinstance(legacy, dict) and alias in legacy:
        return "unverified", None
    servers = canonical if isinstance(canonical, dict) and alias in canonical else legacy or {}
    if alias not in servers:
        return "missing", None
    entry = servers[alias]
    return ("verified", entry) if _entry(entry) else ("unverified", None)


def native_mcp_version(binary: str) -> str | None:
    """Version-gate writes without revealing config or credentials to logs."""
    executable = shutil.which(binary)
    if not executable:
        return None
    try:
        proc = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def stdio_install_entry(snippet: object, key: str, alias: str) -> dict:
    """Accept only the one generated stdio entry; no partial HTTP/header installs."""
    if not isinstance(snippet, dict) or set(snippet) != {key}:
        raise ValueError("Expected one generated MCP config block")
    servers = snippet[key]
    if not isinstance(servers, dict) or set(servers) != {alias}:
        raise ValueError("Generated MCP alias is ambiguous")
    entry = servers[alias]
    if not _entry(entry) or set(entry) - {"command", "args", "env"} or "command" not in entry:
        raise ValueError("Only fully specified stdio MCP entries can be tracked")
    return entry


def codex_http_install_entry(snippet: object, alias: str) -> dict:
    """Track only a direct streamable-HTTP URL and literal header values.

    Codex uses `http_headers`, not JSON MCP `headers` or stdio `env`.
    Env-name mapping, bearer auth and URL-embedded secrets need separate
    native controls; never record an unsupported configuration as verified.
    """
    if not isinstance(snippet, dict) or set(snippet) != {"mcp_servers"}:
        raise ValueError("Expected one generated Codex MCP config block")
    servers = snippet["mcp_servers"]
    if not isinstance(servers, dict) or set(servers) != {alias}:
        raise ValueError("Generated Codex MCP alias is ambiguous")
    entry = servers[alias]
    if not isinstance(entry, dict) or set(entry) - {"url", "http_headers"}:
        raise ValueError("Unsupported Codex HTTP MCP options")
    url, headers = entry.get("url"), entry.get("http_headers", {})
    if (
        not isinstance(url, str)
        or not isinstance(headers, dict)
        or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in headers.items())
    ):
        raise ValueError("Invalid Codex HTTP MCP URL or headers")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.port is not None and not 0 < parsed.port <= 65535)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in url)
        ):
            raise ValueError("Codex HTTP URL requires an uncredentialed endpoint")
    except ValueError as exc:
        raise ValueError("Codex HTTP URL requires an uncredentialed endpoint") from exc
    return entry


def _require_regular_target(path: Path) -> None:
    # Refuse redirection through a settings directory or the final config.
    if any(part.is_symlink() for part in (path, path.parent, path.parent.parent)):
        raise ValueError("MCP configuration is a symlink")


@dataclass(frozen=True)
class McpConfigUndo:
    """Restore only our exact write; never overwrite a concurrent edit."""

    path: Path
    before: bytes | None
    after: bytes

    def rollback(self) -> bool:
        from observal_cli.lockfile import _exclusive_lock

        _require_regular_target(self.path)
        with _exclusive_lock(self.path.with_name(self.path.name + ".observal.lock")):
            if not self.path.is_file() or self.path.read_bytes() != self.after:
                return False
            if self.before is None:
                self.path.unlink()
            else:
                _atomic_config(self.path, self.before.decode("utf-8"))
            return True


def _atomic_config(path: Path, content: str) -> None:
    import os
    import tempfile

    _require_regular_target(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install_toml_mcp(path: Path, alias: str, entry: dict) -> McpConfigUndo:
    from observal_cli.lockfile import _exclusive_lock

    _require_regular_target(path)
    with _exclusive_lock(path.with_name(path.name + ".observal.lock")):
        try:
            original = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            original = ""
        except (OSError, UnicodeError) as exc:
            raise ValueError("Existing MCP settings cannot be read") from exc
        try:
            data = tomllib.loads(original)
        except ValueError as exc:
            raise ValueError("Existing MCP settings are malformed") from exc
        canonical = data.get("mcp_servers", {})
        legacy = data.get("mcp", {})
        legacy = legacy.get("servers", {}) if isinstance(legacy, dict) else None
        if not isinstance(canonical, dict) or not isinstance(legacy, dict) or alias in canonical or alias in legacy:
            raise ValueError("MCP settings are malformed or alias already exists")
        # JSON basic strings are valid TOML basic strings; preserve all existing
        # Codex settings/comments by appending one verified table at the end.
        quote = lambda value: json.dumps(value, ensure_ascii=False)  # noqa: E731
        lines = [f"[mcp_servers.{quote(alias)}]"]
        if "url" in entry:
            codex_http_install_entry({"mcp_servers": {alias: entry}}, alias)
            lines.append(f"url = {quote(entry['url'])}")
            if entry.get("http_headers"):
                lines.extend(["", f"[mcp_servers.{quote(alias)}.http_headers]"])
                lines.extend(f"{quote(k)} = {quote(v)}" for k, v in entry["http_headers"].items())
        else:
            lines.append(f"command = {quote(entry['command'])}")
            lines.append("args = [" + ", ".join(quote(arg) for arg in entry.get("args", [])) + "]")
            if entry.get("env"):
                lines.extend(["", f"[mcp_servers.{quote(alias)}.env]"])
                lines.extend(f"{quote(k)} = {quote(v)}" for k, v in entry["env"].items())
        new_content = original.rstrip() + "\n\n" + "\n".join(lines) + "\n"
        try:
            tomllib.loads(new_content)
        except ValueError as exc:
            raise ValueError("Generated MCP configuration is not valid TOML") from exc
        before = path.read_bytes() if path.exists() else None
        _atomic_config(path, new_content)
        return McpConfigUndo(path, before, new_content.encode("utf-8"))
