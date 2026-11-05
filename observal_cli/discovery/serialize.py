# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Privacy-safe, deterministic local harness inventory output."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from observal_cli.discovery.models import DiscoveryDiagnostic, DiscoveryEvidence
from observal_cli.harness import DiscoveredAgent, DiscoveredHook, DiscoveredMcp, DiscoveredSkill


def _is_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def privacy_safe_path(
    path: str | os.PathLike[str] | None,
    *,
    home: Path | None = None,
    project_dir: Path | None = None,
) -> str | None:
    """Return a stable display path without exposing an absolute parent path.

    Project paths take precedence over home paths because projects commonly
    live below the user's home. External paths expose their basename only.
    Existing safe display labels and relative paths are preserved.
    """

    if path is None:
        return None

    raw = os.fspath(path)
    if not _is_absolute_path(raw):
        return raw.replace("\\", "/")

    # A Windows path cannot be meaningfully resolved on POSIX. It is still
    # privacy-safe when reduced to an external basename marker.
    if PureWindowsPath(raw).is_absolute() and not Path(raw).is_absolute():
        return f"<external>/{PureWindowsPath(raw).name}"

    resolved = Path(raw).expanduser().resolve(strict=False)
    resolved_project = (project_dir or Path.cwd()).expanduser().resolve(strict=False)
    resolved_home = (home or Path.home()).expanduser().resolve(strict=False)

    relative = _relative_to(resolved, resolved_project)
    if relative is not None:
        return "<project>" if relative == Path(".") else f"<project>/{relative.as_posix()}"

    relative = _relative_to(resolved, resolved_home)
    if relative is not None:
        return "~" if relative == Path(".") else f"~/{relative.as_posix()}"

    return f"<external>/{resolved.name}"


def _safe_source(path: str | os.PathLike[str] | None, *, home: Path, project_dir: Path) -> str | None:
    from observal_cli.discovery.redact import redact_text

    display = privacy_safe_path(path, home=home, project_dir=project_dir)
    return redact_text(display) if display is not None else None


def evidence_to_dict(evidence: DiscoveryEvidence, *, home: Path, project_dir: Path) -> dict:
    from observal_cli.discovery.redact import redact_text, sanitize_url

    component = evidence.component
    if isinstance(component, DiscoveredMcp):
        kind = "mcp"
    elif isinstance(component, DiscoveredSkill):
        kind = "skill"
    elif isinstance(component, DiscoveredHook):
        kind = "hook"
    elif isinstance(component, DiscoveredAgent):
        kind = "agent"
    else:
        kind = "unknown"
    launch = evidence.launch
    safe_launch = None
    if launch is not None:
        safe_launch = {
            "kind": str(launch.kind),
            "package": launch.package,
            "version": launch.version,
            "transport": launch.transport,
            "url": sanitize_url(launch.url, remove_all_query=True) if launch.url else None,
            "environment_names": list(launch.environment_names),
            "header_names": list(launch.header_names),
        }
    return {
        "type": kind,
        "name": redact_text(str(getattr(component, "name", ""))),
        "harness": evidence.harness,
        "scope": evidence.scope.value,
        "source": _safe_source(evidence.source_path or evidence.display_path, home=home, project_dir=project_dir),
        "launch": safe_launch,
    }


def inventory_to_dict(
    evidence: list[DiscoveryEvidence], diagnostics: list[DiscoveryDiagnostic], *, home: Path, project_dir: Path
) -> dict:
    from observal_cli.discovery.redact import sanitize_diagnostic_message

    items = [evidence_to_dict(item, home=home, project_dir=project_dir) for item in evidence]
    items.sort(
        key=lambda item: (item["harness"] or "", item["scope"], item["source"] or "", item["type"], item["name"])
    )
    warnings = [
        {
            "code": item.code.value,
            "severity": item.severity.value,
            "provider": item.provider,
            "source": _safe_source(item.source, home=home, project_dir=project_dir),
            "message": sanitize_diagnostic_message(item.message),
        }
        for item in diagnostics
    ]
    warnings.sort(key=lambda item: (item["provider"], item["code"], item["source"] or "", item["message"]))
    return {"inventory_schema_version": 1, "inventory": items, "diagnostics": warnings}
