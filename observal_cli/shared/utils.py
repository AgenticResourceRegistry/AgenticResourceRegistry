# SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared utility functions used across multiple CLI modules.

Single source of truth - helpers that were duplicated across cmd_scan.py,
cmd_doctor.py, cmd_skill.py, and claude_code_hooks_spec.py live here instead.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

# ---------------------------------------------------------------------------
# Name sanitization
# ---------------------------------------------------------------------------

_SAFE_CLI_NAME = re.compile(r"^[a-z0-9_-]+$")


def sanitize_name(name: str) -> str:
    """Normalise an arbitrary string to a safe lowercase slug.

    Lowercases, strips whitespace, replaces unsafe characters with hyphens,
    collapses consecutive hyphens, and strips leading/trailing hyphens.
    Returns "skill" as a last-resort fallback for an all-stripped input.

    Raises TypeError if *name* is not a str.
    """
    if not isinstance(name, str):
        raise TypeError(f"sanitize_name expects str, got {type(name).__name__!r}")
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "skill"


# ---------------------------------------------------------------------------
# JSON / JSONC loading
# ---------------------------------------------------------------------------


def load_jsonc(path: Path) -> dict:
    """Load a JSON file that may contain // line comments (JSONC).

    Raises TypeError if *path* is not a Path.
    """
    if not isinstance(path, Path):
        raise TypeError(f"load_jsonc expects Path, got {type(path).__name__!r}")
    text = path.read_text()
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    return json.loads(stripped)


# ---------------------------------------------------------------------------
# MCP server extraction
# ---------------------------------------------------------------------------


