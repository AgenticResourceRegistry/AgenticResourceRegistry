# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Local harness inventory evidence; no Registry or publication state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

from observal_cli.harness import DiscoveredAgent, DiscoveredHook, DiscoveredMcp, DiscoveredSkill

DiscoveredComponent: TypeAlias = DiscoveredMcp | DiscoveredSkill | DiscoveredHook | DiscoveredAgent


class DiscoveryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class LaunchKind(StrEnum):
    NPM = "npm"
    UV = "uv"
    NODE = "node"
    PYTHON_MODULE = "python_module"
    URL = "url"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DiagnosticCode(StrEnum):
    METADATA_TOO_LARGE = "metadata_too_large"
    METADATA_MALFORMED = "metadata_malformed"
    UNSUPPORTED_LAUNCH = "unsupported_launch"
    PERMISSION_DENIED = "permission_denied"
    PATH_OUTSIDE_ROOT = "path_outside_root"
    SYMLINK_ESCAPE = "symlink_escape"
    RECURSION_LIMIT_REACHED = "recursion_limit_reached"
    ITEM_LIMIT_REACHED = "item_limit_reached"
    ADAPTER_DEADLINE_EXCEEDED = "adapter_deadline_exceeded"
    APPROVED_ROOT_LIMIT_REACHED = "approved_root_limit_reached"
    COLLECTION_FILE_LIMIT_REACHED = "collection_file_limit_reached"
    COLLECTION_ENTRY_LIMIT_REACHED = "collection_entry_limit_reached"
    EVIDENCE_LIMIT_REACHED = "evidence_limit_reached"
    DIAGNOSTIC_LIMIT_REACHED = "diagnostic_limit_reached"


@dataclass(frozen=True)
class SanitizedLaunch:
    kind: LaunchKind | str
    package: str | None = None
    module: str | None = None
    script: str | None = None
    url: str | None = None
    binary: str | None = None
    requirement: str | None = None
    version: str | None = None
    arguments: tuple[str, ...] = ()
    environment_names: tuple[str, ...] = ()
    header_names: tuple[str, ...] = ()
    transport: str | None = None


@dataclass(frozen=True)
class DiscoveryEvidence:
    component: DiscoveredComponent | None
    scope: DiscoveryScope
    harness: str | None = None
    source_path: Path | None = None
    display_path: str | None = None
    launch: SanitizedLaunch | None = None


@dataclass(frozen=True)
class DiscoveryDiagnostic:
    code: DiagnosticCode
    severity: DiagnosticSeverity
    provider: str
    source: str | None
    message: str


@dataclass
class AdapterDiscoveryResult:
    evidence: list[DiscoveryEvidence] = field(default_factory=list)
    diagnostics: list[DiscoveryDiagnostic] = field(default_factory=list)