def resolve_wsl_windows_home() -> Path | None:
    """Return the Windows user home as a WSL path, or None if not on WSL.

    Tries cmd.exe first (most reliable), then falls back to scanning /mnt/c/Users/
    for the directory that matches the current Linux username.
    """
    import subprocess

    # Method 1: cmd.exe + wslpath (works when cmd.exe is available)
    try:
        win_user = subprocess.check_output(
            ["cmd.exe", "/c", "echo %USERPROFILE%"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        wsl_path = subprocess.check_output(["wslpath", win_user], text=True, stderr=subprocess.DEVNULL).strip()
        p = Path(wsl_path)
        if p.is_dir():
            return p
    except Exception:
        pass

    # Method 2: scan /mnt/c/Users/ for a directory matching the Linux username
    try:
        import os

        linux_user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        mnt_users = Path("/mnt/c/Users")
        if mnt_users.is_dir():
            # Prefer exact username match, then case-insensitive match
            for candidate in mnt_users.iterdir():
                if candidate.is_dir() and candidate.name.lower() == linux_user.lower():
                    return candidate
            # Last resort: any non-system directory
            for candidate in mnt_users.iterdir():
                if candidate.is_dir() and candidate.name not in ("Public", "Default", "All Users", "Default User"):
                    return candidate
    except Exception:
        pass

    return None


def resolve_antigravity_dir(home: Path | None = None) -> Path | None:
    """Return the antigravity-cli config dir, with WSL Windows path fallback."""
    h = home or Path.home()
    ag_dir = h / ".gemini" / "antigravity-cli"
    if ag_dir.exists():
        return ag_dir
    win_home = resolve_wsl_windows_home()
    if win_home:
        candidate = win_home / ".gemini" / "antigravity-cli"
        if candidate.exists():
            return candidate
    return None


def resolve_antigravity_config_dir(home: Path | None = None) -> Path | None:
    """Return ~/.gemini/config/ with WSL Windows path fallback.

    This is where agy stores hooks.json and mcp_config.json (global scope).
    """
    h = home or Path.home()
    config_dir = h / ".gemini" / "config"
    if config_dir.exists():
        return config_dir
    win_home = resolve_wsl_windows_home()
    if win_home:
        candidate = win_home / ".gemini" / "config"
        if candidate.exists():
            return candidate
    return None


def resolve_goose_config_dir(home: Path | None = None) -> Path:
    """Return Goose's configuration directory (``config.yaml`` lives here)."""
    root = _goose_path_root()
    if root is not None:
        return root / "config"
    return _goose_dir(home, "config", "XDG_CONFIG_HOME", (".config",))


def resolve_goose_data_dir(home: Path | None = None) -> Path:
    """Return Goose's data directory (``sessions/sessions.db`` lives here)."""
    root = _goose_path_root()
    if root is not None:
        return root / "data"
    return _goose_dir(home, "data", "XDG_DATA_HOME", (".local", "share"))


def resolve_goose_agents_home(home: Path | None = None) -> Path:
    """Return the ``.agents`` root Goose discovers skills, agents, and plugins from."""
    root = _goose_path_root()
    if root is not None:
        return root / ".agents"
    return (home or Path.home()) / ".agents"


def _goose_path_root() -> Path | None:
    """Return ``$GOOSE_PATH_ROOT`` when set to an absolute path, as Goose requires."""
    import os

    raw = os.environ.get("GOOSE_PATH_ROOT", "")
    root = Path(raw) if raw else None
    return root if root is not None and root.is_absolute() else None


def _goose_dir(home: Path | None, windows_leaf: str, xdg_var: str, unix_parts: tuple[str, ...]) -> Path:
    """Resolve one Goose directory the way ``etcetera``'s app strategy does.

    Unix follows XDG (``~/.config/goose``, ``~/.local/share/goose``); Windows
    uses ``%APPDATA%\\Block\\goose\\<leaf>``.  Passing *home* pins the result to
    the Unix layout under that directory so callers stay hermetic.
    """
    if home is not None:
        return home.joinpath(*unix_parts, "goose")
    import os
    import sys

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Block" / "goose" / windows_leaf
    xdg_root = os.environ.get(xdg_var)
    if xdg_root:
        return Path(xdg_root) / "goose"
    return Path.home().joinpath(*unix_parts, "goose")


def extract_mcp_servers(config: dict, harness: str = "") -> dict:
    """Extract the MCP server map from an harness config dict.

    Resolves harness-specific key paths:
    - ``servers``    - VS Code, Copilot
    - ``mcp``        - OpenCode (flat server map under ``mcp`` key)
    - ``mcp.servers``- Codex (nested under ``mcp`` → ``servers``)
    - ``mcpServers`` - all other harnesses (default)

    Falls back to scanning for server-shaped top-level values when the
    expected key is absent.

    Raises TypeError if *config* is not a dict.
    """
    if not isinstance(config, dict):
        raise TypeError(f"extract_mcp_servers expects dict, got {type(config).__name__!r}")

    if not harness:
        if isinstance(config.get("mcpServers"), dict):
            return config["mcpServers"]
        return {
            name: entry
            for name, entry in config.items()
            if isinstance(entry, dict) and any(field in entry for field in ("command", "url", "type"))
        }

    from observal_cli.harness import ensure_loaded, get_adapter

    ensure_loaded()
    return get_adapter(harness).extract_mcp_servers(config)


# ---------------------------------------------------------------------------
# Hook marker detection
# ---------------------------------------------------------------------------

# Metadata key injected into every Observal-managed matcher group.
OBSERVAL_METADATA_KEY = "_observal"

# Comprehensive set of substrings that identify any Observal-injected hook.
# Used for both scan detection and idempotent cleanup.
_OBSERVAL_HOOK_MARKERS = (
    # Legacy marker names
    "observal-hook",
    "observal-stop-hook",
    # Current session push modules
    "observal_cli.hooks.session_push",
    "observal_cli.hooks.kiro_session_push",
    "observal_cli.hooks.cursor_session_push",
    "observal_cli.hooks.antigravity_session_push",
    # Legacy hook modules
    "observal_cli.hooks.kiro_hook",
    "observal_cli.hooks.kiro_stop_hook",
    "observal_cli.hooks.copilot_cli_hook",
    "observal_cli.hooks.copilot_cli_stop_hook",
    "observal_cli.hooks.buffer_event",
    "observal_cli.hooks.flush_buffer",
    # Catch-all for any observal_cli hook
    "observal_cli",
    # API endpoints
    "/api/v1/telemetry/hooks",
    # Legacy endpoint (removed, still detected for cleanup)
    "/api/v1/otel/hooks",
)


def is_observal_hook_entry(entry: dict) -> bool:
    """Return True if a single hook handler dict belongs to Observal.

    Raises TypeError if *entry* is not a dict.
    """
    if not isinstance(entry, dict):
        raise TypeError(f"is_observal_hook_entry expects dict, got {type(entry).__name__!r}")
    cmd = entry.get("command", "")
    url = entry.get("url", "")
    return any(m in cmd or m in url for m in _OBSERVAL_HOOK_MARKERS)


def is_observal_matcher_group(group: dict) -> bool:
    """Return True if a matcher group is Observal-managed.

    Raises TypeError if *group* is not a dict.
    """
    if not isinstance(group, dict):
        raise TypeError(f"is_observal_matcher_group expects dict, got {type(group).__name__!r}")
    if OBSERVAL_METADATA_KEY in group:
        return True
    return any(is_observal_hook_entry(h) for h in group.get("hooks", []))


# ---------------------------------------------------------------------------
# Hook interpreter
# ---------------------------------------------------------------------------

# Parent of the observal_cli package directory
_PKG_ROOT = str(Path(__file__).resolve().parent.parent.parent)

# Characters cmd.exe treats as operators or token separators outside double
# quotes. list2cmdline only quotes whitespace, so C:\R&D\python.exe would be
# cut at the "&" and run as two commands.
_CMD_NEEDS_QUOTES = re.compile(r'[\s&|<>^(),;="]')


def quote_shell_arg(arg: str) -> str:
    """Quote one argument for the shell a hook command runs under.

    An unquoted path with a space is split into several arguments, and on
    Windows the CLI usually lives under a user directory that may have one.
    cmd.exe ignores single quotes, so Windows gets double quotes, which make
    every character above literal. Both leave a path that needs no quoting
    untouched, so existing hooks do not change.
    """
    if sys.platform == "win32":
        return f'"{arg}"' if not arg or _CMD_NEEDS_QUOTES.search(arg) else arg
    return shlex.quote(arg)


def hook_python_cmd() -> str:
    """Return this CLI's interpreter for a hook command, with PYTHONPATH if needed.

    Hooks run outside the CLI's environment, so bare ``python3`` may not be
    able to import observal_cli. The package root can contain a space just
    like the interpreter, so it is quoted too. ``set "NAME=value"`` already
    keeps the whole assignment as one token on Windows.
    """
    interpreter = quote_shell_arg(sys.executable)
    try:
        import importlib.util

        if importlib.util.find_spec("observal_cli") is not None:
            return interpreter
    except Exception:
        pass
    if sys.platform == "win32":
        return f'set "PYTHONPATH={_PKG_ROOT}" && {interpreter}'
    return f"PYTHONPATH={shlex.quote(_PKG_ROOT)} {interpreter}"


# ---------------------------------------------------------------------------
# Frontmatter / markdown helpers (used by scanning adapters)
# ---------------------------------------------------------------------------


def parse_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a field from YAML frontmatter (--- delimited)."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.startswith(f"{field}:"):
            val = line[len(field) + 1 :].strip().strip('"').strip("'")
            return val
    return None


def extract_body(content: str) -> str:
    """Extract everything after YAML frontmatter."""
    match = re.match(r"^---\s*\n.*?\n---\s*\n?", content, re.DOTALL)
    if match:
        return content[match.end() :]
    return content


def first_content_line(content: str) -> str:
    """Get first non-empty, non-heading content line after frontmatter."""
    in_frontmatter = False
    past_frontmatter = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                past_frontmatter = True
                continue
        if not past_frontmatter and in_frontmatter:
            continue
        if past_frontmatter and stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


def atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` via a temporary file in the same directory.

    The rename is atomic, so a reader never sees a half-written file, and an
    existing file's mode is preserved. On failure the temporary file is removed
    and the original is left untouched.
    """
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = path.stat().st_mode if path.exists() else None
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
